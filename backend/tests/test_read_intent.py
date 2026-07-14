"""Tests for read-file workspace intent and read task summaries."""

from pathlib import Path

import pytest

from agentforge.agents.orchestrator import AgentOrchestrator
from agentforge.agents.workspace_executor import (
    build_read_task_summary,
    prefetch_read_file_contents,
    resolve_read_file_paths,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


READ_PROMPT = (
    "lese den dateiinhalt von /home/joruf/Dokumente/GitHub/Test12/test12345.txt "
    "und liste mir den inhalt hier auf"
)


def test_detect_read_intent_not_file_creation() -> None:
    """Read requests must not be classified as file creation."""
    intent = detect_workspace_intent(READ_PROMPT)

    assert intent.wants_file_read is True
    assert intent.wants_file_creation is False
    assert intent.requires_tools is True
    assert "GitHub/Test12/test12345.txt" in intent.target_paths


def test_detect_write_intent_still_works() -> None:
    """Explicit save requests remain write intents."""
    prompt = "Erstelle index.php und speichere unter /home/joruf/Dokumente/GitHub/Test"
    intent = detect_workspace_intent(prompt)

    assert intent.wants_file_creation is True
    assert intent.wants_file_read is False


def test_is_weak_discussion_content_rejects_status_json() -> None:
    """Status-only JSON placeholders are treated as weak output."""
    assert AgentOrchestrator._is_weak_discussion_content('{"status": "success"}') is True
    assert AgentOrchestrator._is_weak_discussion_content('{"status": "error", "message": "x"}') is True


def test_resolve_read_file_paths_from_absolute_path() -> None:
    """Absolute read paths resolve to workspace-relative file paths."""
    intent = detect_workspace_intent(READ_PROMPT)
    paths = resolve_read_file_paths(READ_PROMPT, intent)

    assert paths == ["GitHub/Test12/test12345.txt"]


@pytest.mark.asyncio
async def test_prefetch_read_file_contents(monkeypatch, tmp_path: Path) -> None:
    """Pre-read returns verified file content from disk."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    target = tmp_path / "GitHub" / "Test12"
    target.mkdir(parents=True)
    file_path = target / "test12345.txt"
    file_path.write_text("Hello World", encoding="utf-8")

    intent = detect_workspace_intent(
        f"lese den dateiinhalt von {file_path} und liste mir den inhalt hier auf"
    )
    contents = await prefetch_read_file_contents("", intent)

    assert contents["GitHub/Test12/test12345.txt"] == "Hello World"


def test_build_read_task_summary_formats_user_response(monkeypatch, tmp_path: Path) -> None:
    """Read summary quotes file content for the final user response."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    target = tmp_path / "GitHub" / "Test12"
    target.mkdir(parents=True)
    file_path = target / "test12345.txt"
    file_path.write_text("Hello World", encoding="utf-8")

    prompt = (
        f"lese den dateiinhalt von {file_path} "
        "und liste mir den inhalt hier auf"
    )
    intent = detect_workspace_intent(prompt)
    summary = build_read_task_summary(
        prompt,
        intent,
        {"GitHub/Test12/test12345.txt": "Hello World"},
    )

    assert "GitHub/Test12/test12345.txt" in summary
    assert "Hello World" in summary


@pytest.mark.asyncio
async def test_multi_agent_read_uses_prefetched_summary(monkeypatch, tmp_path: Path) -> None:
    """Multi-agent read requests return verified file content to the user."""
    from agentforge.models.schemas import ChatCreate, ChatMemorySettings
    from agentforge.storage.conversation_store import conversation_store

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    target = tmp_path / "GitHub" / "Test12"
    target.mkdir(parents=True)
    file_path = target / "test12345.txt"
    file_path.write_text("Hello World", encoding="utf-8")
    prompt = (
        f"lese den dateiinhalt von {file_path} "
        "und liste mir den inhalt hier auf"
    )

    chat = await conversation_store.create_chat(
        ChatCreate(
            title="Read test",
            mode="multi",
            role_ids=["developer", "reviewer"],
            memory=ChatMemorySettings(),
        )
    )
    orchestrator = AgentOrchestrator()

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
            return ("Done.", {"model": "ollama/mock-pm", "role_id": "project_manager"})
        if agent_id == "developer":
            return ('{"status": "success"}', {"model": "ollama/mock-dev", "role_id": "developer"})
        if agent_id == "reviewer":
            return ("Looks good.", {"model": "ollama/mock-review", "role_id": "reviewer"})
        raise AssertionError(agent_id)

    monkeypatch.setattr(AgentOrchestrator, "_agent_loop", fake_agent_loop)

    result = await orchestrator.run(
        chat.id,
        prompt,
        mode="multi",
        role_ids=["developer", "reviewer"],
    )

    assert result.messages
    assert "Hello World" in result.messages[-1].content
