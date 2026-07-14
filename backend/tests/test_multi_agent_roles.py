"""Tests for multi-agent role collaboration."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.role_registry import role_registry
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import (
    ChatCreate,
    ChatMemorySettings,
    ExecutionStrategy,
    OrchestrationMode,
)
from agentforge.storage.conversation_store import conversation_store


def test_is_weak_discussion_content_detects_empty_json() -> None:
    """Placeholder JSON should not appear in team discussions."""
    assert AgentOrchestrator._is_weak_discussion_content("{}") is True
    assert AgentOrchestrator._is_weak_discussion_content('{"arguments": {}}') is True
    assert AgentOrchestrator._is_weak_discussion_content("Created index.php") is False


def test_finalize_agent_content_uses_tool_summary() -> None:
    """Successful tool calls replace empty developer replies."""
    content = AgentOrchestrator._finalize_agent_content(
        "{}",
        ["Created/updated file: GitHub/Test/index.php"],
    )
    assert "GitHub/Test/index.php" in content
    assert "Completed workspace actions" in content


def test_reviewer_multi_prompt_avoids_full_implementation() -> None:
    """Reviewer is instructed to review instead of implementing HTML."""
    orchestrator = AgentOrchestrator()
    reviewer = role_registry.get_role("reviewer")
    assert reviewer is not None
    prompt = orchestrator._build_multi_prompt(
        reviewer,
        0,
        "Erstelle ein PHP Programm mit Header und Footer",
        ["User request: Erstelle ein PHP Programm mit Header und Footer"],
    )
    assert "Do not generate full HTML" in prompt


def test_parallel_round_disabled_for_file_creation() -> None:
    """File creation requests run roles serially so specialists see tool results."""
    intent = detect_workspace_intent(
        "Speichere den Code unter /home/joruf/Dokumente/GitHub/Test"
    )
    assert intent.wants_file_creation is True
    assert (
        AgentOrchestrator._is_parallel_round(
            ExecutionStrategy.HYBRID,
            0,
            4,
            workspace_intent=intent,
        )
        is False
    )


@pytest.mark.asyncio
async def test_multi_agent_developer_tools_then_reviewer(monkeypatch, tmp_path) -> None:
    """Developer file writes are summarized; reviewer stays in review mode."""
    chat = await conversation_store.create_chat(
        ChatCreate(
            title="New Chat",
            mode="multi",
            role_ids=["developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()
    user_prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        f"{tmp_path}/GitHub/Test"
    )

    developer_calls = {"count": 0}
    reviewer_calls = {"count": 0}

    async def fake_agent_loop(
        self,
        chat_id: str,
        agent_id: str,
        agent_name: str,
        messages: list[dict],
        tools,
        memory_scope: str,
        on_event=None,
        user_content: str = "",
        role_id: str | None = None,
        mode_single: bool = False,
        mode_multi: bool = False,
        intervention_queue=None,
        workspace_intent=None,
        task_state=None,
        round_num=0,
        **kwargs,
    ):
        if agent_id == "project_manager":
            if "Final synthesis requested." in messages[1]["content"]:
                return (
                    "The Developer created GitHub/Test/index.php.",
                    {"model": "ollama/mock-pm", "role_id": "project_manager"},
                )
            return (
                "Developer, please create the PHP files with write_file.",
                {"model": "ollama/mock-pm", "role_id": "project_manager"},
            )
        if agent_id == "developer":
            developer_calls["count"] += 1
            return (
                AgentOrchestrator._finalize_agent_content(
                    "{}",
                    ["Created/updated file: GitHub/Test/index.php"],
                ),
                {"model": "ollama/mock-dev", "role_id": "developer"},
            )
        if agent_id == "reviewer":
            reviewer_calls["count"] += 1
            assert "Review the existing discussion only" in messages[1]["content"]
            assert "Created/updated file: GitHub/Test/index.php" in messages[1]["content"]
            return (
                "Developer created index.php. Add semantic HTML5 tags and separate CSS.",
                {"model": "ollama/mock-review", "role_id": "reviewer"},
            )
        raise AssertionError(f"Unexpected agent_id: {agent_id}")

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)

    result = await orchestrator.run(
        chat.id,
        user_prompt,
        OrchestrationMode.MULTI,
        ["developer", "reviewer"],
    )

    assert developer_calls["count"] >= 1
    assert reviewer_calls["count"] >= 1
    assert any(
        "index.php" in item.content
        for item in result.agent_discussions
        if item.from_agent in {"Developer", "Entwickler", "developer"}
        or "index.php" in item.content
    )
    assert any(
        "semantic HTML5" in item.content or "Developer created" in item.content
        for item in result.agent_discussions
    )
    assert result.messages
    assert "index.php" in result.messages[-1].content.lower() or result.messages[-1].content
