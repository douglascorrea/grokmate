"""Tests for grokmate.cli — Typer CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grokmate import cli, db, state

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path) -> None:  # type: ignore[misc]
    """Point CLI at temp DB and state file for every test."""
    cli._db_path = tmp_path / "test.db"
    cli._state_path = tmp_path / "state.json"


@pytest.fixture()
def conn(tmp_path: Path) -> "db.sqlite3.Connection":
    return db.get_connection(tmp_path / "test.db")


class TestCheck:
    @patch("grokmate.adb.scrcpy_available", return_value=True)
    @patch("grokmate.grok.connect_device")
    @patch("grokmate.adb.is_grok_installed", return_value=True)
    @patch("grokmate.adb.get_connected_device")
    def test_check_passes_when_device_connected(
        self,
        mock_device: MagicMock,
        mock_grok_installed: MagicMock,
        mock_u2: MagicMock,
        mock_scrcpy: MagicMock,
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        result = runner.invoke(cli.app, ["check"])
        assert result.exit_code == 0
        assert "✓" in result.output

    @patch("grokmate.adb.scrcpy_available", return_value=False)
    @patch("grokmate.adb.get_connected_device", return_value=None)
    def test_check_fails_without_device(
        self, mock_device: MagicMock, mock_scrcpy: MagicMock
    ) -> None:
        result = runner.invoke(cli.app, ["check"])
        assert result.exit_code == 1
        assert "✗" in result.output


class TestMessage:
    def test_message_fails_without_session(self) -> None:
        result = runner.invoke(cli.app, ["message", "hello"])
        assert result.exit_code == 1
        assert "No active session" in result.output

    @patch("grokmate.grok.extract_full_response", return_value="Grok says hi")
    @patch("grokmate.grok.send_message")
    @patch("grokmate.grok.connect_device")
    @patch("grokmate.grok.tap_new_chat")
    @patch("grokmate.adb.launch_grok")
    @patch("grokmate.adb.get_connected_device")
    def test_message_one_shot_succeeds_without_session(
        self,
        mock_device: MagicMock,
        mock_launch: MagicMock,
        mock_new_chat: MagicMock,
        mock_connect: MagicMock,
        mock_send: MagicMock,
        mock_response: MagicMock,
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        result = runner.invoke(cli.app, ["message", "hello", "--one-shot"])
        assert result.exit_code == 0
        assert "Grok says hi" in result.output

    @patch("grokmate.grok.extract_full_response", return_value="response text")
    @patch("grokmate.grok.send_message")
    @patch("grokmate.grok.connect_device")
    @patch("grokmate.adb.launch_grok")
    @patch("grokmate.adb.get_connected_device")
    def test_message_in_active_session(
        self,
        mock_device: MagicMock,
        mock_launch: MagicMock,
        mock_connect: MagicMock,
        mock_send: MagicMock,
        mock_response: MagicMock,
        conn: "db.sqlite3.Connection",
        tmp_path: Path,
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        # Set up an active session
        db.create_session(conn, "sess-123", "test-session")
        state.write_current_session("sess-123", tmp_path / "state.json")

        result = runner.invoke(cli.app, ["message", "hello"])
        assert result.exit_code == 0
        assert "response text" in result.output


class TestSessionNew:
    @patch("grokmate.grok.tap_new_chat")
    @patch("grokmate.grok.connect_device")
    @patch("grokmate.adb.launch_grok")
    @patch("grokmate.adb.get_connected_device")
    def test_session_new_creates_db_record(
        self,
        mock_device: MagicMock,
        mock_launch: MagicMock,
        mock_connect: MagicMock,
        mock_new_chat: MagicMock,
        conn: "db.sqlite3.Connection",
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        result = runner.invoke(cli.app, ["session", "new", "--name", "my-chat"])
        assert result.exit_code == 0
        assert "my-chat" in result.output

        sessions = db.list_sessions(conn)
        assert len(sessions) == 1
        assert sessions[0]["name"] == "my-chat"
        assert sessions[0]["status"] == "active"

    @patch("grokmate.grok.tap_new_chat")
    @patch("grokmate.grok.connect_device")
    @patch("grokmate.adb.launch_grok")
    @patch("grokmate.adb.get_connected_device")
    def test_session_new_suspends_previous(
        self,
        mock_device: MagicMock,
        mock_launch: MagicMock,
        mock_connect: MagicMock,
        mock_new_chat: MagicMock,
        conn: "db.sqlite3.Connection",
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        runner.invoke(cli.app, ["session", "new", "--name", "first"])
        runner.invoke(cli.app, ["session", "new", "--name", "second"])

        sessions = db.list_sessions(conn)
        statuses = {s["name"]: s["status"] for s in sessions}
        assert statuses["first"] == "suspended"
        assert statuses["second"] == "active"


class TestSessionResume:
    @patch("grokmate.adb.launch_grok")
    @patch("grokmate.adb.get_connected_device")
    def test_session_resume_updates_state(
        self,
        mock_device: MagicMock,
        mock_launch: MagicMock,
        conn: "db.sqlite3.Connection",
        tmp_path: Path,
    ) -> None:
        from grokmate.adb import DeviceInfo

        mock_device.return_value = DeviceInfo(serial="R5CR1234", state="device")

        db.create_session(conn, "sess-old", "old-chat", status="suspended")

        result = runner.invoke(
            cli.app, ["session", "resume", "--session", "old-chat"]
        )
        assert result.exit_code == 0
        assert "old-chat" in result.output

        # Verify state updated
        current = state.read_current_session(tmp_path / "state.json")
        assert current == "sess-old"

        # Verify DB status
        row = db.get_session(conn, "sess-old")
        assert row["status"] == "active"

    @patch("grokmate.adb.get_connected_device", return_value=None)
    def test_session_resume_not_found(self, mock_device: MagicMock) -> None:
        result = runner.invoke(
            cli.app, ["session", "resume", "--session", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output
