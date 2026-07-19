"""SQLite persistence for chats and messages."""

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from agentforge.config import settings
from agentforge.models.schemas import (
    ChatCreate,
    ChatMemorySettings,
    ChatResponse,
    ChatUpdate,
    ExecutionStrategy,
    MessageResponse,
    MessageRole,
    OrchestrationMode,
)


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class ConversationStore:
    """Persistent storage for chats and messages."""

    def __init__(self, db_path=None) -> None:
        """Initialize store with database path."""
        self.db_path = str(db_path or settings.db_path)

    async def _ensure_chat_columns(self, db: aiosqlite.Connection) -> None:
        """Ensure latest chat schema columns exist for older databases."""
        cursor = await db.execute("PRAGMA table_info(chats)")
        rows = await cursor.fetchall()
        existing_columns = {row[1] for row in rows}
        if "execution_strategy" not in existing_columns:
            await db.execute(
                "ALTER TABLE chats ADD COLUMN execution_strategy TEXT NOT NULL DEFAULT 'auto'"
            )
        if "grill_enabled" not in existing_columns:
            await db.execute(
                "ALTER TABLE chats ADD COLUMN grill_enabled INTEGER NOT NULL DEFAULT 0"
            )

    async def initialize(self) -> None:
        """Create database tables if they do not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    execution_strategy TEXT NOT NULL DEFAULT 'auto',
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
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
            await self._ensure_chat_columns(db)
            await db.commit()

    async def create_chat(self, data: ChatCreate) -> ChatResponse:
        """Create a new chat session."""
        chat_id = str(uuid.uuid4())
        now = _utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO chats (
                    id, title, mode, execution_strategy, role_ids, memory, grill_enabled,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    data.title,
                    data.mode.value,
                    data.execution_strategy.value,
                    json.dumps(data.role_ids),
                    data.memory.model_dump_json(),
                    1 if data.grill_enabled else 0,
                    now,
                    now,
                ),
            )
            await db.commit()
        return await self.get_chat(chat_id)

    async def list_chats(self) -> list[ChatResponse]:
        """List all chats ordered by last update."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM chats ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()
        return [self._row_to_chat(row) for row in rows]

    async def get_chat(self, chat_id: str) -> ChatResponse:
        """Fetch a single chat by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM chats WHERE id = ?", (chat_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"Chat {chat_id} not found")
        return self._row_to_chat(row)

    async def update_chat(self, chat_id: str, data: ChatUpdate) -> ChatResponse:
        """Update chat metadata."""
        chat = await self.get_chat(chat_id)
        title = data.title if data.title is not None else chat.title
        mode = data.mode.value if data.mode is not None else chat.mode.value
        execution_strategy = (
            data.execution_strategy.value
            if data.execution_strategy is not None
            else chat.execution_strategy.value
        )
        role_ids = data.role_ids if data.role_ids is not None else chat.role_ids
        memory = data.memory if data.memory is not None else chat.memory
        grill_enabled = (
            data.grill_enabled if data.grill_enabled is not None else chat.grill_enabled
        )
        now = _utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE chats
                SET title = ?, mode = ?, execution_strategy = ?, role_ids = ?, memory = ?,
                    grill_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    mode,
                    execution_strategy,
                    json.dumps(role_ids),
                    memory.model_dump_json(),
                    1 if grill_enabled else 0,
                    now,
                    chat_id,
                ),
            )
            await db.commit()
        return await self.get_chat(chat_id)

    async def delete_chat(self, chat_id: str) -> None:
        """Delete a chat and its messages."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            await db.execute("DELETE FROM memory_entries WHERE chat_id = ?", (chat_id,))
            await db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            await db.commit()

    async def add_message(
        self,
        chat_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        metadata: dict | None = None,
    ) -> MessageResponse:
        """Append a message to a chat."""
        message_id = str(uuid.uuid4())
        now = _utcnow().isoformat()
        meta = json.dumps(metadata or {})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO messages
                (id, chat_id, role, agent_id, agent_name, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    chat_id,
                    role.value,
                    agent_id,
                    agent_name,
                    content,
                    meta,
                    now,
                ),
            )
            await db.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?",
                (now, chat_id),
            )
            await db.commit()
        return MessageResponse(
            id=message_id,
            chat_id=chat_id,
            role=role,
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
        )

    async def list_messages(self, chat_id: str) -> list[MessageResponse]:
        """List messages for a chat."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC",
                (chat_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    def _row_to_chat(self, row) -> ChatResponse:
        """Convert database row to ChatResponse."""
        grill_enabled = False
        if "grill_enabled" in row.keys():
            grill_enabled = bool(row["grill_enabled"])
        elif row["mode"] == OrchestrationMode.GRILL.value:
            grill_enabled = True
        return ChatResponse(
            id=row["id"],
            title=row["title"],
            mode=OrchestrationMode(row["mode"]),
            execution_strategy=ExecutionStrategy(row["execution_strategy"]),
            role_ids=json.loads(row["role_ids"]),
            memory=ChatMemorySettings.model_validate_json(row["memory"]),
            grill_enabled=grill_enabled,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_message(self, row) -> MessageResponse:
        """Convert database row to MessageResponse."""
        return MessageResponse(
            id=row["id"],
            chat_id=row["chat_id"],
            role=MessageRole(row["role"]),
            agent_id=row["agent_id"],
            agent_name=row["agent_name"],
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


conversation_store = ConversationStore()
