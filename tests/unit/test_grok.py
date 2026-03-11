"""Tests for grokmate.grok — Grok app interaction via uiautomator2.

All tests mock the u2 device to run without a real Android device.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from grokmate import grok


def _make_mock_device() -> MagicMock:
    """Create a mock u2 device with standard element stubs."""
    device = MagicMock()

    # Default: elements exist
    def element_factory(**kwargs: object) -> MagicMock:
        el = MagicMock()
        el.exists.return_value = True
        el.get_text.return_value = ""
        return el

    device.side_effect = element_factory
    return device


def _missing_element() -> MagicMock:
    """Return a mock element whose .exists() is False."""
    el = MagicMock()
    el.exists.return_value = False
    return el


def _send_message_device(
    input_el: MagicMock, send_el: MagicMock
) -> MagicMock:
    """Build a mock device for send_message tests.

    Handles the 3-selector fallback pattern in _find_chat_input:
    full resource ID, bare resource ID, and class-based lookup.
    """
    device = MagicMock()
    _input_ids = {
        grok.RES_CHAT_INPUT,
        f"ai.x.grok:id/{grok.RES_CHAT_INPUT}",
    }
    _send_ids = {
        grok.RES_SEND_BUTTON,
        f"ai.x.grok:id/{grok.RES_SEND_BUTTON}",
    }

    def side_effect(**kwargs: object) -> MagicMock:
        rid = kwargs.get("resourceId", "")
        if rid in _input_ids:
            return input_el
        if rid in _send_ids:
            return send_el
        if kwargs.get("className") == "android.widget.EditText":
            return input_el
        if kwargs.get("description") == "Send message":
            return send_el
        return _missing_element()

    device.side_effect = side_effect
    return device


class TestSendMessage:
    def test_set_text_uses_uiautomator_not_adb_input(self) -> None:
        """Ensure we use .set_text() on the EditText, never adb shell input text."""
        input_el = MagicMock()
        input_el.exists.return_value = True
        send_el = MagicMock()
        send_el.exists.return_value = True
        device = _send_message_device(input_el, send_el)

        grok.send_message(device, "Hello, world!")

        # set_text must be called (not adb input text)
        input_el.set_text.assert_called_once_with("Hello, world!")

    def test_send_button_tapped_after_text_set(self) -> None:
        """Ensure the send button is clicked AFTER text is set."""
        call_order: list[str] = []

        input_el = MagicMock()
        input_el.exists.return_value = True
        input_el.set_text.side_effect = lambda t: call_order.append("set_text")

        send_el = MagicMock()
        send_el.exists.return_value = True
        send_el.click.side_effect = lambda: call_order.append("click_send")

        device = _send_message_device(input_el, send_el)

        grok.send_message(device, "test")

        assert call_order == ["set_text", "click_send"]

    def test_send_raises_if_input_not_found(self) -> None:
        device = MagicMock()
        missing = MagicMock()
        missing.exists.return_value = False
        device.side_effect = lambda **kw: missing

        with pytest.raises(RuntimeError, match="Chat input field"):
            grok.send_message(device, "test")

    def test_send_raises_if_send_button_not_found(self) -> None:
        input_el = MagicMock()
        input_el.exists.return_value = True
        send_el = MagicMock()
        send_el.exists.return_value = False
        device = _send_message_device(input_el, send_el)

        with pytest.raises(RuntimeError, match="Send button"):
            grok.send_message(device, "test")


class TestWaitForResponse:
    @patch("grokmate.grok.time")
    def test_polls_until_loading_gone(self, mock_time: MagicMock) -> None:
        """Loading indicator appears twice, then disappears."""
        device = MagicMock()
        mock_time.monotonic.side_effect = [0, 1, 2, 3]  # 4 calls
        mock_time.sleep = MagicMock()

        call_count = 0

        def indicator_factory(**kwargs: object) -> MagicMock:
            nonlocal call_count
            el = MagicMock()
            if kwargs.get("text") in grok.LOADING_INDICATORS:
                # First 2 polls: loading. Then: gone.
                el.exists.return_value = call_count < 2
                call_count += 1
            else:
                el.exists.return_value = False
            return el

        device.side_effect = indicator_factory

        # Should not raise
        grok.wait_for_response(device, timeout=10)

    @patch("grokmate.grok.time")
    def test_times_out(self, mock_time: MagicMock) -> None:
        """Always loading → should raise TimeoutError."""
        device = MagicMock()
        # monotonic returns values that exceed timeout
        mock_time.monotonic.side_effect = list(range(0, 200))
        mock_time.sleep = MagicMock()

        # Loading indicator always present
        loading_el = MagicMock()
        loading_el.exists.return_value = True
        device.side_effect = lambda **kw: loading_el

        with pytest.raises(TimeoutError, match="60s"):
            grok.wait_for_response(device, timeout=60)


class TestIsContentText:
    """Tests for the _is_content_text helper."""

    def test_ui_chrome_excluded(self) -> None:
        assert not grok._is_content_text("Ask anything")
        assert not grok._is_content_text("Think Harder")
        assert not grok._is_content_text("Search")

    def test_very_short_excluded(self) -> None:
        assert not grok._is_content_text("")
        assert not grok._is_content_text("  ")
        assert not grok._is_content_text("ab")
        assert not grok._is_content_text("Yes")  # 3 chars

    def test_short_bullet_items_allowed(self) -> None:
        """Items > 3 chars that aren't chrome should pass through."""
        assert grok._is_content_text("Step 1")
        assert grok._is_content_text("• Use pip")
        assert grok._is_content_text("Done!")
        assert grok._is_content_text("Hello")

    def test_normal_content_allowed(self) -> None:
        assert grok._is_content_text("Here is a detailed answer about the topic.")
        assert grok._is_content_text("This is a paragraph with multiple sentences.")


