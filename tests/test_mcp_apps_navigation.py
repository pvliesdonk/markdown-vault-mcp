"""Tests for cross-view navigation and send-to-LLM.

Covers issue #277: navigateTo function, all view-to-view navigation paths,
sendToLLM, updateContext, and toast confirmation.
"""

from __future__ import annotations

import pytest

from tests.conftest import get_app_html


@pytest.mark.usefixtures("_mcp_env")
class TestCrossViewNavigation:
    """Verify all cross-view navigation paths are wired in the HTML."""

    # AC #1: navigateTo function
    async def test_navigate_to_function_exists(self) -> None:
        html = await get_app_html()
        assert "window.navigateTo" in html
        assert "function navigateTo" in html
        assert "switchTab" in html

    # AC #2: Graph double-click → focus mode (clear + reload for this node)
    async def test_graph_to_context_on_dblclick(self) -> None:
        html = await get_app_html()
        assert "doubleClick" in html
        # Double-click now triggers focus mode via loadGraph(nodeId)
        assert "loadGraph(nodeId)" in html
        # Context navigation still available via mini-card "Full Context" button
        assert "navigateTo('context'" in html

    # AC #3: Graph → Browser (mini card button)
    async def test_graph_to_browser_mini_card(self) -> None:
        html = await get_app_html()
        assert "Open in Browser" in html
        assert "mini-open-browser" in html
        assert "navigateTo('browse'" in html

    # AC #4: Context → Graph (button)
    async def test_context_to_graph_button(self) -> None:
        html = await get_app_html()
        assert 'id="ctx-graph-btn"' in html
        assert "navigateTo('graph'" in html

    # AC #5: Context → Browser (backlink/outlink clicks)
    async def test_context_to_browser_button(self) -> None:
        html = await get_app_html()
        assert 'id="ctx-browse-btn"' in html

    # AC #6: Browser → Context (button in preview)
    async def test_browser_to_context_button(self) -> None:
        html = await get_app_html()
        assert "preview-ctx-btn" in html

    # AC #7: Browser → Graph (button in preview)
    async def test_browser_to_graph_button(self) -> None:
        html = await get_app_html()
        assert "preview-graph-btn" in html

    # AC #8: Mini context card on graph single click
    async def test_graph_mini_card_exists(self) -> None:
        html = await get_app_html()
        assert "graph-mini-card" in html
        assert "showMiniCard" in html

    # AC #9: Mini card has Full Context + Open in Browser
    async def test_mini_card_buttons(self) -> None:
        html = await get_app_html()
        assert "Full Context" in html
        assert "mini-full-ctx" in html


@pytest.mark.usefixtures("_mcp_env")
class TestSendToLLM:
    """Verify standardized send-to-LLM function."""

    # AC #10: Shared sendToLLM function
    async def test_send_to_llm_function(self) -> None:
        html = await get_app_html()
        assert "window.sendToLLM" in html
        assert "app.sendMessage" in html

    # AC #11: Content truncation
    async def test_truncation_at_4000_chars(self) -> None:
        html = await get_app_html()
        assert "4000" in html
        assert "truncated" in html

    # AC #12: Send button in all views
    async def test_send_buttons_across_views(self) -> None:
        html = await get_app_html()
        # Context view
        assert "ctx-send-btn" in html
        # Graph view
        assert "graph-send-btn" in html
        # Browser view
        assert "preview-send-btn" in html

    # AC #13: Toast confirmation
    async def test_toast_after_send(self) -> None:
        html = await get_app_html()
        assert "showToast" in html
        assert "Sent to Claude" in html
        assert 'class="toast"' in html


@pytest.mark.usefixtures("_mcp_env")
class TestAmbientContext:
    """Verify standardized updateContext function."""

    # AC #14: Shared updateContext function
    async def test_update_context_function(self) -> None:
        html = await get_app_html()
        assert "window.updateContext" in html
        assert "updateModelContext" in html

    async def test_context_view_calls_update(self) -> None:
        html = await get_app_html()
        assert "'context card'" in html

    async def test_graph_view_calls_update(self) -> None:
        html = await get_app_html()
        assert "'graph explorer'" in html

    async def test_browser_view_calls_update(self) -> None:
        html = await get_app_html()
        assert "'browser'" in html
