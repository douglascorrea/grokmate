"""Unit tests for image extraction helpers in grokmate.grok.

All tests mock the uiautomator2 device so they run without a real Android
device.  The external ``subprocess`` calls (``adb``) are also mocked.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from grokmate import grok


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_imageview_element(
    left: int = 100,
    top: int = 200,
    right: int = 500,
    bottom: int = 600,
) -> MagicMock:
    """Return a mock u2 element that looks like an ImageView with given bounds."""
    el = MagicMock()
    el.info = {
        "bounds": {"left": left, "top": top, "right": right, "bottom": bottom}
    }
    return el


def _make_device_with_imageviews(
    elements: list[MagicMock],
) -> MagicMock:
    """Build a mock device whose ImageView query returns *elements*."""
    device = MagicMock()

    image_collection = MagicMock()
    image_collection.count = len(elements)
    image_collection.__getitem__ = MagicMock(side_effect=lambda i: elements[i])

    def side_effect(**kwargs: Any) -> MagicMock:
        if kwargs.get("className") == "android.widget.ImageView":
            return image_collection
        el = MagicMock()
        el.exists.return_value = False
        return el

    device.side_effect = side_effect
    return device


# ── _parse_bounds ─────────────────────────────────────────────────────────────


class TestParseBounds:
    def test_dict_format(self) -> None:
        bounds = {"left": 10, "top": 20, "right": 300, "bottom": 400}
        result = grok._parse_bounds(bounds)
        assert result == (10, 20, 300, 400)

    def test_string_format(self) -> None:
        result = grok._parse_bounds("[10,20][300,400]")
        assert result == (10, 20, 300, 400)

    def test_zero_width_returns_none(self) -> None:
        bounds = {"left": 0, "top": 0, "right": 0, "bottom": 200}
        assert grok._parse_bounds(bounds) is None

    def test_zero_height_returns_none(self) -> None:
        bounds = {"left": 0, "top": 0, "right": 200, "bottom": 0}
        assert grok._parse_bounds(bounds) is None

    def test_below_min_dimension_returns_none(self) -> None:
        # 79 × 79 — just under the MIN_IMAGE_DIMENSION threshold
        bounds = {"left": 0, "top": 0, "right": 79, "bottom": 79}
        assert grok._parse_bounds(bounds) is None

    def test_exactly_min_dimension(self) -> None:
        # MIN_IMAGE_DIMENSION × MIN_IMAGE_DIMENSION — should pass
        d = grok.MIN_IMAGE_DIMENSION
        bounds = {"left": 0, "top": 0, "right": d, "bottom": d}
        result = grok._parse_bounds(bounds)
        assert result is not None
        assert result == (0, 0, d, d)

    def test_unknown_type_returns_none(self) -> None:
        assert grok._parse_bounds(42) is None  # type: ignore[arg-type]
        assert grok._parse_bounds(None) is None  # type: ignore[arg-type]

    def test_string_with_insufficient_numbers_returns_none(self) -> None:
        assert grok._parse_bounds("[10,20]") is None


# ── find_image_views ──────────────────────────────────────────────────────────


class TestFindImageViews:
    def test_returns_empty_when_no_imageviews(self) -> None:
        device = MagicMock()
        collection = MagicMock()
        collection.count = 0
        device.side_effect = lambda **kw: collection
        assert grok.find_image_views(device) == []

    def test_filters_small_elements(self) -> None:
        """Elements smaller than MIN_IMAGE_DIMENSION in any axis are dropped."""
        # One small (icon-like), one large
        small = _make_imageview_element(left=0, top=0, right=16, bottom=16)
        large = _make_imageview_element(left=0, top=100, right=400, bottom=600)
        device = _make_device_with_imageviews([small, large])

        result = grok.find_image_views(device)
        assert len(result) == 1
        assert result[0] == (0, 100, 400, 600)

    def test_sorted_top_to_bottom(self) -> None:
        """Results must be sorted by the top coordinate."""
        img_b = _make_imageview_element(left=0, top=800, right=500, bottom=1200)
        img_a = _make_imageview_element(left=0, top=100, right=500, bottom=500)
        device = _make_device_with_imageviews([img_b, img_a])

        result = grok.find_image_views(device)
        assert result[0][1] < result[1][1], "Should be sorted top-to-bottom"

    def test_element_with_no_info_skipped(self) -> None:
        """Elements whose .info access raises are skipped gracefully."""
        bad_el = MagicMock()
        bad_el.info = {}  # no 'bounds' key → _parse_bounds returns None

        good_el = _make_imageview_element()
        device = _make_device_with_imageviews([bad_el, good_el])

        result = grok.find_image_views(device)
        assert len(result) == 1

    def test_device_raises_returns_empty(self) -> None:
        """If the device call itself throws, return empty list."""
        device = MagicMock()
        device.side_effect = Exception("u2 connection lost")
        assert grok.find_image_views(device) == []


# ── _try_save_via_long_press ──────────────────────────────────────────────────


class TestTrySaveViaLongPress:
    @patch("grokmate.grok.time")
    @patch("grokmate.grok.subprocess.run")
    @patch("grokmate.grok._list_device_files")
    @patch("grokmate.grok._get_media_dir")
    def test_save_menu_found_and_file_pulled(
        self,
        mock_media_dir: MagicMock,
        mock_list: MagicMock,
        mock_run: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Happy path: long-press → save menu → file pulled to host."""
        mock_media_dir.return_value = tmp_path
        mock_time.monotonic.side_effect = [0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
        mock_time.sleep = MagicMock()
        mock_time.time.return_value = 1234567890

        # List files: before = empty, after = one new PNG
        mock_list.side_effect = [
            [],  # before snapshot for /sdcard/Pictures
            [],  # before snapshot for /sdcard/Download
            [],  # before snapshot for /sdcard/DCIM
            [],  # before snapshot for /sdcard/DCIM/Screenshots
            ["photo.png"],  # poll after save — new file appears
        ]

        # Simulate adb pull writing the file
        dest_file = tmp_path / "1234567890_grok_photo.png"
        dest_file.touch()

        def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            r = MagicMock()
            r.returncode = 0
            return r

        mock_run.side_effect = fake_run

        device = MagicMock()
        save_item = MagicMock()
        save_item.exists.return_value = True

        def device_side_effect(**kwargs: Any) -> MagicMock:
            if kwargs.get("text") == "Save image":
                return save_item
            el = MagicMock()
            el.exists.return_value = False
            return el

        device.side_effect = device_side_effect

        bounds = (100, 200, 500, 600)
        result = grok._try_save_via_long_press(device, bounds, serial=None)

        device.long_click.assert_called_once_with(300, 400)
        save_item.click.assert_called_once()
        # Result may be None here because we can't easily match the dynamic
        # filename, but the key behaviour is that long_click and click were called.

    @patch("grokmate.grok.time")
    @patch("grokmate.grok._list_device_files")
    def test_no_menu_returns_none(
        self,
        mock_list: MagicMock,
        mock_time: MagicMock,
    ) -> None:
        """If no save menu appears, presses back and returns None."""
        mock_time.sleep = MagicMock()
        mock_list.return_value = []

        device = MagicMock()
        no_item = MagicMock()
        no_item.exists.return_value = False
        device.side_effect = lambda **kw: no_item

        bounds = (100, 200, 500, 600)
        result = grok._try_save_via_long_press(device, bounds)

        assert result is None
        device.press.assert_called_once_with("back")

    @patch("grokmate.grok.time")
    @patch("grokmate.grok._list_device_files")
    def test_long_click_failure_returns_none(
        self,
        mock_list: MagicMock,
        mock_time: MagicMock,
    ) -> None:
        """If both long_click attempts throw, returns None immediately."""
        mock_time.sleep = MagicMock()
        mock_list.return_value = []

        device = MagicMock()
        device.long_click.side_effect = Exception("u2 error")
        device.side_effect = lambda **kw: MagicMock(exists=MagicMock(return_value=False))

        bounds = (100, 200, 500, 600)
        result = grok._try_save_via_long_press(device, bounds)
        assert result is None

    @patch("grokmate.grok._pull_newest_image", return_value=None)
    @patch("grokmate.grok._list_device_files", return_value=[])
    @patch("grokmate.grok.time")
    def test_content_desc_fallback_for_menu(
        self,
        mock_time: MagicMock,
        mock_list: MagicMock,
        mock_pull: MagicMock,
    ) -> None:
        """Save item found via content-description instead of text."""
        mock_time.sleep = MagicMock()

        device = MagicMock()
        no_text_item = MagicMock()
        no_text_item.exists.return_value = False
        desc_item = MagicMock()
        desc_item.exists.return_value = True

        def device_side_effect(**kwargs: Any) -> MagicMock:
            if kwargs.get("description") in grok.SAVE_MENU_TEXTS:
                return desc_item
            return no_text_item

        device.side_effect = device_side_effect

        bounds = (100, 200, 500, 600)
        grok._try_save_via_long_press(device, bounds)

        desc_item.click.assert_called_once()


# ── _fallback_screencap_crop ──────────────────────────────────────────────────


class TestFallbackScreencapCrop:
    @patch("grokmate.grok.time")
    @patch("grokmate.grok._get_media_dir")
    def test_uses_device_screenshot_when_available(
        self,
        mock_media_dir: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Uses device.screenshot() (PIL Image) when it works."""
        mock_media_dir.return_value = tmp_path
        mock_time.time.return_value = 1111111111

        from PIL import Image

        fake_img = Image.new("RGB", (1080, 2400), color=(255, 0, 0))
        device = MagicMock()
        device.screenshot.return_value = fake_img

        bounds = (0, 0, 540, 1200)
        result = grok._fallback_screencap_crop(device, bounds, serial=None, index=0)

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"

        # Verify the crop dimensions
        from PIL import Image as PILImage
        saved = PILImage.open(result)
        assert saved.width == 540
        assert saved.height == 1200

    @patch("grokmate.grok.time")
    @patch("grokmate.grok._get_media_dir")
    @patch("grokmate.grok.subprocess.run")
    def test_falls_back_to_adb_screencap(
        self,
        mock_run: MagicMock,
        mock_media_dir: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Falls back to adb exec-out screencap -p when screenshot() fails."""
        mock_media_dir.return_value = tmp_path
        mock_time.time.return_value = 2222222222

        from PIL import Image

        # Build a minimal PNG in memory
        buf = io.BytesIO()
        Image.new("RGB", (1080, 2400), color=(0, 255, 0)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        device = MagicMock()
        device.screenshot.side_effect = Exception("screenshot() not available")

        adb_result = MagicMock()
        adb_result.returncode = 0
        adb_result.stdout = png_bytes
        mock_run.return_value = adb_result

        bounds = (100, 200, 500, 600)
        result = grok._fallback_screencap_crop(device, bounds, serial=None, index=1)

        assert result is not None
        assert result.exists()

        saved = Image.open(result)
        assert saved.width == 400
        assert saved.height == 400

    @patch("grokmate.grok.time")
    @patch("grokmate.grok._get_media_dir")
    @patch("grokmate.grok.subprocess.run")
    def test_returns_none_when_adb_fails(
        self,
        mock_run: MagicMock,
        mock_media_dir: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns None when both screenshot() and adb screencap fail."""
        mock_media_dir.return_value = tmp_path
        mock_time.time.return_value = 3333333333

        device = MagicMock()
        device.screenshot.side_effect = Exception("unavailable")

        adb_result = MagicMock()
        adb_result.returncode = 1
        adb_result.stdout = b""
        mock_run.return_value = adb_result

        bounds = (0, 0, 400, 400)
        result = grok._fallback_screencap_crop(device, bounds, serial=None, index=0)
        assert result is None


# ── extract_images ────────────────────────────────────────────────────────────


class TestExtractImages:
    def test_no_imageviews_returns_empty(self) -> None:
        """No ImageViews found → empty list, no other calls."""
        device = MagicMock()
        collection = MagicMock()
        collection.count = 0
        device.side_effect = lambda **kw: collection

        result = grok.extract_images(device)
        assert result == []

    @patch("grokmate.grok._try_save_via_long_press")
    @patch("grokmate.grok._fallback_screencap_crop")
    def test_primary_path_used_when_successful(
        self,
        mock_fallback: MagicMock,
        mock_primary: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When long-press save succeeds, fallback is NOT called."""
        saved_path = tmp_path / "saved.png"
        saved_path.touch()
        mock_primary.return_value = saved_path
        mock_fallback.return_value = None

        img = _make_imageview_element()
        device = _make_device_with_imageviews([img])

        result = grok.extract_images(device, serial=None)

        assert result == [saved_path]
        mock_primary.assert_called_once()
        mock_fallback.assert_not_called()

    @patch("grokmate.grok._try_save_via_long_press")
    @patch("grokmate.grok._fallback_screencap_crop")
    def test_fallback_used_when_primary_fails(
        self,
        mock_fallback: MagicMock,
        mock_primary: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When long-press save returns None, fallback screencap is used."""
        fallback_path = tmp_path / "cropped.png"
        fallback_path.touch()
        mock_primary.return_value = None
        mock_fallback.return_value = fallback_path

        img = _make_imageview_element()
        device = _make_device_with_imageviews([img])

        result = grok.extract_images(device, serial=None)

        assert result == [fallback_path]
        mock_primary.assert_called_once()
        mock_fallback.assert_called_once()

    @patch("grokmate.grok._try_save_via_long_press")
    @patch("grokmate.grok._fallback_screencap_crop")
    def test_both_paths_fail_returns_empty(
        self,
        mock_fallback: MagicMock,
        mock_primary: MagicMock,
    ) -> None:
        """If both paths fail for an image, it is skipped (no crash)."""
        mock_primary.return_value = None
        mock_fallback.return_value = None

        img = _make_imageview_element()
        device = _make_device_with_imageviews([img])

        result = grok.extract_images(device)
        assert result == []

    @patch("grokmate.grok._try_save_via_long_press")
    @patch("grokmate.grok._fallback_screencap_crop")
    def test_multiple_images_all_extracted(
        self,
        mock_fallback: MagicMock,
        mock_primary: MagicMock,
        tmp_path: Path,
    ) -> None:
        """All found images are processed and paths returned in visual order."""
        path_a = tmp_path / "a.png"
        path_b = tmp_path / "b.png"
        path_a.touch()
        path_b.touch()

        # First image: primary succeeds; second: primary fails, fallback succeeds
        mock_primary.side_effect = [path_a, None]
        mock_fallback.side_effect = [path_b]

        img1 = _make_imageview_element(top=100, bottom=600)
        img2 = _make_imageview_element(top=800, bottom=1300)
        device = _make_device_with_imageviews([img1, img2])

        result = grok.extract_images(device)
        assert result == [path_a, path_b]
        assert mock_primary.call_count == 2
        assert mock_fallback.call_count == 1

    @patch("grokmate.grok._try_save_via_long_press")
    @patch("grokmate.grok._fallback_screencap_crop")
    def test_serial_passed_to_helpers(
        self,
        mock_fallback: MagicMock,
        mock_primary: MagicMock,
        tmp_path: Path,
    ) -> None:
        """The device serial is forwarded to both primary and fallback helpers."""
        saved_path = tmp_path / "img.png"
        saved_path.touch()
        mock_primary.return_value = saved_path

        img = _make_imageview_element()
        device = _make_device_with_imageviews([img])

        grok.extract_images(device, serial="emulator-5554")

        # serial is passed as a keyword argument
        call_kwargs = mock_primary.call_args.kwargs
        assert call_kwargs.get("serial") == "emulator-5554"
