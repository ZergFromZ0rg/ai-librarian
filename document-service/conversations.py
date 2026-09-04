"""SQLite-backed store for Ask-mode conversations.

Kept in its own database file, separate from the document metadata store: a
conversation is rewritten on every answered turn, so keeping it out of
``library.db`` avoids lock contention with the indexing worker. Each row holds
the whole message list as a JSON blob — conversations are small and always read
or written as a unit.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    messages    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations (updated_at DESC);
"""

MAX_MESSAGES = 200
MAX_BLOB_BYTES = 5 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _title_from(messages: list, given: Optional[str]) -> str:
    if given and given.strip():
        return given.strip()[:200]
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user" and message.get("content"):
            return " ".join(str(message["content"]).split())[:200] or "New conversation"
    return "New conversation"


def _blob(messages) -> str:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    encoded = json.dumps(messages[-MAX_MESSAGES:], ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_BLOB_BYTES:
        raise ValueError("conversation is too large to store")
    return encoded


class ConversationStore:
    def __init__(self, db_path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row(self, conv_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()

    def list(self, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, model, created_at, updated_at, messages "
                "FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        summaries = []
        for row in rows:
            try:
                count = len(json.loads(row["messages"]))
            except (TypeError, ValueError):
                count = 0
            summaries.append({
                "id": row["id"], "title": row["title"], "model": row["model"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "message_count": count,
            })
        return summaries

    def get(self, conv_id: str) -> Optional[dict]:
        with self._lock:
            row = self._row(conv_id)
        if row is None:
            return None
        return {
            "id": row["id"], "title": row["title"], "model": row["model"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "messages": json.loads(row["messages"]),
        }

    def create(self, messages, model: Optional[str] = None, title: Optional[str] = None) -> dict:
        encoded = _blob(messages)
        conv_id, now = uuid.uuid4().hex[:12], _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, model, created_at, updated_at, messages) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, _title_from(messages, title), model, now, now, encoded),
            )
            self._conn.commit()
        return self.get(conv_id)

    def replace(self, conv_id: str, messages, model: Optional[str] = None,
                title: Optional[str] = None) -> Optional[dict]:
        encoded = _blob(messages)
        with self._lock:
            if self._row(conv_id) is None:
                return None
            self._conn.execute(
                "UPDATE conversations SET messages = ?, model = COALESCE(?, model), "
                "title = ?, updated_at = ? WHERE id = ?",
                (encoded, model, _title_from(messages, title), _now(), conv_id),
            )
            self._conn.commit()
        return self.get(conv_id)

    def delete(self, conv_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self._conn.commit()
            return cursor.rowcount > 0
