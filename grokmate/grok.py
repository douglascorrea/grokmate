"""Grok app interaction via uiautomator2.

Key design decision: we NEVER use `adb shell input text` because Samsung
HoneyBoard mangles input (autocorrect, character drops). Instead we use
uiautomator2's `.set_text()` on the EditText element by resource-id, which
writes directly into the field and bypasses the keyboard entirely.
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Protocol

# Resource IDs observed in the Grok app (ai.x.grok)
# NOTE: Grok uses bare resource IDs (React Native style) — no package prefix.
RES_CHAT_INPUT = "chat_text_input"
RES_SEND_BUTTON = "input_send_button"
RES_TOP_BAR = "conversation_top_bar"
NEW_CHAT_DESC = "Start new chat"

# Strings shown while Grok is processing
LOADING_INDICATORS = frozenset({"Reading posts", "Thinking…", "Thinking..."})

DEFAULT_TIMEOUT = 120  # seconds
POLL_INTERVAL = 1.0  # seconds
SCROLL_STABILISE_ROUNDS = 2  # consecutive identical reads before we stop

# ── Image extraction constants ───────────────────────────────────────────────

#: Default directory on the host where extracted images are stored.
MEDIA_DIR: Path = Path.home() / ".grokmate" / "media"

#: Context-menu strings to look for when saving an image (multi-locale).
SAVE_MENU_TEXTS: frozenset[str] = frozenset({
    "Save image",
    "Save",
    "Download",
    "Guardar imagen",
    "Guardar",
    "Descargar",
    "Save to device",
    "Save photo",
})

#: Minimum pixel dimension (width *and* height) for an ImageView to be
#: considered a generated image rather than an icon or avatar.
MIN_IMAGE_DIMENSION: int = 80

#: How long (seconds) to wait for a saved file to appear on the device.
SAVE_WAIT_TIMEOUT: float = 20.0

#: Directories on the device where Android apps typically save images.
_DEVICE_SAVE_DIRS: tuple[str, ...] = (
    "/sdcard/Pictures",
    "/sdcard/Download",
    "/sdcard/DCIM",
    "/sdcard/DCIM/Screenshots",
)

_logger = logging.getLogger(__name__)

# ── Protocols ────────────────────────────────────────────────────────────────


class U2Device(Protocol):
    """Minimal interface we expect from a uiautomator2 device."""

    def __call__(self, **kwargs: object) -> "U2Element": ...
    def swipe_ext(self, direction: str, scale: float = 0.5) -> None: ...


class U2Element(Protocol):
    def exists(self) -> bool: ...
    def set_text(self, text: str) -> None: ...
    def click(self) -> None: ...
    def get_text(self) -> str: ...


# ── Device connection ────────────────────────────────────────────────────────


def connect_device(serial: Optional[str] = None) -> object:
    """Connect uiautomator2 to the device. Returns a u2.Device."""
    import uiautomator2 as u2  # type: ignore[import-untyped]

    if serial:
        return u2.connect(serial)
    return u2.connect()


# ── Chat navigation ──────────────────────────────────────────────────────────


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


def extract_full_response(device: object, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Convenience: wait for response, then read it."""
    wait_for_response(device, timeout=timeout)
    return read_response(device)


# ── Image extraction ─────────────────────────────────────────────────────────


