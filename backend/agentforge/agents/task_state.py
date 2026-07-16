"""Shared task board for multi-agent orchestration."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentforge.agents.compound_planner import build_compound_plan, format_compound_plan_block
from agentforge.agents.workspace_agenda import (
    AgendaAction,
    build_workspace_agenda,
    format_agenda_block,
)
from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.memory.store import memory_store

TASK_BOARD_MEMORY_KEY = "_agentforge_task_board"
MAX_PERSISTED_FACTS = 40
MAX_FACTS_IN_PROMPT = 12
MAX_PRIOR_FACTS_IN_PROMPT = 8
MAX_WEAK_RETRIES = 2
MAX_REPETITION_STALLS = 2
REPETITION_SIMILARITY_THRESHOLD = 0.85
MIN_REPETITION_TEXT_LENGTH = 40


class TaskType(StrEnum):
    """High-level task classification for orchestration."""

    READ_AND_DISPLAY = "read_and_display"
    WRITE_FILES = "write_files"
    WRITE_THEN_READ = "write_then_read"
    WORKFLOW = "workflow"
    LIST_DIRECTORY = "list_directory"
    RUN_COMMAND = "run_command"
    GENERAL = "general"


@dataclass
class TaskPlanStep:
    """One step in the PM task decomposition."""

    step_id: int
    action: str
    assignee: str
    detail: str


@dataclass
class TaskFact:
    """Verified information collected during orchestration."""

    id: str
    source: str
    kind: str
    path: str | None
    content: str
    verified: bool
    agent_id: str
    round_num: int

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize the fact for persistence.

        :return: JSON-serializable dict
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskFact:
        """
        Restore a fact from persisted JSON.

        :param payload: Serialized fact dict
        :return: TaskFact instance
        """
        return cls(
            id=str(payload.get("id") or uuid.uuid4().hex[:12]),
            source=str(payload.get("source") or ""),
            kind=str(payload.get("kind") or "unknown"),
            path=payload.get("path"),
            content=str(payload.get("content") or ""),
            verified=bool(payload.get("verified")),
            agent_id=str(payload.get("agent_id") or "system"),
            round_num=int(payload.get("round_num") or 0),
        )


@dataclass
class CompletionReport:
    """Result of automatic completion checks before final synthesis."""

    complete: bool
    reason: str = ""
    missing: list[str] = field(default_factory=list)


@dataclass
class TaskState:
    """Blackboard state shared across agents within one orchestration run."""

    user_request: str
    task_type: TaskType
    interpreted_request: str = ""
    prompt_corrections: list[dict[str, str]] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    facts: list[TaskFact] = field(default_factory=list)
    plan_steps: list[TaskPlanStep] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    prior_targets: list[str] = field(default_factory=list)
    prior_summary: str = ""
    weak_retry_counts: dict[str, int] = field(default_factory=dict)

    def add_fact(self, fact: TaskFact) -> None:
        """
        Append a fact and replace duplicates for the same source/path pair.

        :param fact: Verified or attempted fact to store
        """
        if fact.path:
            self.facts = [
                existing
                for existing in self.facts
                if not (
                    existing.source == fact.source
                    and existing.path == fact.path
                    and existing.kind == fact.kind
                )
            ]
        self.facts.append(fact)

    def verified_facts(self, kind: str | None = None) -> list[TaskFact]:
        """
        Return verified facts optionally filtered by kind.

        :param kind: Optional fact kind filter
        :return: Matching verified facts
        """
        items = [fact for fact in self.facts if fact.verified]
        if kind:
            items = [fact for fact in items if fact.kind == kind]
        return items

    def fact_content_for_path(self, path: str) -> str | None:
        """
        Return verified file content for a workspace-relative path.

        :param path: Workspace-relative file path
        :return: File content or None
        """
        normalized = path.strip().lstrip("/")
        for fact in self.verified_facts("file_content"):
            if fact.path and fact.path.strip().lstrip("/") == normalized:
                return fact.content
        return None

    def to_persisted_payload(self) -> dict[str, Any]:
        """
        Build JSON payload for chat-scoped persistence.

        :return: Serializable task-board snapshot
        """
        return {
            "last_request": self.user_request,
            "interpreted_request": self.interpreted_request,
            "last_task_type": self.task_type.value,
            "last_targets": self.targets,
            "facts": [fact.to_dict() for fact in self.facts[-MAX_PERSISTED_FACTS:]],
        }


def classify_task_type(intent: WorkspaceIntent) -> TaskType:
    """
    Map workspace intent flags to a task-board task type.

    :param intent: Parsed workspace intent
    :return: Task type enum value
    """
    if intent.wants_file_edit and (
        intent.wants_file_creation or intent.wants_file_read
    ):
        return TaskType.WORKFLOW
    if intent.wants_file_creation and intent.wants_file_read:
        return TaskType.WRITE_THEN_READ
    if intent.wants_file_read:
        return TaskType.READ_AND_DISPLAY
    if intent.wants_list_directory:
        return TaskType.LIST_DIRECTORY
    if intent.wants_file_creation:
        return TaskType.WRITE_FILES
    if intent.wants_command_execution:
        return TaskType.RUN_COMMAND
    return TaskType.GENERAL


def build_task_plan(task_type: TaskType, targets: list[str]) -> list[TaskPlanStep]:
    """
    Build a deterministic PM task plan for the current request.

    :param task_type: Classified task type
    :param targets: Workspace-relative target paths
    :return: Ordered plan steps
    """
    target_text = ", ".join(targets) if targets else "workspace"
    if task_type == TaskType.READ_AND_DISPLAY:
        return [
            TaskPlanStep(1, "read_file", "developer", f"Read file(s): {target_text}"),
            TaskPlanStep(2, "verify_content", "reviewer", "Confirm content matches disk"),
            TaskPlanStep(3, "present_to_user", "project_manager", "Quote file content for the user"),
        ]
    if task_type == TaskType.WRITE_FILES:
        return [
            TaskPlanStep(1, "write_file", "developer", f"Create/update file(s): {target_text}"),
            TaskPlanStep(2, "verify_on_disk", "reviewer", "Confirm files exist on disk"),
            TaskPlanStep(3, "present_to_user", "project_manager", "Summarize created files"),
        ]
    if task_type == TaskType.WRITE_THEN_READ:
        return [
            TaskPlanStep(1, "write_file", "developer", f"Create/update file(s): {target_text}"),
            TaskPlanStep(2, "verify_on_disk", "reviewer", "Confirm files exist on disk"),
            TaskPlanStep(3, "read_file", "developer", "Read created file(s) from disk"),
            TaskPlanStep(4, "present_to_user", "project_manager", "Quote file content for the user"),
        ]
    if task_type == TaskType.WORKFLOW:
        return [
            TaskPlanStep(1, "analyze", "project_manager", "Coordinate the full workspace workflow"),
            TaskPlanStep(2, "present_to_user", "project_manager", "Deliver read-back and edit summary"),
        ]
    if task_type == TaskType.LIST_DIRECTORY:
        return [
            TaskPlanStep(1, "list_directory", "developer", f"List directory: {target_text}"),
            TaskPlanStep(2, "present_to_user", "project_manager", "Summarize directory contents"),
        ]
    if task_type == TaskType.RUN_COMMAND:
        return [
            TaskPlanStep(1, "run_command", "developer", "Execute the requested command"),
            TaskPlanStep(2, "present_to_user", "project_manager", "Summarize command output"),
        ]
    return [
        TaskPlanStep(1, "analyze", "project_manager", "Coordinate specialists for the request"),
        TaskPlanStep(2, "present_to_user", "project_manager", "Deliver the final answer"),
    ]


def _assignee_for_agenda_action(action: AgendaAction) -> str:
    """
    Map an agenda action to the responsible agent role.

    :param action: Agenda step action
    :return: Role identifier
    """
    if action == AgendaAction.READ_FILE:
        return "developer"
    if action == AgendaAction.EDIT_FILE:
        return "developer"
    if action in {AgendaAction.CREATE_DIRECTORY, AgendaAction.WRITE_FILE}:
        return "developer"
    if action == AgendaAction.WRITE_DERIVED_FILE:
        return "developer"
    return "project_manager"


def build_plan_from_agenda(
    user_content: str,
    intent: WorkspaceIntent,
) -> tuple[list[TaskPlanStep], list[str]]:
    """
    Build numbered plan steps and canonical targets from a workspace agenda.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Tuple of plan steps and deduplicated target paths
    """
    agenda = build_workspace_agenda(user_content, intent)
    if not agenda:
        return [], []

    plan_steps = [
        TaskPlanStep(
            step.step_id,
            step.action.value,
            _assignee_for_agenda_action(step.action),
            step.detail,
        )
        for step in agenda
    ]
    targets: list[str] = []
    seen: set[str] = set()
    for step in agenda:
        if step.path and step.path not in seen:
            seen.add(step.path)
            targets.append(step.path)
    return plan_steps, targets


def build_task_state(
    user_content: str,
    intent: WorkspaceIntent,
    prior_payload: dict[str, Any] | None = None,
    *,
    interpreted_request: str | None = None,
    prompt_corrections: list[dict[str, str]] | None = None,
) -> TaskState:
    """
    Initialize a task board for the current orchestration run.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :param prior_payload: Optional persisted task-board snapshot from earlier turns
    :param interpreted_request: Spell-normalized message used for intent parsing
    :param prompt_corrections: Applied pre-processing corrections
    :return: Initialized task state
    """
    task_type = classify_task_type(intent)
    processing_content = interpreted_request or user_content
    agenda_plan, agenda_targets = build_plan_from_agenda(processing_content, intent)
    if agenda_plan:
        targets = agenda_targets
        plan_steps = agenda_plan
    else:
        targets = list(intent.target_paths or intent.target_dirs)
        plan_steps = build_task_plan(task_type, targets)
    prior_targets: list[str] = []
    prior_summary = ""
    if prior_payload:
        prior_targets = [str(item) for item in prior_payload.get("last_targets") or []]
        prior_summary = str(prior_payload.get("last_request") or "").strip()

    state = TaskState(
        user_request=user_content,
        interpreted_request=processing_content,
        prompt_corrections=list(prompt_corrections or []),
        task_type=task_type,
        targets=targets,
        plan_steps=plan_steps,
        prior_targets=prior_targets,
        prior_summary=prior_summary,
    )
    for raw in prior_payload.get("facts") or [] if prior_payload else []:
        if isinstance(raw, dict):
            fact = TaskFact.from_dict(raw)
            fact.agent_id = f"prior:{fact.agent_id}"
            state.add_fact(fact)
    return state


def seed_read_facts(
    task_state: TaskState,
    prefetched: dict[str, str],
    *,
    agent_id: str = "system",
    round_num: int = 0,
) -> None:
    """
    Seed verified read facts from deterministic pre-fetch results.

    :param task_state: Active task board
    :param prefetched: Mapping of path to content or error text
    :param agent_id: Agent or subsystem that produced the read
    :param round_num: Orchestration round index
    """
    for path, payload in prefetched.items():
        if payload.startswith("[ERROR]"):
            task_state.add_fact(
                TaskFact(
                    id=f"fact_{uuid.uuid4().hex[:10]}",
                    source="prefetch_read",
                    kind="file_error",
                    path=path,
                    content=payload,
                    verified=False,
                    agent_id=agent_id,
                    round_num=round_num,
                )
            )
            continue
        task_state.add_fact(
            TaskFact(
                id=f"fact_{uuid.uuid4().hex[:10]}",
                source="prefetch_read",
                kind="file_content",
                path=path,
                content=payload,
                verified=True,
                agent_id=agent_id,
                round_num=round_num,
            )
        )


def seed_write_facts(
    task_state: TaskState,
    relative_paths: list[str],
    *,
    agent_id: str = "developer",
    round_num: int = 0,
    source: str = "guarantee_write",
) -> None:
    """
    Seed verified write facts for files confirmed on disk.

    :param task_state: Active task board
    :param relative_paths: Workspace-relative file paths
    :param agent_id: Agent or subsystem that produced the write
    :param round_num: Orchestration round index
    :param source: Fact source label
    """
    for path in relative_paths:
        if not path:
            continue
        task_state.add_fact(
            TaskFact(
                id=f"fact_{uuid.uuid4().hex[:10]}",
                source=source,
                kind="file_written",
                path=path,
                content=f"Verified on disk: {path}",
                verified=True,
                agent_id=agent_id,
                round_num=round_num,
            )
        )


def seed_edit_facts(
    task_state: TaskState,
    relative_path: str,
    *,
    replace_from: str,
    replace_to: str,
    agent_id: str = "developer",
    round_num: int = 0,
    source: str = "agenda_edit",
) -> None:
    """
    Seed verified edit facts after a deterministic text replacement.

    :param task_state: Active task board
    :param relative_path: Workspace-relative file path
    :param replace_from: Original text replaced on disk
    :param replace_to: New text written on disk
    :param agent_id: Agent or subsystem that produced the edit
    :param round_num: Orchestration round index
    :param source: Fact source label
    """
    if not relative_path:
        return
    task_state.add_fact(
        TaskFact(
            id=f"fact_{uuid.uuid4().hex[:10]}",
            source=source,
            kind="file_edited",
            path=relative_path,
            content=f'Replaced "{replace_from}" with "{replace_to}" in {relative_path}',
            verified=True,
            agent_id=agent_id,
            round_num=round_num,
        )
    )


def seed_list_directory_facts(
    task_state: TaskState,
    relative_dir: str,
    listing: str,
    *,
    agent_id: str = "system",
    round_num: int = 0,
    source: str = "prefetch_list",
) -> None:
    """
    Seed verified directory listing facts from deterministic scanner output.

    :param task_state: Active task board
    :param relative_dir: Workspace-relative directory path
    :param listing: Directory listing text
    :param agent_id: Agent or subsystem that produced the listing
    :param round_num: Orchestration round index
    :param source: Fact source label
    """
    if not relative_dir or not listing.strip():
        return
    task_state.add_fact(
        TaskFact(
            id=f"fact_{uuid.uuid4().hex[:10]}",
            source=source,
            kind="directory_listing",
            path=relative_dir,
            content=listing,
            verified=True,
            agent_id=agent_id,
            round_num=round_num,
        )
    )


def record_tool_result_as_fact(
    task_state: TaskState | None,
    tool_name: str,
    arguments: str,
    output: str,
    success: bool,
    agent_id: str,
    round_num: int,
) -> None:
    """
    Record a tool execution result on the shared task board.

    :param task_state: Active task board or None to skip
    :param tool_name: Executed tool name
    :param arguments: JSON-encoded tool arguments
    :param output: Tool output text
    :param success: Whether execution succeeded
    :param agent_id: Agent role identifier
    :param round_num: Orchestration round index
    """
    if task_state is None or not success:
        return

    try:
        parsed_arguments = json.loads(arguments)
    except json.JSONDecodeError:
        parsed_arguments = {}

    path = parsed_arguments.get("path")
    if isinstance(path, str):
        path = path.strip() or None
    else:
        path = None

    kind = "tool_output"
    content = output
    verified = success

    if tool_name == "read_file" and path:
        kind = "file_content"
        verified = success and not output.lower().startswith("file not found")
    elif tool_name == "write_file" and path:
        kind = "file_written"
    elif tool_name == "list_directory" and path:
        kind = "directory_listing"
    elif tool_name == "run_command":
        kind = "command_output"
        command = parsed_arguments.get("command")
        path = str(command) if command else None
    elif tool_name == "search_files":
        kind = "search_results"
        query = parsed_arguments.get("query")
        path = str(query) if query else None

    task_state.add_fact(
        TaskFact(
            id=f"fact_{uuid.uuid4().hex[:10]}",
            source=tool_name,
            kind=kind,
            path=path,
            content=content,
            verified=verified,
            agent_id=agent_id,
            round_num=round_num,
        )
    )


def check_completion(task_state: TaskState) -> CompletionReport:
    """
    Evaluate whether verified facts satisfy the task completion criteria.

    :param task_state: Active task board
    :return: Completion report
    """
    if task_state.task_type == TaskType.READ_AND_DISPLAY:
        file_targets = [
            target
            for target in task_state.targets
            if target and "." in target.rsplit("/", 1)[-1]
        ]
        if not file_targets:
            file_targets = [
                fact.path
                for fact in task_state.verified_facts("file_content")
                if fact.path
            ]
        missing = [
            target
            for target in file_targets
            if task_state.fact_content_for_path(target) is None
        ]
        if missing:
            return CompletionReport(
                complete=False,
                reason="Missing verified file content",
                missing=missing,
            )
        if file_targets:
            return CompletionReport(complete=True)
        if task_state.verified_facts("file_content"):
            return CompletionReport(complete=True)
        return CompletionReport(
            complete=False,
            reason="No verified file content collected",
        )

    if task_state.task_type == TaskType.WRITE_FILES:
        written = {
            fact.path
            for fact in task_state.verified_facts("file_written")
            if fact.path
        }
        if task_state.targets and not written:
            return CompletionReport(
                complete=False,
                reason="No verified writes recorded",
                missing=list(task_state.targets),
            )
        return CompletionReport(complete=True)

    if task_state.task_type == TaskType.WRITE_THEN_READ:
        written = {
            fact.path
            for fact in task_state.verified_facts("file_written")
            if fact.path
        }
        file_targets = [
            target
            for target in task_state.targets
            if target and "." in target.rsplit("/", 1)[-1]
        ]
        if not file_targets:
            file_targets = sorted(written)
        missing_writes = [
            target for target in file_targets if target not in written
        ]
        if missing_writes:
            return CompletionReport(
                complete=False,
                reason="Missing verified writes before read-back",
                missing=missing_writes,
            )
        missing_reads = [
            target
            for target in file_targets
            if task_state.fact_content_for_path(target) is None
        ]
        if missing_reads:
            return CompletionReport(
                complete=False,
                reason="Missing verified file content after write",
                missing=missing_reads,
            )
        return CompletionReport(complete=True)

    if task_state.task_type == TaskType.WORKFLOW:
        written = {
            fact.path
            for fact in task_state.verified_facts("file_written")
            if fact.path
        }
        edited = {
            fact.path
            for fact in task_state.verified_facts("file_edited")
            if fact.path
        }
        file_targets = [
            target
            for target in task_state.targets
            if target and "." in target.rsplit("/", 1)[-1]
        ]
        if not file_targets:
            file_targets = sorted(written | edited)
        missing_writes = [
            target for target in file_targets if target not in written
        ]
        if missing_writes:
            return CompletionReport(
                complete=False,
                reason="Missing verified writes before read-back",
                missing=missing_writes,
            )
        missing_reads = [
            target
            for target in file_targets
            if task_state.fact_content_for_path(target) is None
        ]
        if missing_reads:
            return CompletionReport(
                complete=False,
                reason="Missing verified file content after write",
                missing=missing_reads,
            )
        intent = detect_workspace_intent(
            task_state.interpreted_request or task_state.user_request,
        )
        agenda = build_workspace_agenda(
            task_state.interpreted_request or task_state.user_request,
            intent,
        )
        edit_paths = [
            step.path
            for step in agenda
            if step.action == AgendaAction.EDIT_FILE and step.path
        ]
        missing_edits = [path for path in edit_paths if path not in edited]
        if missing_edits:
            return CompletionReport(
                complete=False,
                reason="Missing verified file edits",
                missing=missing_edits,
            )
        derived_steps = [
            step
            for step in agenda
            if step.action == AgendaAction.WRITE_DERIVED_FILE and step.source_path
        ]
        if derived_steps:
            written_all = {
                fact.path
                for fact in task_state.verified_facts("file_written")
                if fact.path
            }
            for step in derived_steps:
                parent = str(Path(step.source_path).parent)
                prefix = f"{parent}/" if parent not in {"", "."} else ""
                has_derived = any(
                    path.endswith(".txt")
                    and path.startswith(prefix)
                    and path in written_all
                    for path in written_all
                )
                if not has_derived:
                    return CompletionReport(
                        complete=False,
                        reason="Missing derived .txt file from H1 content",
                        missing=[f"{step.source_path} → *.txt"],
                    )
        return CompletionReport(complete=True)

    if task_state.task_type == TaskType.LIST_DIRECTORY:
        if task_state.verified_facts("directory_listing"):
            return CompletionReport(complete=True)
        return CompletionReport(
            complete=False,
            reason="No directory listing collected",
        )

    if task_state.task_type == TaskType.RUN_COMMAND:
        if task_state.verified_facts("command_output"):
            return CompletionReport(complete=True)
        return CompletionReport(
            complete=False,
            reason="No command output collected",
        )

    return CompletionReport(complete=True)


def normalize_discussion_text(content: str, *, max_length: int = 600) -> str:
    """
    Normalize agent discussion text for repetition checks.

    :param content: Raw agent message text
    :param max_length: Maximum normalized length
    :return: Collapsed lowercase text
    """
    text = re.sub(r"\s+", " ", (content or "").strip().lower())
    return text[:max_length]


def discussion_similarity(left: str, right: str) -> float:
    """
    Estimate lexical similarity between two discussion messages.

    :param left: First message text
    :param right: Second message text
    :return: Jaccard similarity score between 0.0 and 1.0
    """
    left_words = set(normalize_discussion_text(left).split())
    right_words = set(normalize_discussion_text(right).split())
    if not left_words or not right_words:
        left_norm = normalize_discussion_text(left)
        right_norm = normalize_discussion_text(right)
        return 1.0 if left_norm and left_norm == right_norm else 0.0
    union = left_words | right_words
    if not union:
        return 0.0
    return len(left_words & right_words) / len(union)


def discussion_entry_is_repeat(
    agent_name: str,
    content: str,
    transcript: list[str],
    *,
    threshold: float = REPETITION_SIMILARITY_THRESHOLD,
) -> bool:
    """
    Return True when an agent message substantially repeats prior transcript content.

    :param agent_name: Display name of the speaking agent
    :param content: Candidate message body
    :param transcript: Current discussion transcript lines
    :param threshold: Similarity threshold treated as a repeat
    :return: Whether the message should be treated as repetitive
    """
    normalized = normalize_discussion_text(content)
    if len(normalized) < MIN_REPETITION_TEXT_LENGTH:
        return False

    prefix = f"{agent_name}:"
    for entry in transcript:
        if not entry.startswith(prefix):
            continue
        prior_body = entry.split(": ", 1)[-1] if ": " in entry else entry
        if normalized == normalize_discussion_text(prior_body):
            return True
        if discussion_similarity(content, prior_body) >= threshold:
            return True
    return False


def increment_weak_retry(task_state: TaskState | None, role_id: str | None) -> int:
    """
    Track weak-output retries for one agent role.

    :param task_state: Active task board or None
    :param role_id: Agent role identifier
    :return: Updated retry count
    """
    if task_state is None or not role_id:
        return 0
    count = task_state.weak_retry_counts.get(role_id, 0) + 1
    task_state.weak_retry_counts[role_id] = count
    return count


def build_escalation_message(
    task_state: TaskState,
    role_id: str,
    *,
    reason: str = "",
) -> str:
    """
    Build a user-facing escalation message after repeated weak agent output.

    :param task_state: Active task board
    :param role_id: Agent role that failed to produce usable output
    :param reason: Optional extra context for the user
    :return: Escalation message body without the [ASK_USER] prefix
    """
    targets = ", ".join(task_state.targets) if task_state.targets else "unknown"
    detail = reason.strip() or f"The {role_id} could not verify the requested workspace action."
    return (
        f"I could not complete the task after {MAX_WEAK_RETRIES} attempts. "
        f"{detail} "
        f"Please clarify or confirm the target path(s): {targets}."
    )


def format_inter_round_memory_block(task_state: TaskState | None) -> str:
    """
    Format persisted facts from earlier turns for the current prompt.

    :param task_state: Active task board or None
    :return: Inter-round memory block or empty string
    """
    if task_state is None:
        return ""

    lines: list[str] = []
    if task_state.prior_summary:
        lines.append(f"Previous request in this chat: {task_state.prior_summary}")
    if task_state.prior_targets:
        lines.append("Previous workspace targets: " + ", ".join(task_state.prior_targets))

    prior_facts = [
        fact for fact in task_state.facts if fact.agent_id.startswith("prior:")
    ][-MAX_PRIOR_FACTS_IN_PROMPT:]
    if prior_facts:
        lines.append("Previous verified facts:")
        for fact in prior_facts:
            label = fact.path or fact.kind
            preview = fact.content.replace("\n", " ")[:120]
            lines.append(f"- {label}: {preview}")

    if not lines:
        return ""
    return "Inter-round task memory:\n" + "\n".join(lines)


def format_role_output_schema(role_id: str, task_type: TaskType) -> str:
    """
    Return role-specific response structure instructions.

    :param role_id: Agent role identifier
    :param task_type: Classified task type
    :return: Prompt guidance or empty string
    """
    if role_id == "developer":
        if task_type == TaskType.READ_AND_DISPLAY:
            return (
                "\n\nResponse format:\n"
                "ACTION: read_file\n"
                "RESULT: success|missing|error\n"
                "CONTENT: quote the file text verbatim"
            )
        if task_type == TaskType.WRITE_FILES:
            return (
                "\n\nResponse format:\n"
                "ACTION: write_file|run_command\n"
                "RESULT: list each created or updated path\n"
                "NOTES: one short sentence per file"
            )
        if task_type == TaskType.WRITE_THEN_READ:
            return (
                "\n\nResponse format:\n"
                "ACTION: write_file then read_file\n"
                "RESULT: created paths, then quoted file content\n"
                "NOTES: never invent content; read back from disk"
            )
        if task_type == TaskType.WORKFLOW:
            return (
                "\n\nResponse format:\n"
                "ACTION: write_file, read_file, then edit_file when scheduled\n"
                "RESULT: created paths, quoted read-back, then edit confirmation\n"
                "NOTES: follow the numbered agenda order exactly"
            )
        return (
            "\n\nResponse format:\n"
            "ACTION: tool used\n"
            "RESULT: outcome\n"
            "NOTES: concise summary for the team"
        )

    if role_id == "reviewer":
        return (
            "\n\nResponse format:\n"
            "VERDICT: pass|fail\n"
            "REASON: compare team claims against task-board facts\n"
            "NOTES: brief actionable feedback only"
        )

    if role_id == "project_manager":
        return (
            "\n\nResponse format:\n"
            "STATUS: on_track|blocked|ready\n"
            "NEXT: next assignee and action\n"
            "BLOCKERS: missing facts or open questions"
        )

    if role_id in {"software_tester", "security"}:
        return (
            "\n\nResponse format:\n"
            "FINDINGS: bullet list\n"
            "SEVERITY: low|medium|high\n"
            "RECOMMENDATION: one concrete next step"
        )

    return ""


def build_pm_verification_block(
    task_state: TaskState,
    completion: CompletionReport,
) -> str:
    """
    Build a deterministic PM verification summary from task-board facts.

    :param task_state: Active task board
    :param completion: Completion report for the current task
    :return: Verification block for transcript and synthesis
    """
    lines = [
        "PM verification (facts-based):",
        f"VERDICT: {'pass' if completion.complete else 'fail'}",
    ]
    if completion.complete:
        lines.append(f"Task `{task_state.task_type.value}` meets completion criteria.")
    else:
        lines.append(f"Reason: {completion.reason}")
        if completion.missing:
            lines.append("Missing: " + ", ".join(completion.missing))

    verified = task_state.verified_facts()
    if verified:
        lines.append("Verified facts:")
        for fact in verified[-6:]:
            label = fact.path or fact.kind
            preview = fact.content.replace("\n", " ")[:100]
            lines.append(f"- {fact.kind} {label}: {preview}")
    else:
        lines.append("Verified facts: none")

    return "\n".join(lines)


def format_task_plan_block(task_state: TaskState) -> str:
    """
    Format the PM task plan for prompts and transcript entries.

    :param task_state: Active task board
    :return: Human-readable plan block
    """
    lines = [
        f"Task type: {task_state.task_type.value}",
    ]
    if task_state.prompt_corrections:
        lines.append("Prompt normalization:")
        for correction in task_state.prompt_corrections:
            lines.append(
                f'- "{correction["original"]}" -> "{correction["corrected"]}"'
            )
    if task_state.targets:
        lines.append("Targets: " + ", ".join(task_state.targets))
    processing_content = task_state.interpreted_request or task_state.user_request
    agenda_block = format_agenda_block(
        build_workspace_agenda(
            processing_content,
            detect_workspace_intent(processing_content),
        )
    )
    if agenda_block:
        lines.append(agenda_block)
    compound_block = format_compound_plan_block(
        build_compound_plan(
            processing_content,
            detect_workspace_intent(processing_content),
        )
    )
    if compound_block:
        lines.append(compound_block)
    else:
        lines.append("Plan:")
        for step in task_state.plan_steps:
            lines.append(
                f"{step.step_id}. [{step.assignee}] {step.action} — {step.detail}"
            )
        return "\n".join(lines)
    lines.append("Role assignments:")
    for step in task_state.plan_steps:
        lines.append(
            f"{step.step_id}. [{step.assignee}] {step.action} — {step.detail}"
        )
    return "\n".join(lines)


def format_task_board_block(task_state: TaskState | None) -> str:
    """
    Format verified facts for injection into agent system prompts.

    :param task_state: Active task board or None
    :return: Prompt block or empty string
    """
    if task_state is None:
        return ""

    sections: list[str] = []
    inter_round = format_inter_round_memory_block(task_state)
    if inter_round:
        sections.append(inter_round)

    lines = ["Shared task board (verified facts only):"]
    if task_state.prior_summary:
        lines.append(f"Previous user request: {task_state.prior_summary}")
    if task_state.prior_targets:
        lines.append("Previous targets: " + ", ".join(task_state.prior_targets))

    current_facts = [
        fact for fact in task_state.facts if not fact.agent_id.startswith("prior:")
    ]
    recent_facts = current_facts[-MAX_FACTS_IN_PROMPT:]
    if not recent_facts:
        lines.append("(no verified facts yet)")
        sections.append("\n".join(lines))
        return "\n\n".join(sections)

    for fact in recent_facts:
        label = fact.path or fact.kind
        preview = fact.content.replace("\n", " ")[:160]
        status = "verified" if fact.verified else "unverified"
        lines.append(
            f"- [{fact.id}] {fact.source}/{fact.kind} {label} ({status}, {fact.agent_id}): {preview}"
        )
    sections.append("\n".join(lines))
    return "\n\n".join(sections)


def build_final_response_from_task_state(task_state: TaskState) -> str:
    """
    Build a user-facing final answer from verified task-board facts.

    :param task_state: Active task board
    :return: Final response text or empty string
    """
    if task_state.task_type == TaskType.READ_AND_DISPLAY:
        blocks: list[str] = []
        seen_paths: set[str] = set()
        for fact in task_state.verified_facts("file_content"):
            if not fact.path or fact.path in seen_paths:
                continue
            seen_paths.add(fact.path)
            blocks.append(f"Datei `{fact.path}`:\n\n```\n{fact.content}\n```")
        if blocks:
            return "\n\n".join(blocks)

        errors = [
            fact
            for fact in task_state.facts
            if fact.kind == "file_error" and fact.path
        ]
        if errors:
            return "\n\n".join(f"**{fact.path}**\n{fact.content}" for fact in errors)
        return ""

    if task_state.task_type == TaskType.WRITE_FILES:
        written = task_state.verified_facts("file_written")
        if written:
            lines = ["Created or updated files:"]
            for fact in written:
                if fact.path:
                    lines.append(f"- {fact.path}")
            return "\n".join(lines)

    if task_state.task_type == TaskType.WRITE_THEN_READ:
        blocks: list[str] = []
        seen_paths: set[str] = set()
        for fact in task_state.verified_facts("file_content"):
            if not fact.path or fact.path in seen_paths:
                continue
            seen_paths.add(fact.path)
            blocks.append(f"Datei `{fact.path}`:\n\n```\n{fact.content}\n```")
        if blocks:
            return "\n\n".join(blocks)

    if task_state.task_type == TaskType.WORKFLOW:
        blocks: list[str] = []
        seen_paths: set[str] = set()
        for fact in task_state.verified_facts("file_content"):
            if not fact.path or fact.path in seen_paths:
                continue
            seen_paths.add(fact.path)
            blocks.append(f"Datei `{fact.path}`:\n\n```\n{fact.content}\n```")
        edited = task_state.verified_facts("file_edited")
        if edited:
            edit_lines = ["Applied edits:"]
            for fact in edited:
                if fact.path:
                    edit_lines.append(f"- {fact.path}: {fact.content}")
            blocks.append("\n".join(edit_lines))
        if blocks:
            return "\n\n".join(blocks)

    if task_state.task_type == TaskType.LIST_DIRECTORY:
        listings = task_state.verified_facts("directory_listing")
        if listings:
            return listings[-1].content

    if task_state.task_type == TaskType.RUN_COMMAND:
        outputs = task_state.verified_facts("command_output")
        if outputs:
            return outputs[-1].content

    return ""


async def load_task_board_memory(chat_id: str) -> dict[str, Any] | None:
    """
    Load the persisted task-board snapshot for a chat.

    :param chat_id: Chat session ID
    :return: Parsed payload or None
    """
    entry = await memory_store.get_entry(chat_id, "chat", TASK_BOARD_MEMORY_KEY)
    if not entry:
        return None
    try:
        payload = json.loads(entry)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_relative_path(path: str | None) -> str:
    """
    Normalize a workspace-relative path for comparisons.

    :param path: Raw path string
    :return: Normalized relative path without leading slashes
    """
    return (path or "").strip().lstrip("/")


def _path_matches_fact(step_path: str | None, fact_path: str | None) -> bool:
    """
    Return True when a verified fact path belongs to an agenda step path.

    :param step_path: Target path for the plan step
    :param fact_path: Verified fact path
    :return: Whether the paths match exactly or nest under the step path
    """
    normalized_step = _normalize_relative_path(step_path)
    normalized_fact = _normalize_relative_path(fact_path)
    if not normalized_step or not normalized_fact:
        return False
    return (
        normalized_fact == normalized_step
        or normalized_fact.startswith(f"{normalized_step}/")
    )


def _path_from_plan_detail(detail: str) -> str | None:
    """
    Extract the first workspace-relative path embedded in a plan detail string.

    :param detail: Human-readable plan step detail
    :return: Workspace-relative path or None
    """
    match = re.search(
        r"(GitHub/[^\s,]+|[A-Za-z0-9_.-]+/[^\s,]+)",
        detail or "",
    )
    if not match:
        return None
    return match.group(1).strip().rstrip(").")


def _agenda_path_for_plan_step(
    task_state: TaskState,
    plan_step: TaskPlanStep,
) -> str | None:
    """
    Resolve the canonical path for one plan step.

    :param task_state: Active task board
    :param plan_step: Plan step entry
    :return: Workspace-relative path when known
    """
    processing_content = task_state.interpreted_request or task_state.user_request
    intent = detect_workspace_intent(processing_content)
    agenda = build_workspace_agenda(processing_content, intent)
    for agenda_step in agenda:
        if agenda_step.step_id == plan_step.step_id:
            return agenda_step.path
    return _path_from_plan_detail(plan_step.detail)


def _step_is_complete(
    action: str,
    path: str | None,
    task_state: TaskState,
) -> bool:
    """
    Determine whether one plan step has verified completion facts.

    :param action: Plan step action name
    :param path: Workspace-relative target path
    :param task_state: Active task board
    :return: True when the step is complete
    """
    if action == "create_directory":
        if not path:
            return False
        for kind in ("file_written", "file_content", "file_edited"):
            for fact in task_state.verified_facts(kind):
                if _path_matches_fact(path, fact.path):
                    return True
        return False
    if action == "write_file":
        if not path:
            return bool(task_state.verified_facts("file_written"))
        return any(
            _normalize_relative_path(fact.path) == _normalize_relative_path(path)
            for fact in task_state.verified_facts("file_written")
            if fact.path
        )
    if action == "read_file":
        if path:
            return task_state.fact_content_for_path(path) is not None
        return bool(task_state.verified_facts("file_content"))
    if action == "edit_file":
        if not path:
            return bool(task_state.verified_facts("file_edited"))
        return any(
            _normalize_relative_path(fact.path) == _normalize_relative_path(path)
            for fact in task_state.verified_facts("file_edited")
            if fact.path
        )
    if action == "write_derived_file":
        processing_content = task_state.interpreted_request or task_state.user_request
        intent = detect_workspace_intent(processing_content)
        agenda = build_workspace_agenda(processing_content, intent)
        derived_steps = [
            step
            for step in agenda
            if step.action == AgendaAction.WRITE_DERIVED_FILE and step.source_path
        ]
        if not derived_steps:
            return True
        written_all = {
            fact.path
            for fact in task_state.verified_facts("file_written")
            if fact.path
        }
        for step in derived_steps:
            parent = str(Path(step.source_path).parent)
            prefix = f"{parent}/" if parent not in {"", "."} else ""
            if not any(
                candidate.endswith(".txt")
                and candidate.startswith(prefix)
                and candidate in written_all
                for candidate in written_all
            ):
                return False
        return True
    if action == "list_directory":
        return bool(task_state.verified_facts("directory_listing"))
    if action in {"run_command", "execute_command"}:
        return bool(task_state.verified_facts("command_output"))
    if action in {
        "verify_content",
        "verify_on_disk",
        "present_to_user",
        "analyze",
    }:
        return check_completion(task_state).complete
    return False


def build_task_board_ui_payload(task_state: TaskState) -> dict[str, Any]:
    """
    Build a frontend-friendly task-board snapshot for WebSocket updates.

    :param task_state: Active task board
    :return: Serializable task-board payload
    """
    completion = check_completion(task_state)
    steps_payload: list[dict[str, Any]] = []
    active_assigned = False

    for plan_step in task_state.plan_steps:
        path = _agenda_path_for_plan_step(task_state, plan_step)
        done = _step_is_complete(plan_step.action, path, task_state)
        if done:
            status = "done"
        elif not active_assigned:
            status = "active"
            active_assigned = True
        else:
            status = "pending"
        steps_payload.append(
            {
                "step_id": plan_step.step_id,
                "action": plan_step.action,
                "assignee": plan_step.assignee,
                "detail": plan_step.detail,
                "path": path,
                "status": status,
            }
        )

    return {
        "type": "task_board_updated",
        "task_type": task_state.task_type.value,
        "complete": completion.complete,
        "reason": completion.reason,
        "targets": list(task_state.targets),
        "steps": steps_payload,
    }


async def emit_task_board_update(
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    task_state: TaskState | None,
) -> None:
    """
    Push the current task-board snapshot to the UI when a callback is available.

    :param on_event: Optional WebSocket event callback
    :param task_state: Active task board or None
    """
    if on_event is None or task_state is None or not task_state.plan_steps:
        return
    await on_event(build_task_board_ui_payload(task_state))


async def persist_task_board(
    chat_id: str,
    task_state: TaskState,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    """
    Persist the current task-board snapshot for follow-up turns.

    :param chat_id: Chat session ID
    :param task_state: Active task board
    :param on_event: Optional WebSocket callback for UI updates
    """
    payload = json.dumps(task_state.to_persisted_payload(), ensure_ascii=False)
    await memory_store.set_entry(chat_id, "chat", TASK_BOARD_MEMORY_KEY, payload)
    await emit_task_board_update(on_event, task_state)
