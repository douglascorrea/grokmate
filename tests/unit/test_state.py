"""Tests for grokmate.state — state.json read/write."""

from pathlib import Path

import pytest

from grokmate import state


@pytest.fixture()
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


def test_write_and_read_current_session(state_path: Path) -> None:
    state.write_current_session("sess-abc-123", state_path)
    assert state.read_current_session(state_path) == "sess-abc-123"


def test_no_session_returns_none(state_path: Path) -> None:
    # File doesn't exist
    assert state.read_current_session(state_path) is None


def test_empty_file_returns_none(state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("")
    assert state.read_current_session(state_path) is None


def test_overwrite_session(state_path: Path) -> None:
    state.write_current_session("first", state_path)
    state.write_current_session("second", state_path)
    assert state.read_current_session(state_path) == "second"


def test_clear_session(state_path: Path) -> None:
    state.write_current_session("sess-1", state_path)
    state.write_current_session(None, state_path)
    assert state.read_current_session(state_path) is None


def test_preserves_other_keys(state_path: Path) -> None:
    """Writing session should not clobber other keys in state.json."""
    import json

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"other_key": "keep_me"}))
    state.write_current_session("sess-1", state_path)
    data = json.loads(state_path.read_text())
    assert data["current_session_id"] == "sess-1"
    assert data["other_key"] == "keep_me"
