"""Unified user clarification / choice dialog support."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

from agentforge.agents.approval_manager import approval_manager
from agentforge.models.schemas import (
    AgendaResumeState,
    ApprovalResumeState,
    OrchestrationResumeState,
    UserChoiceOption,
)


class ClarificationKind(StrEnum):
    """Kind of blocker that requires user clarification."""

    MISSING_CONTENT_TAG = "missing_content_tag"
    AGENT_BLOCKED = "agent_blocked"
    AGENT_QUESTION = "agent_question"
    WORKFLOW_INCOMPLETE = "workflow_incomplete"
    GENERIC = "generic"


CLARIFICATION_PENDING_PREFIX = "[CLARIFICATION_PENDING]"

STANDARD_RETRY = UserChoiceOption(
    id="retry",
    label="Retry",
    description="Try the failed step again.",
)
STANDARD_SKIP = UserChoiceOption(
    id="skip",
    label="Skip this step",
    description="Continue without completing this step.",
)
STANDARD_ABORT = UserChoiceOption(
    id="abort",
    label="Abort",
    description="Stop the current workflow.",
)
STANDARD_CUSTOM = UserChoiceOption(
    id="custom_reply",
    label="Provide answer",
    description="Enter a custom clarification or instruction.",
)


def build_clarification_options(
    kind: ClarificationKind | str,
    context: dict[str, Any] | None = None,
) -> list[UserChoiceOption]:
    """
    Build selectable recovery options for a clarification kind.

    :param kind: Clarification category
    :param context: Kind-specific context (tags, paths, etc.)
    :return: Options shown in the user-choice dialog
    """
    ctx = context or {}
    kind_value = kind.value if isinstance(kind, ClarificationKind) else str(kind)

    if kind_value == ClarificationKind.MISSING_CONTENT_TAG:
        requested_tag = str(ctx.get("requested_tag", "h2"))
        available_tags = list(ctx.get("available_tags") or [])
        options: list[UserChoiceOption] = []
        for tag in available_tags:
            if tag == requested_tag:
                continue
            options.append(
                UserChoiceOption(
                    id=f"use_{tag}",
                    label=f"Use <{tag}> instead",
                    description=f"Write file content from the <{tag}> element.",
                ),
            )
        options.extend([STANDARD_SKIP, STANDARD_ABORT])
        return options

    if kind_value == ClarificationKind.AGENT_BLOCKED:
        return [STANDARD_RETRY, STANDARD_CUSTOM, STANDARD_ABORT]

    if kind_value == ClarificationKind.AGENT_QUESTION:
        return [STANDARD_CUSTOM, STANDARD_RETRY, STANDARD_ABORT]

    if kind_value == ClarificationKind.WORKFLOW_INCOMPLETE:
        return [STANDARD_RETRY, STANDARD_SKIP, STANDARD_CUSTOM, STANDARD_ABORT]

    return [STANDARD_RETRY, STANDARD_CUSTOM, STANDARD_ABORT]


def allows_custom_input_for_kind(kind: ClarificationKind | str) -> bool:
    """
    Return whether the clarification kind supports a free-text reply.

    :param kind: Clarification category
    :return: True when a custom text field should be shown
    """
    kind_value = kind.value if isinstance(kind, ClarificationKind) else str(kind)
    return kind_value in {
        ClarificationKind.AGENT_BLOCKED,
        ClarificationKind.AGENT_QUESTION,
        ClarificationKind.WORKFLOW_INCOMPLETE,
        ClarificationKind.GENERIC,
    }


async def request_clarification(
    chat_id: str,
    kind: ClarificationKind | str,
    question: str,
    options: list[UserChoiceOption] | None,
    resume_state: (
        AgendaResumeState
        | OrchestrationResumeState
        | ApprovalResumeState
        | dict[str, Any]
    ),
    on_event: Callable | None,
    *,
    allows_custom_input: bool | None = None,
) -> str:
    """
    Pause orchestration and ask the user to choose a recovery action.

    :param chat_id: Chat session ID
    :param kind: Clarification category
    :param question: User-facing question text
    :param options: Optional explicit options; defaults are built from kind
    :param resume_state: Continuation state stored until the user responds
    :param on_event: Optional WebSocket event callback
    :param allows_custom_input: Override free-text support for this dialog
    :return: Approval request ID
    """
    kind_value = kind.value if isinstance(kind, ClarificationKind) else str(kind)
    context: dict[str, Any] = {}
    if isinstance(resume_state, OrchestrationResumeState):
        context = dict(resume_state.context)
    elif isinstance(resume_state, AgendaResumeState):
        context = {
            "step_index": resume_state.step_index,
            "step_path": resume_state.step_path,
            "requested_tag": resume_state.requested_tag,
            "content_source_path": resume_state.content_source_path,
        }

    resolved_options = options or build_clarification_options(kind_value, context)
    custom_allowed = (
        allows_custom_input
        if allows_custom_input is not None
        else allows_custom_input_for_kind(kind_value)
    )
    payload = {
        "kind": kind_value,
        "question": question,
        "options": [option.model_dump() for option in resolved_options],
        "allows_custom_input": custom_allowed,
        "context": context,
    }
    approval_id = await approval_manager.request(
        chat_id,
        "user_choice",
        question,
        payload,
    )
    approval_manager.set_resume_state(approval_id, resume_state)

    if on_event:
        await on_event({
            "type": "user_choice_pending",
            "approval_id": approval_id,
            "kind": kind_value,
            "question": question,
            "options": payload["options"],
            "allows_custom_input": custom_allowed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return approval_id


def clarification_pending_marker() -> str:
    """
    Return the sentinel value indicating a clarification dialog was opened.

    :return: Marker string consumed by orchestration callers
    """
    return CLARIFICATION_PENDING_PREFIX


def is_clarification_pending(content: str) -> bool:
    """
    Check whether agent output indicates a pending clarification dialog.

    :param content: Agent loop output
    :return: True when orchestration should pause for user choice
    """
    return content.strip().startswith(CLARIFICATION_PENDING_PREFIX)
