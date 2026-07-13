"""Tests for async chat title generation."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentforge.api.routes import (
    DEFAULT_CHAT_TITLE,
    _chat_needs_generated_title,
    _fallback_chat_title,
    _generate_chat_title,
)
from agentforge.models.schemas import ChatCreate, ChatMemorySettings
from agentforge.storage.conversation_store import conversation_store


def test_chat_needs_generated_title() -> None:
    """Title generation runs only for default titles on first send."""
    assert _chat_needs_generated_title(DEFAULT_CHAT_TITLE, False) is True
    assert _chat_needs_generated_title(DEFAULT_CHAT_TITLE, True) is False
    assert _chat_needs_generated_title("Existing Title", False) is False


def test_fallback_chat_title_collapses_whitespace() -> None:
    """Fallback titles use a trimmed excerpt of the user message."""
    title = _fallback_chat_title("  Erstelle   ein   PHP   Programm  ")
    assert title == "Erstelle ein PHP Programm"


@pytest.mark.asyncio
async def test_generate_chat_title_persists_and_returns(monkeypatch) -> None:
    """Generated titles are stored and returned to callers."""
    chat = await conversation_store.create_chat(
        ChatCreate(title=DEFAULT_CHAT_TITLE, mode="multi", role_ids=[], memory=ChatMemorySettings())
    )

    class FakeOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            self.llm = AsyncMock()
            self.llm.generate_title = AsyncMock(return_value="PHP Layout Entwurf")

    monkeypatch.setattr("agentforge.api.routes.AgentOrchestrator", FakeOrchestrator)

    title = await _generate_chat_title(chat.id, "Erstelle ein PHP Programm mit Header")
    stored = await conversation_store.get_chat(chat.id)

    assert title == "PHP Layout Entwurf"
    assert stored.title == "PHP Layout Entwurf"


@pytest.mark.asyncio
async def test_generate_chat_title_uses_fallback_on_error(monkeypatch) -> None:
    """Failed title generation still replaces the default chat title."""
    chat = await conversation_store.create_chat(
        ChatCreate(title=DEFAULT_CHAT_TITLE, mode="single", role_ids=[], memory=ChatMemorySettings())
    )

    class BrokenOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("LLM unavailable")

    monkeypatch.setattr("agentforge.api.routes.AgentOrchestrator", BrokenOrchestrator)

    prompt = "Multi agent test request"
    title = await _generate_chat_title(chat.id, prompt)
    stored = await conversation_store.get_chat(chat.id)

    assert title == prompt
    assert stored.title == prompt
