# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""SQLite store for the per-session tree of nodes.

The DB owns the *shape* of each canvas — sessions, nodes with positions and
prompts, and the edges linking them — tenanted by user_id. Node content
(responses) is never stored here: it lives server-side in whichever chat
backend is configured, and is re-hydrated via GET /history/{node_id}.

Terminology: a node_id keys one prompt/answer exchange. In this app a
"conversation" means a full root-to-leaf path through the tree; see
chat_tree/exporter.py.

Trees are small, so any structural change rewrites the session's rows
wholesale (delete + insert) rather than diffing.
"""

import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Dot-prefixed ON PURPOSE: Reflex's dev reload watcher excludes hidden
# directories by default (see reflex/utils/exec.py::is_excluded_by_default).
# A visible data/ dir here caused a hot reload on every DB write — each save
# reset backend state and clobbered in-flight geometry. Keep it ".db" or
# move it outside the project; never rename it to a visible directory.
# chat_tree/ sits directly under the repo root: two parents, not three.
DATA_DIR = Path(__file__).resolve().parent.parent / ".db"
DB_FILE = DATA_DIR / "canvas.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    session_id TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    w          REAL NOT NULL DEFAULT 0,
    h          REAL NOT NULL DEFAULT 0,
    prompt     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, node_id)
);
CREATE TABLE IF NOT EXISTS edges (
    session_id     TEXT NOT NULL,
    edge_id        TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    PRIMARY KEY (session_id, edge_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, updated_at);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)
        # Migrate pre-rename DBs: the per-node key was originally called "cid"
        # (conversation id); a node keys one exchange, and "conversation" now
        # means a root-to-leaf path, so the column is node_id.
        node_cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        if "cid" in node_cols and "node_id" not in node_cols:
            conn.execute("ALTER TABLE nodes RENAME COLUMN cid TO node_id")
        edge_cols = {row[1] for row in conn.execute("PRAGMA table_info(edges)")}
        if "source_cid" in edge_cols and "source_node_id" not in edge_cols:
            conn.execute("ALTER TABLE edges RENAME COLUMN source_cid TO source_node_id")
            conn.execute("ALTER TABLE edges RENAME COLUMN target_cid TO target_node_id")
        # Migrate pre-resize DBs: nodes gained w/h (0 = auto-size). x/y is the
        # top-left corner; the bottom-right corner is (x + w, y + h).
        node_cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        for col in ("w", "h"):
            if col not in node_cols:
                conn.execute(f"ALTER TABLE nodes ADD COLUMN {col} REAL NOT NULL DEFAULT 0")


def create_session(user_id: str) -> str:
    session_id = uuid.uuid4().hex
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (session_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, now, now),
        )
    return session_id


def latest_session(user_id: str) -> Optional[str]:
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row[0] if row else None


def list_sessions(user_id: str) -> List[Dict[str, Any]]:
    """All of a user's sessions, newest first, with node count and a title
    snippet (the first non-empty prompt) for the session picker."""
    with closing(_connect()) as conn, conn:
        rows = conn.execute(
            """
            SELECT s.session_id, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM nodes n
                    WHERE n.session_id = s.session_id) AS node_count,
                   COALESCE((SELECT n.prompt FROM nodes n
                             WHERE n.session_id = s.session_id AND n.prompt != ''
                             ORDER BY n.rowid LIMIT 1), '') AS first_prompt
            FROM sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "session_id": r[0],
            "created_at": r[1],
            "updated_at": r[2],
            "node_count": r[3],
            "first_prompt": r[4],
        }
        for r in rows
    ]


def session_owner(session_id: str) -> Optional[str]:
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row[0] if row else None


def save_dag(
    session_id: str,
    user_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> None:
    """Rewrite the session's DAG rows wholesale from ReactFlow-shaped dicts."""
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, user_id, now, now),
        )
        conn.execute("DELETE FROM nodes WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM edges WHERE session_id = ?", (session_id,))
        conn.executemany(
            "INSERT INTO nodes (session_id, node_id, x, y, w, h, prompt) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    session_id,
                    n["id"],
                    n["position"]["x"],
                    n["position"]["y"],
                    n["data"].get("width", 0) or 0,
                    n["data"].get("height", 0) or 0,
                    n["data"].get("prompt", ""),
                )
                for n in nodes
            ],
        )
        conn.executemany(
            "INSERT INTO edges (session_id, edge_id, source_node_id, target_node_id) VALUES (?, ?, ?, ?)",
            [(session_id, e["id"], e["source"], e["target"]) for e in edges],
        )


def load_dag(
    session_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (node_rows, edge_rows) as plain dicts (not ReactFlow shape)."""
    with closing(_connect()) as conn, conn:
        node_rows = conn.execute(
            "SELECT node_id, x, y, w, h, prompt FROM nodes WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT edge_id, source_node_id, target_node_id FROM edges WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return (
        [
            {"node_id": r[0], "x": r[1], "y": r[2], "w": r[3], "h": r[4], "prompt": r[5]}
            for r in node_rows
        ],
        [{"edge_id": r[0], "source": r[1], "target": r[2]} for r in edge_rows],
    )
