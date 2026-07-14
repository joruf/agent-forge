"""Parse multi-step workspace requests into ordered execution agendas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentforge.agents.workspace_executor import (
    extract_literal_text_content,
    plan_deliverable_files,
)
from agentforge.agents.workspace_intent import (
    WorkspaceIntent,
    detect_file_edit_intent,
    extract_named_folder,
    extract_text_replacement,
)


class AgendaAction(StrEnum):
    """One executable workspace action in user-request order."""

    CREATE_DIRECTORY = "create_directory"
    WRITE_FILE = "write_file"
    READ_FILE = "read_file"
    EDIT_FILE = "edit_file"


@dataclass
class AgendaStep:
    """Single numbered step in a workspace execution agenda."""

    step_id: int
    action: AgendaAction
    path: str | None
    detail: str
    replace_from: str | None = None
    replace_to: str | None = None


def _primary_directory(intent: WorkspaceIntent) -> str | None:
    """
    Resolve the main workspace directory for this request.

    :param intent: Parsed workspace intent
    :return: Workspace-relative directory or None
    """
    if intent.target_dirs:
        return max(intent.target_dirs, key=len)
    for path in intent.target_paths:
        if "/" in path:
            return path.rsplit("/", 1)[0]
    return None


def _primary_file(user_content: str, intent: WorkspaceIntent) -> str | None:
    """
    Resolve the canonical deliverable file path for this request.

    User paths may omit subfolders (e.g. GitHub/index.html instead of
    GitHub/Test12/index.html). The planned deliverable path wins.

    :param user_content: User message text
    :param intent: Parsed workspace intent
    :return: Workspace-relative file path or None
    """
    planned = plan_deliverable_files(user_content, intent)
    if planned:
        return planned[0]
    file_paths = [path for path in intent.target_paths if "." in path.rsplit("/", 1)[-1]]
    return file_paths[0] if file_paths else None


def build_workspace_agenda(
    user_content: str,
    intent: WorkspaceIntent,
) -> list[AgendaStep]:
    """
    Build an ordered 1..N agenda from the user request before agent execution.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Ordered agenda steps (may be empty for non-workspace tasks)
    """
    text = user_content or ""
    steps: list[AgendaStep] = []
    step_id = 1

    primary_dir = _primary_directory(intent)
    primary_file = _primary_file(user_content, intent)
    named_folder = extract_named_folder(text)
    wants_edit = detect_file_edit_intent(text)
    replacement = extract_text_replacement(text) if wants_edit else None

    if named_folder or intent.wants_directory_creation:
        directory = primary_dir
        if not directory and named_folder:
            directory = named_folder
        if directory:
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.CREATE_DIRECTORY,
                    directory,
                    f"Create directory `{directory}`",
                )
            )
            step_id += 1

    if intent.wants_file_creation and primary_file:
        literal = extract_literal_text_content(text)
        if literal:
            detail = f"Create `{primary_file}` containing text \"{literal}\""
        elif primary_file.endswith((".html", ".htm")):
            detail = f"Create `{primary_file}` with the requested HTML content"
        else:
            detail = f"Create `{primary_file}` with the requested content"
        steps.append(
            AgendaStep(
                step_id,
                AgendaAction.WRITE_FILE,
                primary_file,
                detail,
            )
        )
        step_id += 1

    if intent.wants_file_read and primary_file:
        steps.append(
            AgendaStep(
                step_id,
                AgendaAction.READ_FILE,
                primary_file,
                f"Read `{primary_file}` from disk and show the content in chat",
            )
        )
        step_id += 1

    if wants_edit and primary_file:
        old_text, new_text = replacement or ("", "")
        detail = f"Edit `{primary_file}`"
        if old_text and new_text:
            detail = f'Replace "{old_text}" with "{new_text}" in `{primary_file}`'
        steps.append(
            AgendaStep(
                step_id,
                AgendaAction.EDIT_FILE,
                primary_file,
                detail,
                replace_from=old_text or None,
                replace_to=new_text or None,
            )
        )

    return steps


def format_agenda_block(agenda: list[AgendaStep]) -> str:
    """
    Format the numbered agenda for PM prompts and transcripts.

    :param agenda: Ordered workspace agenda
    :return: Human-readable agenda block
    """
    if not agenda:
        return ""
    lines = ["Execution agenda (complete in this order):"]
    for step in agenda:
        path_part = f" → `{step.path}`" if step.path else ""
        lines.append(f"{step.step_id}. {step.action.value}{path_part} — {step.detail}")
    return "\n".join(lines)