class TestReadResponse:
    @staticmethod
    def _make_page_device(pages: list[list[str]]) -> MagicMock:
        """Build a mock device that returns successive pages of TextViews.

        Each call to ``d(className="android.widget.TextView")`` pops the next
        page from *pages*. After all pages are consumed the last one repeats.
        """
        device = MagicMock()
        idx = [0]

        def element_factory(**kwargs: object) -> MagicMock:
            if kwargs.get("className") == "android.widget.TextView":
                cur_texts = list(pages[min(idx[0], len(pages) - 1)])
                elements = MagicMock()
                elements.count = len(cur_texts)

                def getitem(i: int, _texts: list[str] = cur_texts) -> MagicMock:
                    el = MagicMock()
                    el.get_text.return_value = _texts[i]
                    return el

                elements.__getitem__ = MagicMock(side_effect=getitem)
                idx[0] += 1
                return elements
            return MagicMock()

        device.side_effect = element_factory
        device.swipe_ext = MagicMock()
        return device

    def test_two_phase_scroll_accumulates_full_text(self) -> None:
        """Phase 1 scrolls to top, phase 2 scrolls down accumulating."""
        pages = [
            # Phase 1: scroll to top — content is stable from the start
            ["First paragraph of the response."],
            ["First paragraph of the response."],  # stable ×1
            ["First paragraph of the response."],  # stable ×2 → top reached
            # Phase 2: scroll down accumulating
            ["First paragraph of the response."],
            ["First paragraph of the response.", "Second paragraph with details."],
            ["Second paragraph with details.", "Third and final paragraph."],
            ["Second paragraph with details.", "Third and final paragraph."],  # stable ×1
            ["Second paragraph with details.", "Third and final paragraph."],  # stable ×2
        ]
        device = self._make_page_device(pages)

        with patch("grokmate.grok.time"):
            result = grok.read_response(device)

        assert "First paragraph" in result
        assert "Second paragraph" in result
        assert "Third and final" in result
        # Must be joined, not just the longest block
        assert "\n\n" in result

    def test_response_filters_ui_chrome(self) -> None:
        """UI chrome strings are excluded from the accumulated content."""
        pages = [
            # Phase 1: stable immediately
            ["Ask anything", "Real content here."],
            ["Ask anything", "Real content here."],
            ["Ask anything", "Real content here."],
            # Phase 2: same screen, stable immediately
            ["Ask anything", "Real content here."],
            ["Ask anything", "Real content here."],
            ["Ask anything", "Real content here."],
        ]
        device = self._make_page_device(pages)

        with patch("grokmate.grok.time"):
            result = grok.read_response(device)

        assert "Real content here." in result
        assert "Ask anything" not in result

    def test_empty_response(self) -> None:
        """No TextViews found → empty string."""
        device = MagicMock()
        elements = MagicMock()
        elements.count = 0
        device.side_effect = lambda **kw: elements
        device.swipe_ext = MagicMock()

        with patch("grokmate.grok.time"):
            result = grok.read_response(device)

        assert result == ""

    def test_scrolls_up_then_down(self) -> None:
        """Verify swipe directions: phase 1 swipes 'down', phase 2 swipes 'up'."""
        pages = [
            # Phase 1: 3 reads to stabilise
            ["Content block."],
            ["Content block."],
            ["Content block."],
            # Phase 2: 3 reads to stabilise
            ["Content block."],
            ["Content block."],
            ["Content block."],
        ]
        device = self._make_page_device(pages)

        with patch("grokmate.grok.time"):
            grok.read_response(device)

        # Collect all swipe_ext calls
        swipe_calls = device.swipe_ext.call_args_list
        directions = [c[0][0] for c in swipe_calls]

        # Phase 1 should swipe "down" (scroll up), phase 2 should swipe "up" (scroll down)
        assert "down" in directions, "Phase 1 should swipe down to scroll to top"
        assert "up" in directions, "Phase 2 should swipe up to scroll through content"
        # "down" swipes should come before "up" swipes
        first_down = directions.index("down")
        last_up = len(directions) - 1 - directions[::-1].index("up")
        assert first_down < last_up


class TestTapNewChat:
    def test_tap_new_chat_clicks_button(self) -> None:
        device = MagicMock()
        btn = MagicMock()
        btn.exists.return_value = True

        def side_effect(**kw: object) -> MagicMock:
            # "Start new chat" button lookup
            if kw.get("description") == grok.NEW_CHAT_DESC:
                return btn
            # After clicking, _find_chat_input checks for the input field
            el = MagicMock()
            el.exists.return_value = True
            return el

        device.side_effect = side_effect

        with patch("grokmate.grok.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 0.1]
            mock_time.sleep = MagicMock()
            grok.tap_new_chat(device)

        btn.click.assert_called_once()

    def test_tap_new_chat_raises_if_not_found(self) -> None:
        device = MagicMock()
        btn = MagicMock()
        btn.exists.return_value = False
        device.side_effect = lambda **kw: btn

        with pytest.raises(RuntimeError, match="Start new chat"):
            grok.tap_new_chat(device)