def _get_media_dir() -> Path:
    """Ensure ``~/.grokmate/media/`` exists and return its path."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return MEDIA_DIR


def _parse_bounds(bounds_raw: object) -> Optional[tuple[int, int, int, int]]:
    """Parse element bounds into ``(left, top, right, bottom)``.

    Handles two formats returned by uiautomator2:
    - dict: ``{"left": x, "top": y, "right": x2, "bottom": y2}``
    - str:  ``"[x,y][x2,y2]"``

    Returns ``None`` if the bounds are missing or degenerate (zero-size).
    """
    if isinstance(bounds_raw, dict):
        left = int(bounds_raw.get("left", 0))
        top = int(bounds_raw.get("top", 0))
        right = int(bounds_raw.get("right", 0))
        bottom = int(bounds_raw.get("bottom", 0))
    elif isinstance(bounds_raw, str):
        nums = re.findall(r"\d+", bounds_raw)
        if len(nums) < 4:
            return None
        left, top, right, bottom = int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
    else:
        return None

    width = right - left
    height = bottom - top
    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        return None  # too small — likely an icon, not a generated image

    return (left, top, right, bottom)


def find_image_views(device: object) -> list[tuple[int, int, int, int]]:
    """Locate visible ``ImageView`` elements large enough to be generated images.

    Returns a list of ``(left, top, right, bottom)`` tuples sorted by their
    vertical position (top-to-bottom / visual reading order).  Only elements
    whose width *and* height are ≥ ``MIN_IMAGE_DIMENSION`` pixels are included.
    """
    d = device  # type: ignore[assignment]
    candidates: list[tuple[int, int, int, int]] = []

    try:
        elements = d(className="android.widget.ImageView")
        count = elements.count
    except Exception:
        return []

    for i in range(count):
        try:
            el = elements[i]
            info = el.info
            bounds_raw = info.get("bounds") if isinstance(info, dict) else None
            if bounds_raw is None:
                continue
            bounds = _parse_bounds(bounds_raw)
            if bounds is not None:
                candidates.append(bounds)
        except Exception:
            continue

    # Sort top-to-bottom
    candidates.sort(key=lambda b: b[1])
    return candidates


def _adb_base(serial: Optional[str] = None) -> list[str]:
    """Return ``["adb"]`` or ``["adb", "-s", serial]``."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd


