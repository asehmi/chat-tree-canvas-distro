# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Local SQLite persistence for insecure_agent_provider.py's chat history.

This giveaway only ever runs against a single local file — no cloud
backend, no adapter abstraction, one straightforward aiosqlite connection
covering table creation, round allocation, and history reads/writes.

`chat_tree/providers/base.py`'s documented reference contract requires a
`text` field on every history row, both roles — `load_history()` below
emits it consistently for both `user` and `assistant` rows.

Rows are keyed by `node_id` — chat-tree-canvas's own per-exchange key, the
same one the in-memory `HISTORY` dict this module replaces used to be
keyed by. Deliberately not called "session_id": that name is already
taken by `chat_tree/dag_store.py`'s own, unrelated `sessions.session_id`
in `canvas.db` — a different ID space entirely (one canvas session holds
many nodes). Reusing the name here caused real confusion once, mixing up
which database a copied ID actually belonged to.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("insecure_agent.db")

DB_PATH = Path(__file__).resolve().parent.parent / ".db" / "insecure_agent_chat.db"

MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT    NOT NULL,
    user_id     TEXT,
    role        TEXT    NOT NULL CHECK(role IN ('user', 'assistant')),
    content     TEXT,
    tool_calls  TEXT,
    round       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

MESSAGES_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_messages_node
ON messages (node_id, created_at)
"""

_db: aiosqlite.Connection | None = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("insecure_agent.db not initialised — call await init() first.")
    return _db


async def init() -> None:
    global _db
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute(MESSAGES_DDL)
    # Migrate pre-rename DBs: the per-node key was originally called
    # "session_id", colliding in name (though not in meaning) with
    # chat_tree/dag_store.py's own unrelated sessions.session_id in
    # canvas.db — confusing enough in practice to be worth a rename.
    async with _db.execute("PRAGMA table_info(messages)") as cursor:
        cols = {row[1] for row in await cursor.fetchall()}
    if "session_id" in cols and "node_id" not in cols:
        await _db.execute("ALTER TABLE messages RENAME COLUMN session_id TO node_id")
    await _db.execute(MESSAGES_INDEX_DDL)
    await _db.commit()
    logger.info(f"[DB] connected: {DB_PATH}")


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def start_round(node_id: str, user_message: str, user_id: str) -> int:
    """Persist the user turn and return its freshly-allocated round number.

    Round allocation and the INSERT are one statement, so two concurrent
    requests on the same node can never mint the same round — the read-
    MAX-then-insert-later split this avoids could, and a duplicate round
    would corrupt delete_round()'s `WHERE round = ?` filter.
    """
    db = _conn()
    async with db.execute(
        """
        INSERT INTO messages (node_id, user_id, role, content, round)
        VALUES (?, ?, 'user', ?, (SELECT COALESCE(MAX(round), -1) + 1 FROM messages WHERE node_id = ?))
        RETURNING round
        """,
        (node_id, user_id, user_message, node_id),
    ) as cursor:
        row = await cursor.fetchone()
    await db.commit()
    return row[0]


async def finish_round(
    node_id: str,
    round_num: int,
    assistant_stored: dict[str, Any],
    user_id: str,
) -> None:
    """Persist the assistant turn for a round opened by start_round()."""
    db = _conn()
    tool_calls_json = json.dumps({
        "mode": assistant_stored.get("mode"),
        "result": assistant_stored.get("result", {}),
        "events": assistant_stored.get("events", []),
        "interrupted": assistant_stored.get("interrupted", False),
    })
    await db.execute(
        "INSERT INTO messages (node_id, user_id, role, content, tool_calls, round) "
        "VALUES (?, ?, 'assistant', ?, ?, ?)",
        (node_id, user_id, assistant_stored.get("text", ""), tool_calls_json, round_num),
    )
    await db.commit()


async def load_history(node_id: str) -> list[dict[str, Any]]:
    """Message rows for one node, in the shape chat_tree/providers/base.py
    documents: {"role": "user", "text": str} and
    {"role": "assistant", "text": str, "events": [...], "result": {...},
    "mode": str, "round": int, "interrupted": bool}.
    """
    db = _conn()
    async with db.execute(
        "SELECT role, content, tool_calls, round FROM messages "
        "WHERE node_id = ? ORDER BY round, created_at",
        (node_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    messages: list[dict[str, Any]] = []
    for role, content, tool_calls_raw, round_num in rows:
        if role == "user":
            messages.append({"role": "user", "text": content or "", "round": round_num})
            continue

        msg: dict[str, Any] = {"role": "assistant", "text": content or "", "round": round_num}
        if tool_calls_raw:
            try:
                stored = json.loads(tool_calls_raw)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                msg["mode"] = stored.get("mode")
                msg["result"] = stored.get("result", {})
                msg["events"] = stored.get("events", [])
                msg["interrupted"] = stored.get("interrupted", False)
        messages.append(msg)
    return messages


async def delete_round(node_id: str, round_num: int) -> None:
    """Delete one round's user+assistant messages. Silently a no-op if
    the round doesn't exist — same behavior as a plain SQL DELETE."""
    db = _conn()
    await db.execute(
        "DELETE FROM messages WHERE node_id = ? AND round = ?",
        (node_id, round_num),
    )
    await db.commit()
