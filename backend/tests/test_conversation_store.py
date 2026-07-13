"""Tests for chat and message persistence."""

import aiosqlite
import pytest

from agentforge.models.schemas import ChatCreate, ChatMemorySettings, ChatUpdate, MessageRole
from agentforge.storage.conversation_store import ConversationStore


@pytest.fixture
def store(temp_data_dir) -> ConversationStore:
    """Initialized conversation store with temp database."""
    db_path = temp_data_dir / "conversations.db"
    return ConversationStore(db_path)


@pytest.fixture
async def initialized_store(store: ConversationStore) -> ConversationStore:
    """Store with database schema created."""
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_create_and_list_chats(initialized_store: ConversationStore) -> None:
    """Chats can be created and listed."""
    store = initialized_store
    chat = await store.create_chat(
        ChatCreate(title="Test Chat", mode="single", role_ids=[], memory=ChatMemorySettings())
    )
    chats = await store.list_chats()
    assert len(chats) == 1
    assert chats[0].id == chat.id
    assert chats[0].title == "Test Chat"
    assert chats[0].execution_strategy.value == "auto"


@pytest.mark.asyncio
async def test_update_chat_title(initialized_store: ConversationStore) -> None:
    """Chat metadata can be updated."""
    store = initialized_store
    chat = await store.create_chat(
        ChatCreate(title="Old", mode="single", role_ids=[], memory=ChatMemorySettings())
    )
    updated = await store.update_chat(chat.id, ChatUpdate(title="New Title"))
    assert updated.title == "New Title"


@pytest.mark.asyncio
async def test_update_chat_execution_strategy(initialized_store: ConversationStore) -> None:
    """Chat execution strategy can be updated and persisted."""
    store = initialized_store
    chat = await store.create_chat(
        ChatCreate(title="Strategy", mode="single", role_ids=[], memory=ChatMemorySettings())
    )
    updated = await store.update_chat(chat.id, ChatUpdate(execution_strategy="hybrid"))
    assert updated.execution_strategy.value == "hybrid"


@pytest.mark.asyncio
async def test_add_and_list_messages(initialized_store: ConversationStore) -> None:
    """Messages are stored and retrieved in order."""
    store = initialized_store
    chat = await store.create_chat(
        ChatCreate(title="Msg Chat", mode="single", role_ids=[], memory=ChatMemorySettings())
    )
    await store.add_message(chat.id, MessageRole.USER, "Hello")
    await store.add_message(chat.id, MessageRole.ASSISTANT, "Hi")
    messages = await store.list_messages(chat.id)
    assert len(messages) == 2
    assert messages[0].content == "Hello"
    assert messages[1].content == "Hi"


@pytest.mark.asyncio
async def test_delete_chat_cascades_messages(initialized_store: ConversationStore) -> None:
    """Deleting a chat removes its messages."""
    store = initialized_store
    chat = await store.create_chat(
        ChatCreate(title="Delete Me", mode="single", role_ids=[], memory=ChatMemorySettings())
    )
    await store.add_message(chat.id, MessageRole.USER, "x")
    await store.delete_chat(chat.id)
    with pytest.raises(KeyError):
        await store.get_chat(chat.id)
    assert await store.list_messages(chat.id) == []


@pytest.mark.asyncio
async def test_initialize_adds_execution_strategy_column_for_legacy_db(
    store: ConversationStore,
) -> None:
    """Store initialization upgrades legacy chat schema with strategy column."""
    async with aiosqlite.connect(store.db_path) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                role_ids TEXT NOT NULL DEFAULT '[]',
                memory TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                agent_id TEXT,
                agent_name TEXT,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                chat_id TEXT,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tokens_estimate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.commit()

    await store.initialize()

    async with aiosqlite.connect(store.db_path) as db:
        cursor = await db.execute("PRAGMA table_info(chats)")
        rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    assert "execution_strategy" in columns
