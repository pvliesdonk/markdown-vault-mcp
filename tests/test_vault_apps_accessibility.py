"""Accessible names in the served SPA, with a synthetic MCP Apps host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("playwright.sync_api")
if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Browser, Frame, Page

pytestmark = pytest.mark.browser
_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """Use Chromium when installed; keep the optional suite actionable otherwise."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium missing -- run `uv run playwright install chromium`")
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """Close each synthetic host page before the module's runtime stops."""
    instance = browser.new_page()
    yield instance
    instance.close()


def _open_vault(page: Page) -> Frame:
    """Load the served app with only synthetic host transport and no network."""
    host = (_ROOT / "tests/fixtures/vault_app_host.html").read_text()
    app = (_ROOT / "src/markdown_vault_mcp/static/app.html").read_text()
    page.route("**/*", lambda route: route.abort())
    page.route(
        "https://vault.test/**",
        lambda route: route.fulfill(
            content_type="text/html",
            body=app if route.request.url.endswith("/app.html") else host,
        ),
    )
    page.goto("https://vault.test/")
    frame = page.frames[1]
    frame.wait_for_function("window.vaultApp?.getHostContext()?.theme === 'light'")
    return frame


def test_context_heading_through_tool_lifecycle(page: Page) -> None:
    """A visible context heading has a name before and after tool responses."""
    frame = _open_vault(page)
    card = frame.locator("#context-card")
    heading = frame.locator("#ctx-title")
    assert not card.is_visible()
    success = {
        "path": "folder/example.md",
        "title": "Synthetic note",
        "folder": "folder",
        "frontmatter": {},
        "modified_at": 1_700_000_000.0,
        "backlinks": [],
        "outlinks": [],
        "similar": [],
        "folder_notes": [],
        "tags": {},
    }
    for result in (success, None, "error"):
        frame.evaluate("void window.loadContext('folder/example.md')")
        page.wait_for_function("window.pending.length === 1")
        assert card.is_visible()
        assert heading.aria_snapshot().strip().startswith('- heading "')
        assert heading.inner_text().strip()
        payload = {"content": [{"type": "text", "text": json.dumps(result)}]}
        page.evaluate(
            "([result, error]) => window.respond(result, error)",
            [payload, result == "error"],
        )
        if isinstance(result, dict):
            frame.get_by_role("heading", name=result["title"], exact=True).wait_for()
            assert card.is_visible()
            frame.evaluate("void window.loadContext(null)")
            assert not card.is_visible()
        else:
            card.wait_for(state="hidden")


def test_search_name_survives_populated_value(page: Page) -> None:
    """Typing and clearing do not remove the search field's accessible name."""
    frame = _open_vault(page)
    frame.locator('.tab-bar [data-tab="browse"]').click()
    search = frame.get_by_role("textbox", name="Search vault", exact=True)
    for query in ("", "synthetic query", ""):
        search.fill(query)
        assert search.input_value() == query
        assert 'textbox "Search vault"' in search.aria_snapshot()
