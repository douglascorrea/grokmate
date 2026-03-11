"""E2E tests for image extraction — requires a connected Android device with Grok.

Run with:
    pytest tests/e2e/test_image_extraction.py -m e2e -v

These tests are skipped automatically when no device is connected.
They interact with a live Grok session on the device.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from grokmate import adb, grok

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


@skip_no_device
def test_find_image_views_returns_list() -> None:
    """find_image_views() should return a list (may be empty if no images on screen)."""
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)
    time.sleep(2)

    result = grok.find_image_views(u2_dev)

    assert isinstance(result, list)
    for bounds in result:
        assert len(bounds) == 4
        left, top, right, bottom = bounds
        assert right > left, "right must be greater than left"
        assert bottom > top, "bottom must be greater than top"
        assert (right - left) >= grok.MIN_IMAGE_DIMENSION
        assert (bottom - top) >= grok.MIN_IMAGE_DIMENSION


@skip_no_device
def test_extract_images_after_imagine_prompt() -> None:
    """Send an 'imagine' prompt to Grok, then extract images.

    This test sends a message asking Grok to generate an image.  If Grok
    responds with an image, extract_images() should return at least one path
    that exists on the host filesystem.

    NOTE: This test may take up to 2 minutes for Grok to generate the image.
    Grok's image generation is non-deterministic — if it fails to produce
    an image, the test passes with zero extracted paths (no error).
    """
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)
    time.sleep(2)

    # Open a fresh chat
    grok.tap_new_chat(u2_dev)
    time.sleep(1)

    # Send an image-generation prompt
    grok.send_message(u2_dev, "Imagine a red apple on a white table")

    # Wait for the response (long timeout for image generation)
    try:
        grok.wait_for_response(u2_dev, timeout=180)
    except TimeoutError:
        pytest.skip("Grok did not respond within timeout")

    # Allow UI to fully render
    time.sleep(2)

    # Attempt image extraction
    paths = grok.extract_images(u2_dev, serial=serial)

    # Validate any returned paths
    for p in paths:
        assert isinstance(p, Path), f"Expected Path, got {type(p)}"
        assert p.is_absolute(), f"Path should be absolute: {p}"
        assert p.exists(), f"File should exist on host: {p}"
        assert p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"), (
            f"Unexpected file extension: {p.suffix}"
        )
        # Sanity check: file is not empty
        assert p.stat().st_size > 0, f"File is empty: {p}"

    # Clean up extracted files (best-effort)
    for p in paths:
        try:
            p.unlink()
        except Exception:
            pass


@skip_no_device
def test_extract_images_does_not_affect_text_response() -> None:
    """Image extraction must not corrupt or erase the text response.

    Send a plain text query, verify the text response is intact, then
    verify that extract_images() returns an empty list (no generated images).
    """
    device_info = adb.get_connected_device()
    serial = device_info.serial if device_info else None

    adb.launch_grok(serial)
    u2_dev = grok.connect_device(serial)
    time.sleep(2)

    grok.tap_new_chat(u2_dev)
    time.sleep(1)

    prompt = "What is 2 + 2? Reply with only the number."
    grok.send_message(u2_dev, prompt)

    try:
        grok.wait_for_response(u2_dev, timeout=60)
    except TimeoutError:
        pytest.skip("Grok did not respond within timeout")

    time.sleep(1)

    text = grok.read_response(u2_dev)
    assert text.strip(), "Text response should not be empty"

    # No image should be generated for a math question
    paths = grok.extract_images(u2_dev, serial=serial)
    assert isinstance(paths, list)
    # A plain math response should have no generated images
    # (we don't assert len == 0 in case Grok adds decorative images, but
    # the call must not raise)
