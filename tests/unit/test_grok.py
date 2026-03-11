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


class TestSendMessage:
    def test_set_text_uses_uiautomator_not_adb_input(self) -> None:
        """Ensure we use .set_text() on the EditText, never adb shell input text."""
        device = MagicMock()
        input_el = MagicMock()
        input_el.exists.return_value = True
        send_el = MagicMock()
        send_el.exists.return_value = True

        def side_effect(**kwargs: object) -> MagicMock:
            if kwargs.get("resourceId") == grok.RES_CHAT_INPUT:
                return input_el
            if kwargs.get("resourceId") == grok.RES_SEND_BUTTON:
                return send_el
            return MagicMock()

        device.side_effect = side_effect

        grok.send_message(device, "Hello, world!")

        # set_text must be called (not adb input text)
        input_el.set_text.assert_called_once_with("Hello, world!")

    def test_send_button_tapped_after_text_set(self) -> None:
        """Ensure the send button is clicked AFTER text is set."""
        device = MagicMock()
        call_order: list[str] = []

        input_el = MagicMock()
        input_el.exists.return_value = True
        input_el.set_text.side_effect = lambda t: call_order.append("set_text")

        send_el = MagicMock()
        send_el.exists.return_value = True
        send_el.click.side_effect = lambda: call_order.append("click_send")

        def side_effect(**kwargs: object) -> MagicMock:
            if kwargs.get("resourceId") == grok.RES_CHAT_INPUT:
                return input_el
            if kwargs.get("resourceId") == grok.RES_SEND_BUTTON:
                return send_el
            return MagicMock()

        device.side_effect = side_effect

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
        device = MagicMock()
        input_el = MagicMock()
        input_el.exists.return_value = True
        send_el = MagicMock()
        send_el.exists.return_value = False

        def side_effect(**kwargs: object) -> MagicMock:
            if kwargs.get("resourceId") == grok.RES_CHAT_INPUT:
                return input_el
            if kwargs.get("resourceId") == grok.RES_SEND_BUTTON:
                return send_el
            return MagicMock()

        device.side_effect = side_effect

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


class TestReadResponse:
    def test_response_scrolls_to_accumulate_full_text(self) -> None:
        """Simulate scrolling that reveals new text, then stabilises."""
        device = MagicMock()

        pages = [
            ["Hello", "How can I help?"],
            ["Hello", "How can I help?", "Here is a detailed answer."],
            ["Hello", "How can I help?", "Here is a detailed answer."],  # stable
            ["Hello", "How can I help?", "Here is a detailed answer."],  # stable x2
        ]
        page_idx = [0]

        def element_factory(**kwargs: object) -> MagicMock:
            if kwargs.get("className") == "android.widget.TextView":
                elements = MagicMock()
                cur_texts = list(pages[min(page_idx[0], len(pages) - 1)])
                elements.count = len(cur_texts)

                def getitem(i: int, _texts: list[str] = cur_texts) -> MagicMock:
                    el = MagicMock()
                    el.get_text.return_value = _texts[i]
                    return el

                elements.__getitem__ = MagicMock(side_effect=getitem)
                page_idx[0] += 1
                return elements
            return MagicMock()

        device.side_effect = element_factory
        device.swipe_ext = MagicMock()

        with patch("grokmate.grok.time"):
            result = grok.read_response(device)

        assert "detailed answer" in result

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


class TestTapNewChat:
    def test_tap_new_chat_clicks_button(self) -> None:
        device = MagicMock()
        btn = MagicMock()
        btn.exists.return_value = True
        device.side_effect = lambda **kw: btn

        with patch("grokmate.grok.time"):
            grok.tap_new_chat(device)

        btn.click.assert_called_once()

    def test_tap_new_chat_raises_if_not_found(self) -> None:
        device = MagicMock()
        btn = MagicMock()
        btn.exists.return_value = False
        device.side_effect = lambda **kw: btn

        with pytest.raises(RuntimeError, match="Start new chat"):
            grok.tap_new_chat(device)
