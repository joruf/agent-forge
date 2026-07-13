"""Tests for agent memory store."""

import pytest

from agentforge.memory.store import MemoryStore
from agentforge.models.schemas import ChatMemorySettings


@pytest.fixture
async def memory_db(temp_data_dir) -> MemoryStore:
    """Memory store backed by initialized schema."""
    from agentforge.storage.conversation_store import ConversationStore

    db_path = temp_data_dir / "memory.db"
    conv = ConversationStore(db_path)
    await conv.initialize()
    return MemoryStore(db_path)

@pytest.mark.asyncio
async def test_memory_disabled_returns_empty(memory_db: MemoryStore) -> None:
    """Disabled memory produces no context string."""
    settings = ChatMemorySettings(enabled=False, memory_tokens=8000, memory_scope="chat")
    context = await memory_db.get_context("chat-1", settings)
    assert context == ""


@pytest.mark.asyncio
async def test_chat_scope_memory(memory_db: MemoryStore) -> None:
    """Chat-scoped entries appear in context."""
    await memory_db.set_entry("chat-1", "chat", "pref", "dark mode")
    settings = ChatMemorySettings(enabled=True, memory_tokens=8000, memory_scope="chat")
    context = await memory_db.get_context("chat-1", settings)
    assert "pref: dark mode" in context


@pytest.mark.asyncio
async def test_global_memory_included(memory_db: MemoryStore) -> None:
    """Global scope entries are included when configured."""
    await memory_db.set_entry(None, "global", "user_name", "Joachim")
    settings = ChatMemorySettings(enabled=True, memory_tokens=8000, memory_scope="global")
    context = await memory_db.get_context("chat-2", settings)
    assert "user_name: Joachim" in context


@pytest.mark.asyncio
async def test_memory_respects_token_budget(memory_db: MemoryStore) -> None:
    """Context is empty when a single entry exceeds the token budget."""
    await memory_db.set_entry("chat-3", "chat", "a", "x" * 200)
    within = ChatMemorySettings(enabled=True, memory_tokens=100, memory_scope="chat")
    over = ChatMemorySettings(enabled=True, memory_tokens=10, memory_scope="chat")
    assert "a:" in await memory_db.get_context("chat-3", within)
    assert await memory_db.get_context("chat-3", over) == ""
