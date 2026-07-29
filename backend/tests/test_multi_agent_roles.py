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


def test_pm_final_synthesis_prompt_bounds_transcript() -> None:
    """PM final synthesis only sees the transcript tail, not the full history."""
    orchestrator = AgentOrchestrator()
    pm = role_registry.get_role("project_manager")
    assert pm is not None
    max_rounds = orchestrator._resolve_multi_rounds()
    long_transcript = [f"Developer: entry {i}" for i in range(30)]
    prompt = orchestrator._build_multi_prompt(
        pm,
        max_rounds - 1,
        "Baue eine Funktion",
        long_transcript,
    )
    assert "Final synthesis requested." in prompt
    assert "entry 29" in prompt
    assert "entry 0" not in prompt


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
async def test_multi_agent_developer_tools_then_reviewer(monkeypatch, tmp_path, chat_ready) -> None:
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


@pytest.mark.asyncio
async def test_multi_agent_hi_uses_conversational_path(monkeypatch, chat_ready) -> None:
    """Simple greetings skip full multi-agent orchestration and task board setup."""
    chat = await conversation_store.create_chat(
        ChatCreate(
            title="New Chat",
            mode="multi",
            role_ids=["project_manager", "developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()
    agent_loop_calls = {"count": 0}
    events: list[dict] = []

    async def fake_agent_loop(*args, **kwargs):
        agent_loop_calls["count"] += 1
        raise AssertionError("_agent_loop should not run for conversational greetings")

    async def fake_stream_llm_complete(
        self,
        llm,
        messages,
        on_event=None,
        **kwargs,
    ):
        return (
            "Hello! How can I help you today?",
            "ollama/mock-pm",
            [],
            False,
        )

    async def capture_event(payload: dict) -> None:
        events.append(payload)

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)
    monkeypatch.setattr(AgentOrchestrator, "_stream_llm_complete", fake_stream_llm_complete)

    result = await orchestrator.run(
        chat.id,
        "hi",
        OrchestrationMode.MULTI,
        ["project_manager", "developer", "reviewer"],
        on_event=capture_event,
    )

    assert agent_loop_calls["count"] == 0
    assert result.messages
    assert result.messages[-1].metadata.get("conversational_multi") is True
    assert result.messages[-1].metadata.get("action_gate", {}).get("category") == "conversational"
    assert "Hello" in result.messages[-1].content
    assert len(result.agent_discussions) == 1
    assert not any(event.get("type") == "task_board_updated" for event in events)
    assert any(
        event.get("type") == "action_gate_decision" and event.get("category") == "conversational"
        for event in events
    )


@pytest.mark.asyncio
async def test_multi_agent_danke_skips_agent_loop(monkeypatch, chat_ready) -> None:
    """Acknowledgments skip multi-agent orchestration."""
    chat = await conversation_store.create_chat(
        ChatCreate(
            title="New Chat",
            mode="multi",
            role_ids=["project_manager", "developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()
    agent_loop_calls = {"count": 0}

    async def fake_agent_loop(*args, **kwargs):
        agent_loop_calls["count"] += 1
        raise AssertionError("_agent_loop should not run for acknowledgments")

    async def fake_stream_llm_complete(self, llm, messages, on_event=None, **kwargs):
        return ("Gern geschehen!", "ollama/mock-pm", [], False)

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)
    monkeypatch.setattr(AgentOrchestrator, "_stream_llm_complete", fake_stream_llm_complete)

    result = await orchestrator.run(
        chat.id,
        "danke",
        OrchestrationMode.MULTI,
        ["project_manager", "developer", "reviewer"],
    )

    assert agent_loop_calls["count"] == 0
    assert result.messages[-1].metadata.get("action_gate", {}).get("category") == "acknowledgment"


@pytest.mark.asyncio
async def test_multi_agent_informational_question_skips_agent_loop(monkeypatch, chat_ready) -> None:
    """Informational questions skip multi-agent orchestration in MULTI mode."""
    chat = await conversation_store.create_chat(
        ChatCreate(
            title="New Chat",
            mode="multi",
            role_ids=["project_manager", "developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()
    agent_loop_calls = {"count": 0}

    async def fake_agent_loop(*args, **kwargs):
        agent_loop_calls["count"] += 1
        raise AssertionError("_agent_loop should not run for informational questions")

    async def fake_stream_llm_complete(self, llm, messages, on_event=None, **kwargs):
        return (
            "AgentForge is a multi-agent coding assistant.",
            "ollama/mock-pm",
            [],
            False,
        )

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)
    monkeypatch.setattr(AgentOrchestrator, "_stream_llm_complete", fake_stream_llm_complete)

    result = await orchestrator.run(
        chat.id,
        "was ist das?",
        OrchestrationMode.MULTI,
        ["project_manager", "developer", "reviewer"],
    )

    assert agent_loop_calls["count"] == 0
    assert result.messages[-1].metadata.get("action_gate", {}).get("category") == "informational"


@pytest.mark.asyncio
async def test_multi_agent_create_index_php_runs_agent_loop(monkeypatch, chat_ready) -> None:
    """Workspace tasks must not be blocked by the action gate."""
    chat = await conversation_store.create_chat(
        ChatCreate(
            title="New Chat",
            mode="multi",
            role_ids=["project_manager", "developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()
    agent_loop_calls = {"count": 0}

    async def fake_agent_loop(*args, **kwargs):
        agent_loop_calls["count"] += 1
        return (
            "Created index.php",
            {"model": "ollama/mock-dev", "role_id": "developer"},
        )

    async def fake_stream_llm_complete(self, llm, messages, on_event=None, **kwargs):
        return ("Please create index.php", "ollama/mock-pm", [], False)

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)
    monkeypatch.setattr(AgentOrchestrator, "_stream_llm_complete", fake_stream_llm_complete)

    await orchestrator.run(
        chat.id,
        "create index.php",
        OrchestrationMode.MULTI,
        ["project_manager", "developer", "reviewer"],
    )

    assert agent_loop_calls["count"] >= 1
