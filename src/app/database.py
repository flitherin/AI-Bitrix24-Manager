from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import Conversation, RequestFields

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    chat_id INTEGER PRIMARY KEY,
    telegram_user_id INTEGER NOT NULL,
    username TEXT,
    fields_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT,
    qualified INTEGER NOT NULL DEFAULT 0,
    crm_entity TEXT,
    crm_id INTEGER,
    crm_contact_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES conversations(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id_id
ON messages(chat_id, id);
"""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON")
        await self._connection.execute("PRAGMA journal_mode = WAL")
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def get_or_create_conversation(
        self,
        chat_id: int,
        telegram_user_id: int,
        username: str | None,
    ) -> Conversation:
        now = _utc_now()
        await self.connection.execute(
            """
            INSERT INTO conversations (
                chat_id, telegram_user_id, username, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                telegram_user_id = excluded.telegram_user_id,
                username = excluded.username
            """,
            (chat_id, telegram_user_id, username, now, now),
        )
        await self.connection.commit()
        conversation = await self.get_conversation(chat_id)
        if conversation is None:
            raise RuntimeError("Failed to create conversation")
        return conversation

    async def get_conversation(self, chat_id: int) -> Conversation | None:
        cursor = await self.connection.execute(
            "SELECT * FROM conversations WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return self._to_conversation(row) if row else None

    async def reset_conversation(
        self,
        chat_id: int,
        telegram_user_id: int,
        username: str | None,
    ) -> Conversation:
        await self.connection.execute(
            "DELETE FROM conversations WHERE chat_id = ?", (chat_id,)
        )
        await self.connection.commit()
        return await self.get_or_create_conversation(chat_id, telegram_user_id, username)

    async def get_history(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        cursor = await self.connection.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content
                FROM messages
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def save_turn(
        self,
        chat_id: int,
        user_text: str | None,
        assistant_text: str | None,
        fields: RequestFields,
        summary: str | None,
        qualified: bool,
        crm_entity: str | None = None,
    ) -> None:
        now = _utc_now()
        await self.connection.execute("BEGIN IMMEDIATE")
        try:
            await self.connection.execute(
                """
                UPDATE conversations SET
                    fields_json = ?,
                    summary = COALESCE(?, summary),
                    qualified = CASE WHEN ? = 1 THEN 1 ELSE qualified END,
                    crm_entity = COALESCE(?, crm_entity),
                    updated_at = ?
                WHERE chat_id = ?
                """,
                (
                    fields.model_dump_json(),
                    summary,
                    int(qualified),
                    crm_entity,
                    now,
                    chat_id,
                ),
            )
            if user_text is not None:
                await self._insert_message(chat_id, "user", user_text, now)
            if assistant_text is not None:
                await self._insert_message(chat_id, "assistant", assistant_text, now)
            await self.connection.commit()
        except Exception:
            await self.connection.rollback()
            raise

    async def record_message(self, chat_id: int, role: str, content: str) -> None:
        await self._insert_message(chat_id, role, content, _utc_now())
        await self.connection.commit()

    async def set_crm_contact(self, chat_id: int, contact_id: int) -> None:
        await self.connection.execute(
            """
            UPDATE conversations
            SET crm_contact_id = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (contact_id, _utc_now(), chat_id),
        )
        await self.connection.commit()

    async def set_crm_created(
        self,
        chat_id: int,
        entity: str,
        crm_id: int,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE conversations SET
                qualified = 1,
                crm_entity = ?,
                crm_id = ?,
                updated_at = ?
            WHERE chat_id = ?
            """,
            (entity, crm_id, _utc_now(), chat_id),
        )
        await self.connection.commit()

    async def _insert_message(
        self,
        chat_id: int,
        role: str,
        content: str,
        now: str,
    ) -> None:
        await self.connection.execute(
            """
            INSERT INTO messages (chat_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, role, content, now),
        )

    @staticmethod
    def _to_conversation(row: aiosqlite.Row) -> Conversation:
        return Conversation(
            chat_id=row["chat_id"],
            telegram_user_id=row["telegram_user_id"],
            username=row["username"],
            fields=RequestFields.model_validate_json(row["fields_json"]),
            summary=row["summary"],
            qualified=bool(row["qualified"]),
            crm_entity=row["crm_entity"],
            crm_id=row["crm_id"],
            crm_contact_id=row["crm_contact_id"],
        )
