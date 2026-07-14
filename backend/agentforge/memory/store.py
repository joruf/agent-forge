"""Context memory for agents."""

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from agentforge.config import settings
from agentforge.models.schemas import ChatMemorySettings


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return max(1, len(text) // 4)


class MemoryStore:
    """Token-budgeted memory for chat and global scope."""

    def __init__(self, db_path=None) -> None:
        """Initialize memory store."""
        self.db_path = str(db_path or settings.db_path)

    async def get_context(
        self,
        chat_id: str,
        memory_settings: ChatMemorySettings,
    ) -> str:
        """Build memory context string within token budget."""
        if not memory_settings.enabled:
            return ""

        scopes = ["chat"]
        entries: list[tuple[str, str, str]] = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            for scope in scopes:
                if scope == "chat":
                    cursor = await db.execute(
                        """
                        SELECT key, value, updated_at FROM memory_entries
                        WHERE scope = ? AND chat_id = ?
                        ORDER BY updated_at DESC
                        """,
                        (scope, chat_id),
                    )
                else:
                    cursor = await db.execute(
                        """
                        SELECT key, value, updated_at FROM memory_entries
                        WHERE scope = ?
                        ORDER BY updated_at DESC
                        """,
                        (scope,),
                    )
                rows = await cursor.fetchall()
                for row in rows:
                    entries.append((scope, row["key"], row["value"]))

        budget = memory_settings.memory_tokens
        used = 0
        lines: list[str] = []
        for scope, key, value in entries:
            line = f"[{scope}] {key}: {value}"
            cost = _estimate_tokens(line)
            if used + cost > budget:
                break
            lines.append(line)
            used += cost

        if not lines:
            return ""
        return "Persistent memory:\n" + "\n".join(lines)

    async def set_entry(
        self,
        chat_id: str | None,
        scope: str,
        key: str,
        value: str,
    ) -> None:
        """Store or update a memory entry."""
        now = datetime.now(timezone.utc).isoformat()
        tokens = _estimate_tokens(value)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id FROM memory_entries
                WHERE scope = ? AND key = ? AND (chat_id = ? OR (chat_id IS NULL AND ? IS NULL))
                """,
                (scope, key, chat_id, chat_id),
            )
            existing = await cursor.fetchone()
            if existing:
                await db.execute(
                    """
                    UPDATE memory_entries
                    SET value = ?, tokens_estimate = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (value, tokens, now, existing[0]),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO memory_entries
                    (id, chat_id, scope, key, value, tokens_estimate, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        chat_id,
                        scope,
                        key,
                        value,
                        tokens,
                        now,
                        now,
                    ),
                )
            await db.commit()

    async def list_entries(self, chat_id: str | None = None) -> list[dict]:
        """List memory entries for inspection."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if chat_id:
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_entries
                    WHERE chat_id = ? OR scope = 'global'
                    ORDER BY updated_at DESC
                    """,
                    (chat_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM memory_entries ORDER BY updated_at DESC"
                )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]


memory_store = MemoryStore()
