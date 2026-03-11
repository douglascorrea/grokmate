"""End-to-end tests requiring a real Android device with the Grok app.

Run with: pytest tests/e2e/ -m e2e
Skip automatically if no device is connected.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grokmate import adb, db, grok, state

# Skip entire module if no device connected
pytestmark = pytest.mark.e2e


def _device_available() -> bool:
    try:
        return adb.get_connected_device() is not None
    except Exception:
        return False


skip_no_device = pytest.mark.skipif(
    not _device_available(),
    reason="No Android device connected",
)


@pytest.fixture()
def e2e_db(tmp_path: Path) -> sqlite3.Connection:
    return db.get_connection(tmp_path / "e2e.db")


@pytest.fixture()
def e2e_state(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@skip_no_device
def test_full_one_shot_flow(e2e_db: sqlite3.Connection, e2e_state: Path) -> None:
    """Run a one-shot message and verify DB persistence."""
    import uuid

    session_id = str(uuid.uuid4())
    name = "e2e-oneshot"

    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)

    import time
    time.sleep(3)

    grok.tap_new_chat(u2_dev)
    db.create_session(e2e_db, session_id, name, device_serial=serial, status="active")

    text = "hello"
    grok.send_message(u2_dev, text)
    db.add_message(e2e_db, session_id, "user", text)

    response = grok.extract_full_response(u2_dev)
    assert response, "Response should not be empty"
    db.add_message(e2e_db, session_id, "assistant", response)

    msgs = db.get_messages(e2e_db, session_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


@skip_no_device
def test_session_create_then_message(
    e2e_db: sqlite3.Connection, e2e_state: Path
) -> None:
    """Create a session, send a message, check response is relevant."""
    import uuid
    import time

    session_id = str(uuid.uuid4())
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)
    time.sleep(3)

    grok.tap_new_chat(u2_dev)
    db.create_session(
        e2e_db, session_id, "e2e-test", device_serial=serial, status="active"
    )
    state.write_current_session(session_id, e2e_state)

    grok.send_message(u2_dev, "what is elixir")
    db.add_message(e2e_db, session_id, "user", "what is elixir")

    response = grok.extract_full_response(u2_dev)
    db.add_message(e2e_db, session_id, "assistant", response)

    assert "elixir" in response.lower(), f"Expected 'elixir' in response: {response}"


@skip_no_device
def test_text_injection_no_garbling() -> None:
    """Regression test: verify uiautomator2 set_text doesn't garble input.

    This catches the Samsung HoneyBoard autocorrect bug where
    'adb shell input text' would mangle words.
    """
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)

    import time
    time.sleep(2)

    # Set a known text string
    test_text = "The actual quick brown fox jumps over the lazy dog"

    input_field = u2_dev(resourceId=grok.RES_CHAT_INPUT)  # type: ignore[operator]
    assert input_field.exists(), "Chat input field not found"

    input_field.set_text(test_text)
    time.sleep(0.5)

    # Read it back
    retrieved = input_field.get_text()
    assert retrieved == test_text, (
        f"Text garbled! Expected: {test_text!r}, Got: {retrieved!r}"
    )

    # Clear the field so we don't accidentally send
    input_field.set_text("")
