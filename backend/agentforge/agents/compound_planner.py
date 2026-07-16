"""Parse long compound workspace prompts into ordered, linked execution plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agentforge.agents.workspace_executor import (
    extract_content_source_heading,
    extract_explicit_filename_from_clause,
    extract_h1_text_from_request,
    extract_literal_text_content,
    plan_deliverable_files,
    resolve_write_path,
)
from agentforge.agents.workspace_intent import (
    WorkspaceIntent,
    detect_file_edit_intent,
    detect_derived_filename_intent,
    extract_named_folder,
    extract_text_replacement,
    detect_workspace_intent,
)
from agentforge.config import settings
from agentforge.utils.html_tags import (
    DERIVED_FILE_WITH_TAG,
    HTML_TAG_NAME,
    extract_tag_insert_from_clause,
    parse_tag_reference,
)


class ClauseAction(StrEnum):
    """Action inferred from one prompt clause."""

    CREATE_DIRECTORY = "create_directory"
    WRITE_FILE = "write_file"
    READ_FILE = "read_file"
    EDIT_FILE = "edit_file"
    WRITE_DERIVED_FILE = "write_derived_file"
    UNKNOWN = "unknown"


@dataclass
class StepArtifact:
    """One deliverable produced by an earlier plan step."""

    step_id: int
    action: ClauseAction
    path: str | None
    file_kind: str | None = None


@dataclass
class CompoundClause:
    """One segmented clause from a multi-step user request."""

    clause_id: int
    text: str
    action: ClauseAction
    explicit_path: str | None = None
    explicit_dir: str | None = None
    named_folder: str | None = None
    named_file: str | None = None
    literal_text: str | None = None
    replace_from: str | None = None
    replace_to: str | None = None
    after_heading_level: int | None = None
    insert_heading_level: int | None = None
    after_tag: str | None = None
    insert_tag: str | None = None
    insert_heading_text: str | None = None
    references_created_html: bool = False
    references_created_file: bool = False
    naming_source: str | None = None
    derived_extension: str | None = None
    resolved_path: str | None = None
    source_path: str | None = None
    explicit_filename: str | None = None
    content_from_heading: str | None = None
    content_source_path: str | None = None


@dataclass
class CompoundPlan:
    """Ordered compound workspace plan with cross-step links."""

    clauses: list[CompoundClause] = field(default_factory=list)
    artifacts: list[StepArtifact] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        """
        Return the number of executable clauses.

        :return: Clause count excluding unknown/no-op segments
        """
        return len([clause for clause in self.clauses if clause.action != ClauseAction.UNKNOWN])


CLAUSE_BREAK = re.compile(
    r"\n+|\b(?:danach|dann|anschließend|afterwards|after that|and then|then)\b",
    re.IGNORECASE,
)

CREATE_DIR_CLAUSE = re.compile(
    r"\b("
    r"erstell\w*|anleg\w*|create|mkdir|generier\w*"
    r")\b.*\b(ordner|verzeichnis|folder|directory)\b",
    re.IGNORECASE | re.DOTALL,
)

WRITE_FILE_CLAUSE = re.compile(
    r"\b("
    r"erstell\w*|schreib\w*|write|create|generier\w*|füg\w+|hinzu"
    r")\b.*\b(datei|file)\b",
    re.IGNORECASE | re.DOTALL,
)

DARIN_FILE_CLAUSE = re.compile(
    r"\b(?:darin|therein)\b.*?\b(?:datei|file)\b",
    re.IGNORECASE | re.DOTALL,
)

HTML_INITIAL_WRITE = re.compile(
    rf"\b(?:füg\w+|hinzu|schreib\w+|insert|add)\b.*?\b(?:html|{HTML_TAG_NAME}(?:-tag)?|code)\b",
    re.IGNORECASE | re.DOTALL,
)

READ_CLAUSE = re.compile(
    r"\b(lese|lies|lesen|read|show|display|ausgib\w*|zeig\w*)\b",
    re.IGNORECASE,
)

EDIT_CLAUSE = re.compile(
    r"\b(bearbeit\w*|edit\w*|änder\w*|update\w*|tausch\w*|replace\w*|ersetz\w*)\b",
    re.IGNORECASE,
)

DERIVED_CLAUSE = DERIVED_FILE_WITH_TAG

CREATED_HTML_REF = re.compile(
    r"\b(erstellt\w*|created|geschrieben\w*|written)\b.*?\b(html|htm)\b",
    re.IGNORECASE,
)

CREATED_FILE_REF = re.compile(
    r"\b(erstellt\w*|created|geschrieben\w*|written)\b.*?\b(datei|file)\b",
    re.IGNORECASE,
)

NAMED_FILE_IN_CLAUSE = re.compile(
    r"(?:datei|file)\s+mit\s+(?:dem\s+)?namen[\s.:,]+([\w.-]+\.\w+)",
    re.IGNORECASE,
)

PATH_TOKEN = re.compile(
    r"(?:"
    r"(?:/[\w./-]+)|"
    r"(?:[\w.-]+/[\w./-]+\.\w+)"
    r")",
)


def _to_workspace_relative(path_str: str) -> str | None:
    """
    Convert an absolute or relative path to workspace-relative form.

    :param path_str: User-mentioned path
    :return: Workspace-relative path or None
    """
    raw = path_str.strip().strip("'\"")
    if not raw:
        return None
    root = settings.workspace_root.resolve()
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(root)):
                return None
            return str(resolved.relative_to(root))
        target = (root / raw.lstrip("/")).resolve()
        if not str(target).startswith(str(root)):
            return None
        return str(target.relative_to(root))
    except (OSError, ValueError):
        return None


def _extract_path_from_clause(clause: str) -> str | None:
    """
    Extract the first usable file path from one clause.

    :param clause: Prompt clause text
    :return: Workspace-relative file path or None
    """
    for match in PATH_TOKEN.finditer(clause):
        relative = _to_workspace_relative(match.group(0))
        if relative and "." in Path(relative).name:
            return relative
    return None


def _extract_directory_from_clause(clause: str) -> str | None:
    """
    Extract a directory path from one clause.

    :param clause: Prompt clause text
    :return: Workspace-relative directory or None
    """
    for match in PATH_TOKEN.finditer(clause):
        relative = _to_workspace_relative(match.group(0))
        if not relative:
            continue
        path = Path(relative)
        if path.suffix:
            return str(path.parent) if str(path.parent) not in {"", "."} else None
        return relative
    return None


def _is_derived_file_clause(clause: str) -> bool:
    """
    Return True when a clause requests a filename derived from heading content.

    Explicit filenames such as ``1.txt`` without a content-naming pattern are
    treated as regular writes, not derived-file steps.

    :param clause: Prompt clause text
    :return: Whether the clause is a derived-file request
    """
    if not DERIVED_CLAUSE.search(clause):
        return False
    explicit = extract_explicit_filename_from_clause(clause)
    if explicit and not re.search(
        r"namen\s+(?:des\s+)?(?:inhalts?",
        clause,
        re.IGNORECASE,
    ):
        return False
    return True


def _is_html_tag_insert(clause: str) -> bool:
    """
    Return True when a clause requests inserting an HTML element.

    :param clause: Prompt clause text
    :return: Whether the clause is an HTML element insertion edit
    """
    return extract_tag_insert_from_clause(clause) is not None


def _classify_clause(clause: str) -> ClauseAction:
    """
    Infer the primary action for one clause.

    :param clause: Prompt clause text
    :return: Clause action label
    """
    if _is_derived_file_clause(clause):
        return ClauseAction.WRITE_DERIVED_FILE
    if CREATE_DIR_CLAUSE.search(clause) and not WRITE_FILE_CLAUSE.search(clause):
        return ClauseAction.CREATE_DIRECTORY
    if _is_html_tag_insert(clause):
        return ClauseAction.EDIT_FILE
    if EDIT_CLAUSE.search(clause):
        return ClauseAction.EDIT_FILE
    if READ_CLAUSE.search(clause):
        return ClauseAction.READ_FILE
    if (
        WRITE_FILE_CLAUSE.search(clause)
        or DARIN_FILE_CLAUSE.search(clause)
        or HTML_INITIAL_WRITE.search(clause)
    ):
        return ClauseAction.WRITE_FILE
    if CREATE_DIR_CLAUSE.search(clause):
        return ClauseAction.CREATE_DIRECTORY
    return ClauseAction.UNKNOWN


def split_into_clauses(user_content: str) -> list[str]:
    """
    Split a long prompt into ordered clauses using newlines and temporal markers.

    :param user_content: Full user message
    :return: Ordered non-empty clause strings
    """
    text = (user_content or "").strip()
    if not text:
        return []

    raw_parts = CLAUSE_BREAK.split(text)
    merged: list[str] = []
    index = 0
    while index < len(raw_parts):
        part = raw_parts[index].strip(" \t.,;")
        if not part:
            index += 1
            continue
        lower = part.lower()
        if lower.startswith(("im verzeichnis", "in folder", "in directory", "under")) and index + 1 < len(raw_parts):
            nxt = raw_parts[index + 1].strip(" \t.,;")
            if nxt:
                merged.append(f"{part} {nxt}")
                index += 2
                continue
        if PATH_TOKEN.fullmatch(part) and merged:
            merged[-1] = f"{merged[-1]} {part}"
            index += 1
            continue
        if lower.startswith("darin ") and merged:
            merged[-1] = f"{merged[-1]} {part}"
            index += 1
            continue
        merged.append(part)
        index += 1
    return merged


def _is_context_clause(clause: str) -> bool:
    """
    Return True when a clause carries path context but no standalone action.

    :param clause: Prompt clause text
    :return: Whether the clause should be skipped in the action list
    """
    stripped = clause.strip()
    if PATH_TOKEN.fullmatch(stripped):
        return True
    if re.match(r"^(im verzeichnis|in folder|in directory|under)\b", stripped, re.IGNORECASE):
        if (
            DARIN_FILE_CLAUSE.search(stripped)
            or HTML_INITIAL_WRITE.search(stripped)
            or WRITE_FILE_CLAUSE.search(stripped)
            or CREATE_DIR_CLAUSE.search(stripped)
        ):
            return False
        return True
    return False


def is_compound_request(user_content: str) -> bool:
    """
    Return True when the prompt contains multiple logical workspace steps.

    :param user_content: Full user message
    :return: Whether compound planning should run
    """
    text = user_content or ""
    if not re.search(
        r"\b(?:danach|dann|anschließend|afterwards|after that|and then|then)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    clauses = split_into_clauses(user_content)
    if len(clauses) < 2:
        return False
    actions = {
        _classify_clause(clause)
        for clause in clauses
        if not _is_context_clause(clause)
    }
    actionable = actions - {ClauseAction.UNKNOWN}
    return len(actionable) >= 2


def _resolve_reference_path(
    clause: CompoundClause,
    artifacts: list[StepArtifact],
    *,
    prefer_html: bool = False,
) -> str | None:
    """
    Resolve an anaphoric file reference to a prior artifact path.

    :param clause: Current clause
    :param artifacts: Prior step artifacts
    :param prefer_html: Prefer the latest HTML artifact
    :return: Workspace-relative path or None
    """
    if prefer_html or clause.references_created_html:
        for artifact in reversed(artifacts):
            if artifact.path and artifact.path.lower().endswith((".html", ".htm")):
                return artifact.path
    if clause.references_created_file:
        for artifact in reversed(artifacts):
            if artifact.path:
                return artifact.path
    return None


def _register_artifact(
    artifacts: list[StepArtifact],
    *,
    step_id: int,
    action: ClauseAction,
    path: str | None,
) -> None:
    """
    Record one produced artifact for downstream reference resolution.

    :param artifacts: Mutable artifact list
    :param step_id: Source step identifier
    :param action: Source action
    :param path: Workspace-relative path
    """
    if not path:
        return
    file_kind = None
    if path.lower().endswith((".html", ".htm")):
        file_kind = "html"
    elif path.lower().endswith(".txt"):
        file_kind = "txt"
    artifacts.append(
        StepArtifact(
            step_id=step_id,
            action=action,
            path=path,
            file_kind=file_kind,
        )
    )


def _reconcile_plan_with_intent(
    plan: CompoundPlan,
    user_content: str,
    intent: WorkspaceIntent,
) -> None:
    """
    Align compound clause paths with intent enrichment and deliverable planning.

    :param plan: Compound plan to update in place
    :param user_content: Full user message
    :param intent: Parsed workspace intent
    """
    planned_files = plan_deliverable_files(user_content, intent)
    primary_file = planned_files[0] if planned_files else None
    primary_dir = None
    if intent.target_dirs:
        primary_dir = max(intent.target_dirs, key=len)
    elif primary_file and "/" in primary_file:
        primary_dir = primary_file.rsplit("/", 1)[0]

    for clause in plan.clauses:
        if clause.action == ClauseAction.CREATE_DIRECTORY and primary_dir:
            clause.resolved_path = primary_dir
        elif clause.action in {
            ClauseAction.READ_FILE,
            ClauseAction.EDIT_FILE,
        } and primary_file:
            if not clause.resolved_path:
                clause.resolved_path = primary_file
            elif Path(clause.resolved_path).name == Path(primary_file).name:
                clause.resolved_path = primary_file
        elif clause.action == ClauseAction.WRITE_FILE and primary_file and not clause.resolved_path:
            clause.resolved_path = primary_file
        elif clause.action == ClauseAction.WRITE_DERIVED_FILE and primary_file:
            clause.source_path = primary_file
            if not clause.resolved_path:
                clause.resolved_path = primary_file
        if (
            clause.content_from_heading
            and primary_file
            and primary_file.lower().endswith((".html", ".htm"))
        ):
            clause.content_source_path = primary_file

    plan.artifacts = []
    for clause in plan.clauses:
        if clause.action == ClauseAction.CREATE_DIRECTORY and clause.resolved_path:
            _register_artifact(
                plan.artifacts,
                step_id=clause.clause_id,
                action=clause.action,
                path=clause.resolved_path,
            )
        elif clause.action == ClauseAction.WRITE_FILE and clause.resolved_path:
            _register_artifact(
                plan.artifacts,
                step_id=clause.clause_id,
                action=clause.action,
                path=clause.resolved_path,
            )
        elif clause.action == ClauseAction.WRITE_DERIVED_FILE and clause.resolved_path:
            _register_artifact(
                plan.artifacts,
                step_id=clause.clause_id,
                action=clause.action,
                path=clause.resolved_path,
            )


def build_compound_plan(user_content: str, intent: WorkspaceIntent | None = None) -> CompoundPlan:
    """
    Build an ordered compound plan with cross-step associations.

    :param user_content: Full user message
    :param intent: Optional pre-parsed workspace intent
    :return: Compound plan with resolved links
    """
    parsed_intent = intent or detect_workspace_intent(user_content)
    planned_files = plan_deliverable_files(user_content, parsed_intent)
    primary_file = planned_files[0] if planned_files else None
    primary_dir = None
    if parsed_intent.target_dirs:
        primary_dir = max(parsed_intent.target_dirs, key=len)
    elif primary_file and "/" in primary_file:
        primary_dir = primary_file.rsplit("/", 1)[0]

    plan = CompoundPlan()
    pending_folder: str | None = extract_named_folder(user_content)
    pending_file: str | None = None

    for clause_id, clause_text in enumerate(split_into_clauses(user_content), start=1):
        if _is_context_clause(clause_text):
            continue
        action = _classify_clause(clause_text)
        clause = CompoundClause(
            clause_id=clause_id,
            text=clause_text,
            action=action,
            explicit_path=_extract_path_from_clause(clause_text),
            explicit_dir=_extract_directory_from_clause(clause_text),
            named_folder=extract_named_folder(clause_text) or pending_folder,
            references_created_html=bool(CREATED_HTML_REF.search(clause_text)),
            references_created_file=bool(CREATED_FILE_REF.search(clause_text)),
        )

        named_file_match = NAMED_FILE_IN_CLAUSE.search(clause_text)
        if named_file_match:
            pending_file = named_file_match.group(1)
            clause.named_file = pending_file

        explicit_filename = extract_explicit_filename_from_clause(clause_text)
        if explicit_filename:
            clause.explicit_filename = explicit_filename

        content_heading = extract_content_source_heading(clause_text)
        if content_heading:
            clause.content_from_heading = content_heading
            html_source = _resolve_reference_path(
                clause,
                plan.artifacts,
                prefer_html=True,
            )
            if html_source:
                clause.content_source_path = html_source

        literal = extract_literal_text_content(clause_text)
        if literal:
            clause.literal_text = literal
        elif action == ClauseAction.WRITE_FILE and primary_file:
            clause.literal_text = extract_h1_text_from_request(user_content)

        replacement = extract_text_replacement(clause_text)
        if replacement:
            clause.replace_from, clause.replace_to = replacement

        insert_info = extract_tag_insert_from_clause(clause_text)
        if insert_info:
            after_tag, insert_tag, insert_text = insert_info
            clause.after_tag = after_tag
            clause.insert_tag = insert_tag
            clause.insert_heading_text = insert_text
            if after_tag.startswith("h") and after_tag[1:].isdigit():
                clause.after_heading_level = int(after_tag[1:])
            if insert_tag.startswith("h") and insert_tag[1:].isdigit():
                clause.insert_heading_level = int(insert_tag[1:])

        if action == ClauseAction.WRITE_DERIVED_FILE:
            tag_source = parse_tag_reference(clause_text)
            if tag_source:
                clause.naming_source = tag_source
            elif re.search(r"\büberschrift\b|\bheading\b", clause_text, re.IGNORECASE):
                clause.naming_source = "h1"
            else:
                clause.naming_source = "content"
            ext_match = re.search(r"\.(txt|md|json|csv)\b|dateiendung\s*\.(\w+)", clause_text, re.I)
            if ext_match:
                clause.derived_extension = "." + (ext_match.group(1) or ext_match.group(2)).lower()
            else:
                clause.derived_extension = ".txt"

        resolved = clause.explicit_path
        if not resolved and clause.explicit_filename and action == ClauseAction.WRITE_FILE:
            resolved = resolve_write_path(
                clause.explicit_filename,
                primary_dir,
                primary_file,
            )
        if not resolved:
            resolved = _resolve_reference_path(
                clause,
                plan.artifacts,
                prefer_html=action in {
                    ClauseAction.READ_FILE,
                    ClauseAction.EDIT_FILE,
                    ClauseAction.WRITE_DERIVED_FILE,
                },
            )
        if not resolved and clause.named_file and (primary_dir or clause.named_folder):
            base = primary_dir or clause.named_folder
            if base:
                resolved = f"{base}/{clause.named_file}"
        if not resolved and action in {
            ClauseAction.WRITE_FILE,
            ClauseAction.READ_FILE,
            ClauseAction.EDIT_FILE,
        }:
            resolved = primary_file
        if not resolved and action == ClauseAction.WRITE_DERIVED_FILE:
            resolved = _resolve_reference_path(
                clause,
                plan.artifacts,
                prefer_html=True,
            )

        clause.resolved_path = resolved
        if action == ClauseAction.WRITE_DERIVED_FILE:
            clause.source_path = resolved

        if action == ClauseAction.CREATE_DIRECTORY:
            directory = clause.explicit_dir or clause.named_folder or primary_dir
            clause.resolved_path = directory
            _register_artifact(
                plan.artifacts,
                step_id=clause_id,
                action=action,
                path=directory,
            )
        elif action == ClauseAction.WRITE_FILE and resolved:
            _register_artifact(
                plan.artifacts,
                step_id=clause_id,
                action=action,
                path=resolved,
            )
            pending_file = Path(resolved).name
        elif action in {ClauseAction.READ_FILE, ClauseAction.EDIT_FILE} and resolved:
            _register_artifact(
                plan.artifacts,
                step_id=clause_id,
                action=action,
                path=resolved,
            )

        plan.clauses.append(clause)

    _reconcile_plan_with_intent(plan, user_content, parsed_intent)

    if detect_derived_filename_intent(user_content) and primary_file:
        has_derived = any(
            clause.action == ClauseAction.WRITE_DERIVED_FILE for clause in plan.clauses
        )
        if not has_derived:
            naming_source = "h1"
            for clause_text in reversed(split_into_clauses(user_content)):
                if not _is_derived_file_clause(clause_text):
                    continue
                naming_source = parse_tag_reference(clause_text) or naming_source
                break
            plan.clauses.append(
                CompoundClause(
                    clause_id=len(plan.clauses) + 1,
                    text="derived file from HTML heading",
                    action=ClauseAction.WRITE_DERIVED_FILE,
                    resolved_path=primary_file,
                    source_path=primary_file,
                    naming_source=naming_source,
                    derived_extension=".txt",
                    references_created_html=True,
                )
            )

    return plan


def format_compound_plan_block(plan: CompoundPlan) -> str:
    """
    Format the compound plan for orchestration transcripts.

    :param plan: Compound workspace plan
    :return: Human-readable block or empty string
    """
    if plan.step_count < 2:
        return ""
    lines = ["Compound plan (logical steps with cross-references):"]
    for clause in plan.clauses:
        if clause.action == ClauseAction.UNKNOWN:
            continue
        target = clause.resolved_path or clause.source_path or "—"
        ref = ""
        if clause.references_created_html:
            ref = " [ref: created HTML]"
        elif clause.references_created_file:
            ref = " [ref: created file]"
        lines.append(
            f"{clause.clause_id}. {clause.action.value} → `{target}` — {clause.text[:120]}{ref}",
        )
    return "\n".join(lines)
