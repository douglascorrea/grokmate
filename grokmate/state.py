"""Manage ~/.grokmate/state.json for tracking current session."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_STATE_PATH = Path.home() / ".grokmate" / "state.json"


def read_current_session(state_path: Path = DEFAULT_STATE_PATH) -> Optional[str]:
    """Return current_session_id or None if not set / file missing."""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text())
        return data.get("current_session_id")
    except (json.JSONDecodeError, OSError):
        return None


def write_current_session(
    session_id: Optional[str], state_path: Path = DEFAULT_STATE_PATH
) -> None:
    """Write (or clear) the current session id."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    if session_id is None:
        data.pop("current_session_id", None)
    else:
        data["current_session_id"] = session_id
    state_path.write_text(json.dumps(data, indent=2) + "\n")
