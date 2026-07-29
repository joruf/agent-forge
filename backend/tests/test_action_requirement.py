"""Unit tests for the action requirement gate."""

import pytest

from agentforge.agents.action_requirement import (
    ActionCategory,
    analyze_action_requirement,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import OrchestrationMode


@pytest.mark.parametrize(
    ("message", "expected_category"),
    [
        ("", ActionCategory.EMPTY),
        ("   ", ActionCategory.EMPTY),
        ("hi", ActionCategory.CONVERSATIONAL),
        ("Hi!", ActionCategory.CONVERSATIONAL),
        ("hallo", ActionCategory.CONVERSATIONAL),
        ("guten tag", ActionCategory.CONVERSATIONAL),
        ("hey there", ActionCategory.CONVERSATIONAL),
        ("danke", ActionCategory.ACKNOWLEDGMENT),
        ("thanks!", ActionCategory.ACKNOWLEDGMENT),
        ("ok", ActionCategory.ACKNOWLEDGMENT),
    ],
)
def test_no_action_categories(message: str, expected_category: ActionCategory) -> None:
    """Greetings, acknowledgments, and empty input skip workspace action."""
    result = analyze_action_requirement(message, mode=OrchestrationMode.MULTI)
    assert result.category == expected_category
    assert result.requires_action is False


def test_workspace_task_requires_action() -> None:
    """Workspace intents always require action."""
    intent = detect_workspace_intent("create index.php")
    result = analyze_action_requirement(
        "create index.php",
        intent=intent,
        mode=OrchestrationMode.MULTI,
    )
    assert result.requires_action is True
    assert result.category == ActionCategory.WORKSPACE_TASK


def test_list_files_requires_action() -> None:
    """Directory listing requests require workspace tools."""
    result = analyze_action_requirement(
        "list files in src/",
        mode=OrchestrationMode.MULTI,
    )
    assert result.requires_action is True
    assert result.category == ActionCategory.WORKSPACE_TASK


@pytest.mark.parametrize(
    "message",
    [
        "was ist das?",
        "was ist AgentForge?",
        "how does this work?",
    ],
)
def test_informational_question_multi_skips_action(message: str) -> None:
    """Informational questions skip multi-agent orchestration in MULTI mode."""
    result = analyze_action_requirement(message, mode=OrchestrationMode.MULTI)
    assert result.category == ActionCategory.INFORMATIONAL
    assert result.requires_action is False


def test_informational_question_single_requires_action() -> None:
    """Informational questions still run in SINGLE mode."""
    result = analyze_action_requirement(
        "what is AgentForge?",
        mode=OrchestrationMode.SINGLE,
    )
    assert result.category == ActionCategory.INFORMATIONAL
    assert result.requires_action is True


def test_short_non_action_message() -> None:
    """Very short non-action messages are conversational."""
    result = analyze_action_requirement("sounds good", mode=OrchestrationMode.MULTI)
    assert result.category == ActionCategory.CONVERSATIONAL
    assert result.requires_action is False
