"""Parse multi-step workspace requests into ordered execution agendas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentforge.agents.compound_planner import (
    ClauseAction,
    build_compound_plan,
    is_compound_request,
)
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
    WRITE_DERIVED_FILE = "write_derived_file"


@dataclass
class AgendaStep:
    """Single numbered step in a workspace execution agenda."""

    step_id: int
    action: AgendaAction
    path: str | None
    detail: str
    replace_from: str | None = None
    replace_to: str | None = None
    source_path: str | None = None
    naming_source: str | None = None
    derived_extension: str | None = None
    source_clause: str | None = None


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


def _agenda_from_compound_plan(user_content: str, intent: WorkspaceIntent) -> list[AgendaStep]:
    """
    Build agenda steps from the compound planner output.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Ordered agenda steps
    """
    plan = build_compound_plan(user_content, intent)
    steps: list[AgendaStep] = []
    step_id = 1
    action_order = {
        AgendaAction.CREATE_DIRECTORY: 0,
        AgendaAction.WRITE_FILE: 1,
        AgendaAction.READ_FILE: 2,
        AgendaAction.EDIT_FILE: 3,
        AgendaAction.WRITE_DERIVED_FILE: 4,
    }
    ordered_clauses = sorted(
        [clause for clause in plan.clauses if clause.action != ClauseAction.UNKNOWN],
        key=lambda clause: (action_order.get(AgendaAction(clause.action.value), 99), clause.clause_id),
    )
    seen: set[tuple[str, str | None]] = set()

    for clause in ordered_clauses:
        action = AgendaAction(clause.action.value)
        dedupe_key = (action.value, clause.resolved_path or clause.source_path)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if clause.action == ClauseAction.CREATE_DIRECTORY:
            directory = clause.resolved_path or clause.named_folder
            if not directory:
                continue
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.CREATE_DIRECTORY,
                    directory,
                    f"Create directory `{directory}`",
                    source_clause=clause.text,
                )
            )
            step_id += 1
            continue

        if clause.action == ClauseAction.WRITE_FILE:
            path = clause.resolved_path
            if not path:
                continue
            if clause.literal_text:
                detail = f'Create `{path}` containing text "{clause.literal_text}"'
            elif path.endswith((".html", ".htm")):
                detail = f"Create `{path}` with the requested HTML content"
            else:
                detail = f"Create `{path}` with the requested content"
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.WRITE_FILE,
                    path,
                    detail,
                    source_clause=clause.text,
                )
            )
            step_id += 1
            continue

        if clause.action == ClauseAction.READ_FILE:
            path = clause.resolved_path
            if not path:
                continue
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.READ_FILE,
                    path,
                    f"Read `{path}` from disk and show the content in chat",
                    source_clause=clause.text,
                )
            )
            step_id += 1
            continue

        if clause.action == ClauseAction.EDIT_FILE:
            path = clause.resolved_path
            if not path:
                continue
            detail = f"Edit `{path}`"
            if clause.replace_from and clause.replace_to:
                detail = (
                    f'Replace "{clause.replace_from}" with "{clause.replace_to}" in `{path}`'
                )
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.EDIT_FILE,
                    path,
                    detail,
                    replace_from=clause.replace_from,
                    replace_to=clause.replace_to,
                    source_clause=clause.text,
                )
            )
            step_id += 1
            continue

        if clause.action == ClauseAction.WRITE_DERIVED_FILE:
            source_path = clause.source_path or clause.resolved_path
            if not source_path:
                continue
            extension = clause.derived_extension or ".txt"
            naming = clause.naming_source or "content"
            steps.append(
                AgendaStep(
                    step_id,
                    AgendaAction.WRITE_DERIVED_FILE,
                    None,
                    f"Create `{extension}` file named after {naming} in `{source_path}`",
                    source_path=source_path,
                    naming_source=naming,
                    derived_extension=extension,
                    source_clause=clause.text,
                )
            )
            step_id += 1

    return steps


def _agenda_from_legacy_rules(user_content: str, intent: WorkspaceIntent) -> list[AgendaStep]:
    """
    Build agenda steps using the original single-pass heuristic rules.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Ordered agenda steps
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
        step_id += 1

    if intent.wants_derived_file and primary_file and primary_file.lower().endswith((".html", ".htm")):
        steps.append(
            AgendaStep(
                step_id,
                AgendaAction.WRITE_DERIVED_FILE,
                None,
                f"Create `.txt` file named after H1 content in `{primary_file}`",
                source_path=primary_file,
                naming_source="h1",
                derived_extension=".txt",
            )
        )

    return steps


def _merge_agenda_steps(
    legacy_steps: list[AgendaStep],
    compound_steps: list[AgendaStep],
) -> list[AgendaStep]:
    """
    Merge legacy intent-resolved steps with compound clause steps.

    Legacy steps supply canonical workspace paths. Compound steps add ordering
    context and steps such as derived writes that legacy rules may omit.

    :param legacy_steps: Steps from single-pass intent rules
    :param compound_steps: Steps from compound clause planning
    :return: Ordered merged agenda with sequential step IDs
    """
    action_order = [
        AgendaAction.CREATE_DIRECTORY,
        AgendaAction.WRITE_FILE,
        AgendaAction.READ_FILE,
        AgendaAction.EDIT_FILE,
        AgendaAction.WRITE_DERIVED_FILE,
    ]
    legacy_map = {step.action: step for step in legacy_steps}
    compound_map: dict[AgendaAction, AgendaStep] = {}
    for step in compound_steps:
        compound_map.setdefault(step.action, step)

    merged: list[AgendaStep] = []
    for action in action_order:
        step = legacy_map.get(action) or compound_map.get(action)
        if step is not None:
            merged.append(step)

    for index, step in enumerate(merged, start=1):
        step.step_id = index
    return merged


def build_workspace_agenda(
    user_content: str,
    intent: WorkspaceIntent,
) -> list[AgendaStep]:
    """
    Build an ordered 1..N agenda from the user request before agent execution.

    Compound prompts are segmented into clauses, linked by cross-references,
    and converted into a deterministic step list. Simple prompts keep the
    legacy single-pass behavior.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Ordered agenda steps (may be empty for non-workspace tasks)
    """
    legacy_steps = _agenda_from_legacy_rules(user_content, intent)
    if is_compound_request(user_content):
        compound_steps = _agenda_from_compound_plan(user_content, intent)
        if compound_steps:
            return _merge_agenda_steps(legacy_steps, compound_steps)
    return legacy_steps


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
        if step.source_path and not step.path:
            path_part = f" → from `{step.source_path}`"
        lines.append(f"{step.step_id}. {step.action.value}{path_part} — {step.detail}")
    return "\n".join(lines)
