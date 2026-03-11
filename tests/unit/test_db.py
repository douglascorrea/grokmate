"""Tests for grokmate.db — SQLite session/message CRUD."""

import sqlite3
from pathlib import Path

import pytest

from grokmate import db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Provide a fresh in-memory-like DB in a temp dir."""
    return db.get_connection(tmp_path / "test.db")


class TestSessions:
    def test_create_session(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "sess-1", "my session", device_serial="ABC123")
        row = db.get_session(conn, "sess-1")
        assert row is not None
        assert row["name"] == "my session"
        assert row["status"] == "active"
        assert row["device_serial"] == "ABC123"

    def test_list_sessions(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "first")
        db.create_session(conn, "s2", "second")
        rows = db.list_sessions(conn)
        assert len(rows) == 2

    def test_list_sessions_by_status(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "first", status="active")
        db.create_session(conn, "s2", "second", status="suspended")
        active = db.list_sessions(conn, status="active")
        assert len(active) == 1
        assert active[0]["id"] == "s1"

    def test_mark_session_suspended(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "first", status="active")
        db.update_session_status(conn, "s1", "suspended")
        row = db.get_session(conn, "s1")
        assert row is not None
        assert row["status"] == "suspended"

    def test_suspend_active_sessions(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "first", status="active")
        db.create_session(conn, "s2", "second", status="active")
        db.create_session(conn, "s3", "third", status="suspended")
        count = db.suspend_active_sessions(conn)
        assert count == 2
        assert db.get_session(conn, "s1")["status"] == "suspended"
        assert db.get_session(conn, "s3")["status"] == "suspended"

    def test_find_session_by_name(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "abc-123-def", "cool chat")
        row = db.find_session(conn, "cool chat")
        assert row is not None
        assert row["id"] == "abc-123-def"

    def test_find_session_by_uuid_prefix(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "abc-123-def", "cool chat")
        row = db.find_session(conn, "abc-1")
        assert row is not None
        assert row["id"] == "abc-123-def"

    def test_find_session_not_found(self, conn: sqlite3.Connection) -> None:
        assert db.find_session(conn, "nonexistent") is None


class TestMessages:
    def test_add_message(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "test")
        msg_id = db.add_message(conn, "s1", "user", "hello")
        assert isinstance(msg_id, int)

    def test_get_messages_for_session(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "test")
        db.add_message(conn, "s1", "user", "hello")
        db.add_message(conn, "s1", "assistant", "hi there")
        msgs = db.get_messages(conn, "s1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "hi there"

    def test_messages_ordered_by_timestamp(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "test")
        db.add_message(conn, "s1", "user", "first")
        db.add_message(conn, "s1", "assistant", "second")
        db.add_message(conn, "s1", "user", "third")
        msgs = db.get_messages(conn, "s1")
        contents = [m["content"] for m in msgs]
        assert contents == ["first", "second", "third"]

    def test_messages_scoped_to_session(self, conn: sqlite3.Connection) -> None:
        db.create_session(conn, "s1", "test1")
        db.create_session(conn, "s2", "test2")
        db.add_message(conn, "s1", "user", "for s1")
        db.add_message(conn, "s2", "user", "for s2")
        assert len(db.get_messages(conn, "s1")) == 1
        assert len(db.get_messages(conn, "s2")) == 1