def _list_device_files(path: str, serial: Optional[str] = None) -> list[str]:
    """Return filenames in *path* on the device (newest first via ``ls -1t``)."""
    cmd = _adb_base(serial) + ["shell", "ls", "-1t", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _pull_newest_image(
    device_dir: str,
    before_files: set[str],
    serial: Optional[str] = None,
    label: str = "",
    timeout: float = SAVE_WAIT_TIMEOUT,
) -> Optional[Path]:
    """Wait for a *new* image file to appear in *device_dir* and pull it.

    Compares the directory listing against *before_files*, waits up to
    *timeout* seconds, then ``adb pull``s the first new image found into
    ``~/.grokmate/media/``.

    Returns the host-side :class:`~pathlib.Path` on success, or ``None``.
    """
    media_dir = _get_media_dir()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        current_files = set(_list_device_files(device_dir, serial))
        new_files = current_files - before_files

        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        image_files = [f for f in new_files if f.lower().endswith(image_exts)]

        if image_files:
            device_path = f"{device_dir}/{image_files[0]}"
            ts = int(time.time())
            safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:20] if label else "grok"
            host_filename = f"{ts}_{safe_label}_{image_files[0]}"
            host_path = media_dir / host_filename

            pull_cmd = _adb_base(serial) + ["pull", device_path, str(host_path)]
            try:
                result = subprocess.run(pull_cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and host_path.exists():
                    return host_path
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            return None  # found it but pull failed

        time.sleep(0.5)

    return None


def _try_save_via_long_press(
    device: object,
    bounds: tuple[int, int, int, int],
    serial: Optional[str] = None,
) -> Optional[Path]:
    """Primary path: long-press an image element to trigger the save menu.

    Steps:
    1. Snapshot current files in ``_DEVICE_SAVE_DIRS``.
    2. Long-press the element's centre.
    3. Look for a save/download menu item and tap it.
    4. Poll for a new file to appear; pull it to host.

    Returns the host-side :class:`~pathlib.Path` on success, or ``None``.
    """
    d = device  # type: ignore[assignment]
    left, top, right, bottom = bounds
    cx = (left + right) // 2
    cy = (top + bottom) // 2

    # Snapshot current device files before triggering the save
    before_snapshots: dict[str, set[str]] = {
        dir_path: set(_list_device_files(dir_path, serial))
        for dir_path in _DEVICE_SAVE_DIRS
    }

    # Long-press at the image centre
    try:
        d.long_click(cx, cy)
    except Exception:
        try:
            d.long_click(cx, cy, duration=1.0)
        except Exception:
            return None

    time.sleep(1.0)  # wait for the context menu to appear

    # Try to find and tap a save/download menu item (text-based)
    saved = False
    for label in SAVE_MENU_TEXTS:
        try:
            item = d(text=label)
            if item.exists():
                item.click()
                saved = True
                break
        except Exception:
            continue

    # Content-description fallback (some ROMs use content-desc instead of text)
    if not saved:
        for label in SAVE_MENU_TEXTS:
            try:
                item = d(description=label)
                if item.exists():
                    item.click()
                    saved = True
                    break
            except Exception:
                continue

    if not saved:
        # Dismiss the menu (if any) and report failure
        try:
            d.press("back")
        except Exception:
            pass
        return None

    # Wait for the newly saved file and pull it to the host
    for dir_path in _DEVICE_SAVE_DIRS:
        path = _pull_newest_image(
            dir_path,
            before_snapshots[dir_path],
            serial=serial,
            timeout=SAVE_WAIT_TIMEOUT,
        )
        if path is not None:
            return path

    return None


def _fallback_screencap_crop(
    device: object,
    bounds: tuple[int, int, int, int],
    serial: Optional[str] = None,
    index: int = 0,
) -> Optional[Path]:
    """Fallback: capture the full screen and crop to *bounds*.

    First tries ``device.screenshot()`` (uiautomator2 built-in, returns a
    PIL Image), then falls back to ``adb exec-out screencap -p`` + Pillow.

    Returns the host-side :class:`~pathlib.Path` on success, or ``None``.
    """
    _logger.warning(
        "Image extraction: falling back to screencap+crop for element at bounds %s",
        bounds,
    )

    left, top, right, bottom = bounds
    media_dir = _get_media_dir()
    ts = int(time.time())
    out_path = media_dir / f"{ts}_grok_img_{index}.png"

    # ── Attempt 1: uiautomator2 device.screenshot() ──────────────────────
    try:
        d = device  # type: ignore[assignment]
        screenshot = d.screenshot()
        # u2 screenshot() returns a PIL Image object
        if hasattr(screenshot, "crop"):
            cropped = screenshot.crop((left, top, right, bottom))
            cropped.save(str(out_path))
            return out_path
    except Exception:
        pass

    # ── Attempt 2: adb exec-out screencap -p ─────────────────────────────
    try:
        from PIL import Image  # type: ignore[import-untyped]

        cmd = _adb_base(serial) + ["exec-out", "screencap", "-p"]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0 or not result.stdout:
            return None

        img = Image.open(io.BytesIO(result.stdout))
        cropped = img.crop((left, top, right, bottom))
        cropped.save(str(out_path))
        return out_path

    except ImportError:
        _logger.error(
            "Pillow not installed — cannot do screencap+crop fallback. "
            "Install it with: pip install Pillow"
        )
    except Exception as exc:
        _logger.warning("screencap+crop failed: %s", exc)

    return None


def extract_images(
    device: object,
    serial: Optional[str] = None,
) -> list[Path]:
    """Detect and extract generated images from the current Grok response.

    For each ``ImageView`` found on screen that is large enough to be a
    generated image (≥ ``MIN_IMAGE_DIMENSION`` px in both dimensions):

    1. **Primary**: long-press → context-menu save → ``adb pull`` to host.
    2. **Fallback**: ``screencap`` → Pillow crop → save to host.

    All files land in ``~/.grokmate/media/``.

    Returns a list of absolute host-side :class:`~pathlib.Path` objects
    (may be empty if no images were found or all extractions failed).
    """
    bounds_list = find_image_views(device)
    if not bounds_list:
        return []

    paths: list[Path] = []
    for i, bounds in enumerate(bounds_list):
        # Primary: long-press save
        path = _try_save_via_long_press(device, bounds, serial=serial)

        if path is None:
            # Fallback: screencap + crop
            path = _fallback_screencap_crop(device, bounds, serial=serial, index=i)

        if path is not None:
            paths.append(path)

    return paths
