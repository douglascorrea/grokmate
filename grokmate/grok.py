"""Grok app interaction via uiautomator2.

Key design decision: we NEVER use `adb shell input text` because Samsung
HoneyBoard mangles input (autocorrect, character drops). Instead we use
uiautomator2's `.set_text()` on the EditText element by resource-id, which
writes directly into the field and bypasses the keyboard entirely.
"""

from __future__ import annotations

import time
from typing import Optional, Protocol

# Resource IDs observed in the Grok app (ai.x.grok)
RES_CHAT_INPUT = "ai.x.grok:id/chat_text_input"
RES_SEND_BUTTON = "ai.x.grok:id/input_send_button"
RES_TOP_BAR = "ai.x.grok:id/conversation_top_bar"
NEW_CHAT_DESC = "Start new chat"

# Strings shown while Grok is processing
LOADING_INDICATORS = frozenset({"Reading posts", "Thinking…", "Thinking..."})

DEFAULT_TIMEOUT = 60  # seconds
POLL_INTERVAL = 1.0  # seconds
SCROLL_STABILISE_ROUNDS = 2  # consecutive identical reads before we stop


class U2Device(Protocol):
    """Minimal interface we expect from a uiautomator2 device."""

    def __call__(self, **kwargs: object) -> "U2Element": ...
    def swipe_ext(self, direction: str, scale: float = 0.5) -> None: ...


class U2Element(Protocol):
    def exists(self) -> bool: ...
    def set_text(self, text: str) -> None: ...
    def click(self) -> None: ...
    def get_text(self) -> str: ...


def connect_device(serial: Optional[str] = None) -> object:
    """Connect uiautomator2 to the device. Returns a u2.Device."""
    import uiautomator2 as u2  # type: ignore[import-untyped]

    if serial:
        return u2.connect(serial)
    return u2.connect()


def tap_new_chat(device: object) -> None:
    """Tap the 'Start new chat' button."""
    d = device  # type: ignore[assignment]
    btn = d(description=NEW_CHAT_DESC)
    if btn.exists():
        btn.click()
        time.sleep(1.0)
    else:
        raise RuntimeError(
            f"Could not find '{NEW_CHAT_DESC}' button. Is Grok open?"
        )


def send_message(device: object, text: str) -> None:
    """Type a message using set_text (bypasses keyboard) and tap send.

    This is the critical path — we intentionally avoid `adb shell input text`
    because Samsung's HoneyBoard autocorrect mangles input.
    """
    d = device  # type: ignore[assignment]

    # Find the chat input field by resource-id
    input_field = d(resourceId=RES_CHAT_INPUT)
    if not input_field.exists():
        raise RuntimeError(
            f"Chat input field ({RES_CHAT_INPUT}) not found. Is Grok open?"
        )

    # Set text directly — no keyboard involvement
    input_field.set_text(text)

    # Tap send button
    send_btn = d(resourceId=RES_SEND_BUTTON)
    if not send_btn.exists():
        raise RuntimeError(
            f"Send button ({RES_SEND_BUTTON}) not found."
        )
    send_btn.click()


def wait_for_response(device: object, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Poll until loading indicators disappear from the UI tree."""
    d = device  # type: ignore[assignment]
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        # Check if any loading indicator text is visible
        found_loading = False
        for indicator in LOADING_INDICATORS:
            if d(text=indicator).exists():
                found_loading = True
                break

        if not found_loading:
            # Give a brief extra pause to let final text render
            time.sleep(0.5)
            return

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"Grok did not finish responding within {timeout}s"
    )


def read_response(device: object) -> str:
    """Read the assistant's response by extracting TextViews from the chat.

    Scrolls down and accumulates text until content stabilises (same text
    seen for SCROLL_STABILISE_ROUNDS consecutive reads).
    """
    d = device  # type: ignore[assignment]
    collected_texts: list[str] = []
    prev_snapshot = ""
    stable_count = 0

    for _ in range(30):  # safety cap on scroll iterations
        # Gather all visible TextViews
        elements = d(className="android.widget.TextView")
        texts = []
        for i in range(elements.count):
            try:
                t = elements[i].get_text()
                if t and t.strip():
                    texts.append(t.strip())
            except Exception:
                continue

        snapshot = "\n".join(texts)
        if snapshot == prev_snapshot:
            stable_count += 1
            if stable_count >= SCROLL_STABILISE_ROUNDS:
                break
        else:
            stable_count = 0

        prev_snapshot = snapshot
        collected_texts = texts

        # Scroll down to reveal more content
        d.swipe_ext("up", scale=0.5)
        time.sleep(0.5)

    if not collected_texts:
        return ""

    # The last text block(s) are typically the assistant's response.
    # We return the last substantial block — skip UI chrome.
    # Heuristic: take everything after the last occurrence of the user's
    # message marker. For now, return the last collected text segment.
    return collected_texts[-1] if collected_texts else ""


def extract_full_response(device: object) -> str:
    """Convenience: wait for response, then read it."""
    wait_for_response(device)
    return read_response(device)
