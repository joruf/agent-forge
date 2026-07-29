"""Deterministic gate: decide whether a user message requires workspace agent action."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from agentforge.agents.workspace_intent import WorkspaceIntent, detect_workspace_intent
from agentforge.models.schemas import OrchestrationMode

_ACTION_VERBS = re.compile(
    r"\b("
    r"run|fix|create|write|read|list|build|deploy|search|implement|debug|"
    r"erstell|schreib|speicher|generier|anleg|bearbeit|edit|update|fix"
    r")\b",
    re.IGNORECASE,
)

_PATH_OR_EXTENSION = re.compile(
    r"[/\\]|\.(?:py|js|ts|php|html|txt|md|json|yaml|yml|css|vue|sql|sh)\b",
    re.IGNORECASE,
)

_GREETING = re.compile(
    r"^(?:"
    r"hi|hello|hey|hallo|moin|servus|"
    r"guten\s+(?:tag|morgen|abend)|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"howdy|yo|sup|what'?s\s+up"
    r")(?:[!?.]|$|\s+)",
    re.IGNORECASE,
)

_ACKNOWLEDGMENT = re.compile(
    r"^(?:"
    r"danke|thanks|thank\s+you|thx|"
    r"ok(?:ay)?|cool|nice|great|perfekt|super|alles\s+klar"
    r")(?:[!?.]|$|\s+)",
    re.IGNORECASE,
)

_QUESTION_START = re.compile(
    r"^(?:"
    r"was|wie|warum|wann|wo|wer|welche|welcher|welches|"
    r"why|how|what|who|when|where|which|"
    r"is|are|can|could|do|does|did"
    r")\b",
    re.IGNORECASE,
)


class ActionCategory(str, Enum):
    """Classification of user message action requirement."""

    WORKSPACE_TASK = "workspace_task"
    CONVERSATIONAL = "conversational"
    ACKNOWLEDGMENT = "acknowledgment"
    INFORMATIONAL = "informational"
    EMPTY = "empty"


@dataclass(frozen=True)
class ActionRequirementResult:
    """Outcome of the action-requirement gate."""

    requires_action: bool
    category: ActionCategory
    reason: str


def _is_question(text: str) -> bool:
    """
    Return True when the message reads as an informational question.

    :param text: Trimmed user message
    :return: Whether the message is question-shaped
    """
    if text.endswith("?"):
        return True
    return _QUESTION_START.match(text) is not None


def _is_short_non_action(text: str) -> bool:
    """
    Return True for very short messages without paths or action verbs.

    :param text: Trimmed user message
    :return: Whether the message is short and non-actionable
    """
    if len(text.split()) > 3:
        return False
    if _PATH_OR_EXTENSION.search(text):
        return False
    if _ACTION_VERBS.search(text):
        return False
    return True


def analyze_action_requirement(
    user_content: str,
    *,
    intent: WorkspaceIntent | None = None,
    mode: OrchestrationMode | None = None,
) -> ActionRequirementResult:
    """
    Decide whether orchestration should run workspace tools and multi-agent rounds.

    Deterministic heuristic gate — no LLM call.

    :param user_content: Raw user message
    :param intent: Optional pre-parsed workspace intent
    :param mode: Orchestration mode (affects informational question handling)
    :return: Gate decision with category and log-friendly reason
    """
    text = (user_content or "").strip()
    if not text:
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.EMPTY,
            reason="Empty or whitespace-only message",
        )

    parsed = intent or detect_workspace_intent(text)
    if parsed.requires_tools:
        return ActionRequirementResult(
            requires_action=True,
            category=ActionCategory.WORKSPACE_TASK,
            reason="Workspace tools required for detected intent",
        )

    if _ACKNOWLEDGMENT.match(text):
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.ACKNOWLEDGMENT,
            reason="Acknowledgment or brief affirmation",
        )

    if _GREETING.match(text):
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.CONVERSATIONAL,
            reason="Greeting or casual message",
        )

    if _is_question(text):
        if mode in (OrchestrationMode.SINGLE, OrchestrationMode.QUICK):
            return ActionRequirementResult(
                requires_action=True,
                category=ActionCategory.INFORMATIONAL,
                reason="Informational question in single-agent mode",
            )
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.INFORMATIONAL,
            reason="Informational question without workspace intent",
        )

    if _is_short_non_action(text):
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.CONVERSATIONAL,
            reason="Short non-action message",
        )

    if len(text) <= 120 and not _ACTION_VERBS.search(text) and not _PATH_OR_EXTENSION.search(text):
        return ActionRequirementResult(
            requires_action=False,
            category=ActionCategory.CONVERSATIONAL,
            reason="Non-action message without workspace intent",
        )

    return ActionRequirementResult(
        requires_action=True,
        category=ActionCategory.WORKSPACE_TASK,
        reason="Message appears to require agent action",
    )
