"""Tests for agent orchestration helpers."""

import asyncio
from datetime import datetime, timezone

from agentforge.agents.approval_manager import approval_manager
from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import (
    ApprovalResponse,
    ExecutionStrategy,
    MessageResponse,
    MessageRole,
    OrchestrationMode,
)
from agentforge.storage.conversation_store import conversation_store


def test_parse_content_tool_calls_remember() -> None:
    """Embedded JSON tool instructions are parsed for execution."""
    content = (
        '{"function": "remember", "arguments": {"key": "today", "value": "Sunday"}}'
    )
    calls = AgentOrchestrator._parse_content_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "remember"
    assert '"key": "today"' in calls[0]["arguments"]


def test_parse_content_tool_calls_ignores_plain_text() -> None:
    """Plain assistant answers are not treated as tool calls."""
    calls = AgentOrchestrator._parse_content_tool_calls("Today is Sunday.")
    assert calls == []


def test_collect_interventions_appends_to_transcript() -> None:
    """Live user input is merged into the multi-agent transcript."""
    import asyncio

    orchestrator = AgentOrchestrator()
    transcript: list[str] = ["User request: build API"]
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("Please use FastAPI instead.")

    asyncio.run(
        orchestrator._collect_interventions(transcript, queue, None)
    )

    assert len(transcript) == 2
    assert "User (live input): Please use FastAPI instead." in transcript[1]


def test_resolve_execution_strategy_for_pr2_hybrid_mode() -> None:
    """PR2 enables hybrid multi-agent strategy for auto and parallel requests."""
    assert (
        AgentOrchestrator._resolve_execution_strategy(
            OrchestrationMode.SINGLE,
            ExecutionStrategy.AUTO,
        )
        == ExecutionStrategy.SERIAL
    )
    assert (
        AgentOrchestrator._resolve_execution_strategy(
            OrchestrationMode.QUICK,
            ExecutionStrategy.AUTO,
        )
        == ExecutionStrategy.SERIAL
    )
    assert (
        AgentOrchestrator._resolve_execution_strategy(
            OrchestrationMode.MULTI,
            ExecutionStrategy.AUTO,
        )
        == ExecutionStrategy.HYBRID
    )
    assert (
        AgentOrchestrator._resolve_execution_strategy(
            OrchestrationMode.MULTI,
            ExecutionStrategy.PARALLEL,
        )
        == ExecutionStrategy.HYBRID
    )
    assert (
        AgentOrchestrator._resolve_execution_strategy(
            OrchestrationMode.MULTI,
            ExecutionStrategy.SERIAL,
        )
        == ExecutionStrategy.SERIAL
    )


def test_parallel_role_classification() -> None:
    """Only read-only specialist roles are marked parallel-safe."""
    assert AgentOrchestrator._is_parallel_role("reviewer") is True
    assert AgentOrchestrator._is_parallel_role("security") is True
    assert AgentOrchestrator._is_parallel_role("researcher") is True
    assert AgentOrchestrator._is_parallel_role("developer") is False
    assert AgentOrchestrator._is_parallel_role("project_manager") is False


def test_parallel_round_rules() -> None:
    """Hybrid strategy runs parallel only before the final round."""
    assert (
        AgentOrchestrator._is_parallel_round(ExecutionStrategy.HYBRID, 0, 4) is True
    )
    assert (
        AgentOrchestrator._is_parallel_round(ExecutionStrategy.HYBRID, 3, 4) is False
    )
    assert (
        AgentOrchestrator._is_parallel_round(ExecutionStrategy.SERIAL, 1, 4) is False
    )
    intent = detect_workspace_intent("Speichere die Datei unter /tmp/demo")
    assert (
        AgentOrchestrator._is_parallel_round(
            ExecutionStrategy.HYBRID,
            0,
            4,
            workspace_intent=intent,
        )
        is False
    )


def test_prompt_needs_tools() -> None:
    """Tool attachment follows simple intent heuristics."""
    assert AgentOrchestrator._prompt_needs_tools("Wie heißt du?", "developer") is False
    assert AgentOrchestrator._prompt_needs_tools("Read main.py and fix the bug", "developer") is True


def test_execute_approved_command_denied_clears_resume_state() -> None:
    """Denied approval does not resume and removes stored continuation state."""
    orchestrator = AgentOrchestrator()
    chat_id = "deny-chat"
    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo denied",
            {"command": "echo denied", "cwd": None},
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "agent_id": "developer",
            "agent_name": "Developer",
            "role_id": "developer",
            "user_content": "Denied path",
            "mode_single": True,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock"},
            "messages": [],
            "tool_call_id": "call_deny",
        },
    )

    result = asyncio.run(
        orchestrator.execute_approved_command(
            chat_id,
            approval_id,
            ApprovalResponse(approved=False),
        )
    )

    assert result is None
    assert approval_manager.list_pending(chat_id) == []
    assert approval_manager.pop_resume_state(approval_id) is None


