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
# NOTE: Grok uses bare resource IDs (React Native style) — no package prefix.
RES_CHAT_INPUT = "chat_text_input"
RES_SEND_BUTTON = "input_send_button"
RES_TOP_BAR = "conversation_top_bar"
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


def tap_new_chat(device: object, wait_timeout: int = 8) -> None:
    """Tap the 'Start new chat' button and wait for the input field to appear."""
    d = device  # type: ignore[assignment]
    btn = d(description=NEW_CHAT_DESC)
    if btn.exists():
        btn.click()
    else:
        raise RuntimeError(
            f"Could not find '{NEW_CHAT_DESC}' button. Is Grok open?"
        )

    # Wait for the chat input field to appear (new chat screen fully loaded)
    start = time.monotonic()
    while time.monotonic() - start < wait_timeout:
        if (
            d(resourceId=RES_CHAT_INPUT).exists()
            or d(resourceId=f"ai.x.grok:id/{RES_CHAT_INPUT}").exists()
            or d(className="android.widget.EditText").exists()
        ):
            time.sleep(0.3)
            return
        time.sleep(0.3)
    raise RuntimeError(
        f"Chat input field did not appear within {wait_timeout}s after tapping new chat."
    )


def _find_chat_input(d: object, wait_timeout: int = 15) -> object:
    """Find the chat input EditText, trying multiple selectors.

    Grok uses bare resource IDs (React Native style). uiautomator2's
    resourceId selector requires full package-prefixed IDs in the underlying
    UIAutomator Java API, so we fall back to class-based lookup.
    """
    d = d  # type: ignore[assignment]
    start = time.monotonic()
    while time.monotonic() - start < wait_timeout:
        # Try 1: full package-prefixed resource ID
        el = d(resourceId=f"ai.x.grok:id/{RES_CHAT_INPUT}")  # type: ignore[operator]
        if el.exists():
            return el
        # Try 2: bare resource ID (some u2 versions accept this)
        el = d(resourceId=RES_CHAT_INPUT)  # type: ignore[operator]
        if el.exists():
            return el
        # Try 3: EditText class (there is only one on the chat screen)
        el = d(className="android.widget.EditText")  # type: ignore[operator]
        if el.exists():
            return el
        time.sleep(0.5)
    raise RuntimeError(
        f"Chat input field not found after {wait_timeout}s. "
        "Is Grok open and on a chat screen?"
    )


def send_message(device: object, text: str, wait_timeout: int = 15) -> None:
    """Type a message using set_text (bypasses keyboard) and tap send.

    This is the critical path — we intentionally avoid `adb shell input text`
    because Samsung's HoneyBoard autocorrect mangles input.

    Waits up to `wait_timeout` seconds for the input field to appear
    (handles the case where the app is still animating into view).
    """
    d = device  # type: ignore[assignment]

    input_field = _find_chat_input(d, wait_timeout=wait_timeout)

    # Set text directly — no keyboard involvement
    input_field.set_text(text)
    time.sleep(0.3)  # brief settle before tapping send

    # Tap send button — try resource ID first, then content-desc fallback
    send_btn = d(resourceId=RES_SEND_BUTTON)
    if not send_btn.exists():
        send_btn = d(description="Send message")
    if not send_btn.exists():
        raise RuntimeError("Send button not found.")
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


# UI chrome strings to exclude when extracting responses
_UI_CHROME: frozenset[str] = frozenset({
    "Ask", "Imagine", "Auto", "Speak", "Ask anything",
    "Think Harder", "Quick Answer", "Search", "Temporary conversation",
    "Private Chat", "This chat won't appear in history and will be fully erased",
    "Start dictation",
})


def _is_content_text(text: str) -> bool:
    """Return True if text looks like actual content (not UI chrome).

    Excludes only known UI chrome strings and very short strings (≤ 3 chars)
    that are typically icons or single-letter labels. This intentionally allows
    short bullet-point items through.
    """
    t = text.strip()
    if not t:
        return False
    if t in _UI_CHROME:
        return False
    if len(t) <= 3:
        return False
    return True


def _read_visible_texts(d: object) -> list[str]:
    """Read all non-empty text from visible TextViews."""
    elements = d(className="android.widget.TextView")  # type: ignore[operator]
    texts: list[str] = []
    for i in range(elements.count):
        try:
            t = elements[i].get_text()
            if t:
                texts.append(t.strip())
        except Exception:
            continue
    return texts


def read_response(device: object) -> str:
    """Read the assistant's response by extracting TextViews from the chat.

    Strategy:
    1. Scroll UP to the top of the conversation (Grok appends at the bottom,
       so a long response may extend below the fold).
    2. Scroll DOWN, accumulating all visible content TextViews.
    3. Filter out UI chrome via ``_is_content_text()``.
    4. Stop scrolling when content stabilises (no new text for N reads).
    5. Return all content blocks joined by double newlines.
    """
    d = device  # type: ignore[assignment]

    # ── Phase 1: scroll to the top of the conversation ──────────────────
    seen_snapshots: set[str] = set()
    stable_count = 0
    for _ in range(20):  # safety cap
        texts = _read_visible_texts(d)
        snapshot = "|".join(texts)
        if snapshot in seen_snapshots:
            stable_count += 1
            if stable_count >= SCROLL_STABILISE_ROUNDS:
                break
        else:
            stable_count = 0
            seen_snapshots.add(snapshot)

        # Swipe finger down → content scrolls up → reveals earlier content
        d.swipe_ext("down", scale=0.5)
        time.sleep(0.5)

    # ── Phase 2: scroll down, accumulating all content ──────────────────
    all_content: list[str] = []
    seen_snapshots = set()
    stable_count = 0
    for _ in range(30):  # safety cap
        texts = _read_visible_texts(d)
        snapshot = "|".join(texts)
        if snapshot in seen_snapshots:
            stable_count += 1
            if stable_count >= SCROLL_STABILISE_ROUNDS:
                break
        else:
            stable_count = 0
            seen_snapshots.add(snapshot)
            for t in texts:
                if _is_content_text(t) and t not in all_content:
                    all_content.append(t)

        # Swipe finger up → content scrolls down → reveals later content
        d.swipe_ext("up", scale=0.5)
        time.sleep(0.5)

    if not all_content:
        return ""

    return "\n\n".join(all_content)


def extract_full_response(device: object) -> str:
    """Convenience: wait for response, then read it."""
    wait_for_response(device)
    return read_response(device)
