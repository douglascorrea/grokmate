"""SQLite session and message persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path.home() / ".grokmate" / "grokmate.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    device_serial TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a connection, creating the DB and tables if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


# ── Sessions ────────────────────────────────────────────────────────────────


def create_session(
    conn: sqlite3.Connection,
    session_id: str,
    name: str,
    device_serial: Optional[str] = None,
    status: str = "active",
) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO sessions (id, name, created_at, updated_at, device_serial, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, name, now, now, device_serial, status),
    )
    conn.commit()


def update_session_status(
    conn: sqlite3.Connection, session_id: str, status: str
) -> None:
    conn.execute(
        "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now_iso(), session_id),
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()


def find_session(
    conn: sqlite3.Connection, name_or_id: str
) -> Optional[sqlite3.Row]:
    """Find a session by exact name or UUID prefix (>= 4 chars)."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE name = ?", (name_or_id,)
    ).fetchone()
    if row:
        return row
    # Try UUID prefix match
    if len(name_or_id) >= 4:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id LIKE ? ORDER BY created_at DESC LIMIT 1",
            (name_or_id + "%",),
        ).fetchone()
    return row


def list_sessions(
    conn: sqlite3.Connection, status: Optional[str] = None
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM sessions ORDER BY created_at DESC"
    ).fetchall()


def suspend_active_sessions(conn: sqlite3.Connection) -> int:
    """Mark all active sessions as suspended. Returns count affected."""
    cur = conn.execute(
        "UPDATE sessions SET status = 'suspended', updated_at = ? WHERE status = 'active'",
        (_now_iso(),),
    )
    conn.commit()
    return cur.rowcount


# ── Messages ────────────────────────────────────────────────────────────────


def add_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, now),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_messages(
    conn: sqlite3.Connection, session_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
        (session_id,),
    ).fetchall()
