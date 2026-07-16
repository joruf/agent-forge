"""Parse multi-step workspace requests into ordered execution agendas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agentforge.agents.compound_planner import (
    ClauseAction,
    build_compound_plan,
    is_compound_request,
    split_into_clauses,
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
    insert_after_heading: int | None = None
    insert_heading_level: int | None = None
    insert_after_tag: str | None = None
    insert_tag: str | None = None
    insert_heading_text: str | None = None
    source_path: str | None = None
    naming_source: str | None = None
    derived_extension: str | None = None
    source_clause: str | None = None
    content_from_heading: str | None = None
    content_source_path: str | None = None


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


def _agenda_step_signature(step: AgendaStep) -> tuple:
    """
    Build a deduplication signature for one agenda step.

    :param step: Agenda step
    :return: Tuple identifying the step intent
    """
    return (
        step.action,
        step.path,
        step.content_from_heading,
        step.insert_heading_text,
        step.naming_source,
    )


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
    seen_signatures: set[tuple] = set()
    ordered_clauses = sorted(
        [clause for clause in plan.clauses if clause.action != ClauseAction.UNKNOWN],
        key=lambda clause: clause.clause_id,
    )

    for clause in ordered_clauses:
        action = AgendaAction(clause.action.value)

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
            seen_signatures.add(
                (
                    AgendaAction.CREATE_DIRECTORY,
                    directory,
                    None,
                    None,
                    None,
                )
            )
            step_id += 1
            continue

        if clause.action == ClauseAction.WRITE_FILE:
            path = clause.resolved_path
            if not path:
                continue
            if clause.content_from_heading and clause.content_source_path:
                detail = (
                    f"Create `{path}` with {clause.content_from_heading} text "
                    f"from `{clause.content_source_path}`"
                )
            elif clause.literal_text:
                detail = f'Create `{path}` containing text "{clause.literal_text}"'
            elif path.endswith((".html", ".htm")):
                detail = f"Create `{path}` with the requested HTML content"
            else:
                detail = f"Create `{path}` with the requested content"
            candidate = AgendaStep(
                step_id,
                AgendaAction.WRITE_FILE,
                path,
                detail,
                source_clause=clause.text,
                content_from_heading=clause.content_from_heading,
                content_source_path=clause.content_source_path,
            )
            signature = _agenda_step_signature(candidate)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            steps.append(candidate)
            step_id += 1
            continue

        if clause.action == ClauseAction.READ_FILE:
            path = clause.resolved_path
            if not path:
                continue
            candidate = AgendaStep(
                step_id,
                AgendaAction.READ_FILE,
                path,
                f"Read `{path}` from disk and show the content in chat",
                source_clause=clause.text,
            )
            signature = _agenda_step_signature(candidate)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            steps.append(candidate)
            step_id += 1
            continue

        if clause.action == ClauseAction.EDIT_FILE:
            path = clause.resolved_path
            if not path:
                continue
            if not clause.replace_from and not clause.insert_heading_text:
                continue
            detail = f"Edit `{path}`"
            if clause.insert_heading_text:
                if clause.after_tag and clause.insert_tag:
                    detail = (
                        f'Insert <{clause.insert_tag}> "{clause.insert_heading_text}" '
                        f"under <{clause.after_tag}> in `{path}`"
                    )
                else:
                    after_level = clause.after_heading_level or 1
                    insert_level = clause.insert_heading_level or 2
                    detail = (
                        f'Insert H{insert_level} "{clause.insert_heading_text}" '
                        f"under H{after_level} in `{path}`"
                    )
            elif clause.replace_from and clause.replace_to:
                detail = (
                    f'Replace "{clause.replace_from}" with "{clause.replace_to}" in `{path}`'
                )
            candidate = AgendaStep(
                step_id,
                AgendaAction.EDIT_FILE,
                path,
                detail,
                replace_from=clause.replace_from,
                replace_to=clause.replace_to,
                    insert_after_heading=clause.after_heading_level,
                    insert_heading_level=clause.insert_heading_level,
                    insert_after_tag=clause.after_tag,
                    insert_tag=clause.insert_tag,
                    insert_heading_text=clause.insert_heading_text,
                source_clause=clause.text,
            )
            signature = _agenda_step_signature(candidate)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            steps.append(candidate)
            step_id += 1
            continue

        if clause.action == ClauseAction.WRITE_DERIVED_FILE:
            source_path = clause.source_path or clause.resolved_path
            if not source_path:
                continue
            extension = clause.derived_extension or ".txt"
            naming = clause.naming_source or "content"
            candidate = AgendaStep(
                step_id,
                AgendaAction.WRITE_DERIVED_FILE,
                None,
                f"Create `{extension}` file named after {naming} in `{source_path}`",
                source_path=source_path,
                naming_source=naming,
                derived_extension=extension,
                source_clause=clause.text,
            )
            signature = _agenda_step_signature(candidate)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            steps.append(candidate)
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
        naming_source = "h1"
        for clause_text in reversed(split_into_clauses(text)):
            if not re.search(
                r"(?:dateiendung|\.txt|namen des inhalts)",
                clause_text,
                re.IGNORECASE,
            ):
                continue
            from agentforge.utils.html_tags import parse_tag_reference

            tag_source = parse_tag_reference(clause_text)
            if tag_source:
                naming_source = tag_source
            break
        steps.append(
            AgendaStep(
                step_id,
                AgendaAction.WRITE_DERIVED_FILE,
                None,
                f"Create `.txt` file named after {naming_source} content in `{primary_file}`",
                source_path=primary_file,
                naming_source=naming_source,
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

    Compound steps preserve full multi-step ordering. Legacy steps supply
    canonical workspace paths where compound resolution is incomplete.

    :param legacy_steps: Steps from single-pass intent rules
    :param compound_steps: Steps from compound clause planning
    :return: Ordered merged agenda with sequential step IDs
    """
    legacy_by_action = {step.action: step for step in legacy_steps}
    merged: list[AgendaStep] = []
    compound_actions: set[AgendaAction] = set()

    for step in compound_steps:
        compound_actions.add(step.action)
        legacy = legacy_by_action.get(step.action)
        path = step.path
        source_path = step.source_path
        replace_from = step.replace_from
        replace_to = step.replace_to
        naming_source = step.naming_source
        derived_extension = step.derived_extension
        content_from_heading = step.content_from_heading
        content_source_path = step.content_source_path

        if legacy:
            if not path and legacy.path:
                path = legacy.path
            elif (
                legacy.path
                and step.action in {
                    AgendaAction.READ_FILE,
                    AgendaAction.EDIT_FILE,
                }
                and path
                and Path(path).name == Path(legacy.path).name
            ):
                path = legacy.path
            if not source_path and legacy.source_path:
                source_path = legacy.source_path
            elif (
                legacy.source_path
                and step.action == AgendaAction.WRITE_DERIVED_FILE
            ):
                source_path = legacy.source_path
            if legacy.replace_from and not replace_from:
                replace_from = legacy.replace_from
            if legacy.replace_to and not replace_to:
                replace_to = legacy.replace_to
            if legacy.naming_source and not naming_source:
                naming_source = legacy.naming_source
            if legacy.derived_extension and not derived_extension:
                derived_extension = legacy.derived_extension
            if (
                legacy.path
                and step.action == AgendaAction.WRITE_FILE
                and not content_from_heading
                and path
                and Path(path).name == Path(legacy.path).name
            ):
                path = legacy.path

        merged.append(
            AgendaStep(
                step.step_id,
                step.action,
                path,
                step.detail,
                replace_from=replace_from,
                replace_to=replace_to,
                insert_after_heading=step.insert_after_heading,
                insert_heading_level=step.insert_heading_level,
                insert_after_tag=step.insert_after_tag,
                insert_tag=step.insert_tag,
                insert_heading_text=step.insert_heading_text,
                source_path=source_path,
                naming_source=naming_source,
                derived_extension=derived_extension,
                source_clause=step.source_clause,
                content_from_heading=content_from_heading,
                content_source_path=content_source_path,
            )
        )

    for legacy in legacy_steps:
        if legacy.action in compound_actions:
            continue
        merged.append(legacy)

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