def test_execute_approved_command_resumes_and_returns_assistant(monkeypatch) -> None:
    """Approved command resumes flow and persists resumed assistant message."""
    orchestrator = AgentOrchestrator()
    chat_id = "approve-chat"
    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo resumed-ok",
            {"command": "echo resumed-ok", "cwd": None},
        )
    )
    approval_manager.set_resume_state(
        approval_id,
        {
            "chat_id": chat_id,
            "agent_id": "developer",
            "agent_name": "Developer",
            "role_id": "developer",
            "user_content": "Resume me",
            "mode_single": True,
            "memory_scope": "chat",
            "routing": {"model": "ollama/mock"},
            "messages": [],
            "tool_call_id": "call_resume",
        },
    )

    async def fake_resume(self, state, command_output):
        assert state.agent_id == "developer"
        assert "resumed-ok" in command_output
        return "Resumed assistant output.", {"model": "ollama/mock"}

    stored_messages: list[MessageResponse] = []

    async def fake_add_message(
        chat_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: dict | None = None,
    ) -> MessageResponse:
        message = MessageResponse(
            id=f"msg-{len(stored_messages) + 1}",
            chat_id=chat_id,
            role=role,
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        stored_messages.append(message)
        return message

    monkeypatch.setattr(AgentOrchestrator, "_resume_after_approval", fake_resume)
    monkeypatch.setattr(conversation_store, "add_message", fake_add_message)

    result = asyncio.run(
        orchestrator.execute_approved_command(
            chat_id,
            approval_id,
            ApprovalResponse(approved=True),
        )
    )

    assert result is not None
    assert result.role == MessageRole.ASSISTANT
    assert result.agent_id == "developer"
    assert result.content == "Resumed assistant output."
    assert result.metadata.get("resumed_from_approval") is True
    assert result.metadata.get("approval_id") == approval_id
    assert len(stored_messages) == 2
    assert stored_messages[0].role == MessageRole.TOOL
    assert stored_messages[1].role == MessageRole.ASSISTANT
    assert approval_manager.list_pending(chat_id) == []
    assert approval_manager.pop_resume_state(approval_id) is None


def test_execute_approved_command_without_resume_returns_tool_message(
    monkeypatch,
) -> None:
    """Approved command without resume state returns a tool message fallback."""
    orchestrator = AgentOrchestrator()
    chat_id = "approve-no-resume"
    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo no-resume",
            {"command": "echo no-resume", "cwd": None},
        )
    )

    stored_messages: list[MessageResponse] = []

    async def fake_add_message(
        chat_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: dict | None = None,
    ) -> MessageResponse:
        message = MessageResponse(
            id=f"msg-{len(stored_messages) + 1}",
            chat_id=chat_id,
            role=role,
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        stored_messages.append(message)
        return message

    monkeypatch.setattr(conversation_store, "add_message", fake_add_message)

    result = asyncio.run(
        orchestrator.execute_approved_command(
            chat_id,
            approval_id,
            ApprovalResponse(approved=True),
        )
    )

    assert result is not None
    assert result.role == MessageRole.TOOL
    assert "Command executed: echo no-resume" in result.content
    assert result.metadata.get("approval_id") == approval_id
    assert len(stored_messages) == 1
    assert stored_messages[0].role == MessageRole.TOOL
    assert approval_manager.list_pending(chat_id) == []
    assert approval_manager.pop_resume_state(approval_id) is None


def test_execute_approved_command_invalid_resume_state_writes_error_message(
    monkeypatch,
) -> None:
    """Invalid resume state returns a clear assistant error message."""
    orchestrator = AgentOrchestrator()
    chat_id = "approve-invalid-resume"
    approval_id = asyncio.run(
        approval_manager.request(
            chat_id,
            "command",
            "Execute command: echo invalid-state",
            {"command": "echo invalid-state", "cwd": None},
        )
    )
    approval_manager._resume_states[approval_id] = {"unexpected": "payload"}

    stored_messages: list[MessageResponse] = []

    async def fake_add_message(
        chat_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: dict | None = None,
    ) -> MessageResponse:
        message = MessageResponse(
            id=f"msg-{len(stored_messages) + 1}",
            chat_id=chat_id,
            role=role,
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        stored_messages.append(message)
        return message

    monkeypatch.setattr(conversation_store, "add_message", fake_add_message)

    result = asyncio.run(
        orchestrator.execute_approved_command(
            chat_id,
            approval_id,
            ApprovalResponse(approved=True),
        )
    )

    assert result is not None
    assert result.role == MessageRole.ASSISTANT
    assert result.metadata.get("resume_error") is True
    assert result.metadata.get("resume_error_type") == "invalid_state"
    assert "could not resume" in result.content.lower()
    assert len(stored_messages) == 2
    assert stored_messages[0].role == MessageRole.TOOL
    assert stored_messages[1].role == MessageRole.ASSISTANT
    assert approval_manager.list_pending(chat_id) == []
