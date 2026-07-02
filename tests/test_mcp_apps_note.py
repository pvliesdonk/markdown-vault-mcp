"""Tests for the Note Preview MCP App view (Paper redesign, #814).

Covers the Paper-styled note preview: serif header with mono path subtitle,
a Contents (table-of-contents) popover derived from rendered headings,
collapsible frontmatter properties and tags, a Paper markdown body with the
leading frontmatter block stripped before rendering, and an action footer with
copy-markdown / copy-vault-link controls alongside the preserved send/nav
buttons.
"""

from __future__ import annotations

import pytest

from tests.conftest import get_app_html


@pytest.mark.usefixtures("_mcp_env")
class TestNotePreviewHeader:
    """Paper header: serif title, mono path subtitle, Contents popover."""

    async def test_paper_header_elements(self) -> None:
        html = await get_app_html()
        assert "preview-title" in html
        assert "preview-path" in html

    async def test_title_uses_serif_head_font(self) -> None:
        html = await get_app_html()
        assert (
            "font-family: var(--font-head); font-weight: 600; font-size: 19px" in html
        )

    async def test_path_uses_mono_font(self) -> None:
        html = await get_app_html()
        assert ".preview-path {" in html
        assert "var(--font-mono)" in html

    async def test_contents_toc_popover(self) -> None:
        html = await get_app_html()
        assert "preview-toc-btn" in html
        assert 'id="preview-toc"' in html
        assert "Contents" in html
        assert "On this page" in html

    async def test_toc_built_from_rendered_headings(self) -> None:
        html = await get_app_html()
        # ToC is derived from the rendered DOM headings, not the raw markdown.
        assert "querySelectorAll('h1, h2, h3')" in html

    async def test_contents_button_hidden_when_no_headings(self) -> None:
        html = await get_app_html()
        # A note with no h1-h3 headings hides the Contents button entirely.
        assert "preview-toc-btn').style.display" in html
        assert "'none'" in html

    async def test_contents_button_toggles_popover(self) -> None:
        html = await get_app_html()
        # Clicking Contents toggles the popover; stopPropagation keeps the
        # outside-click handler from closing it in the same tick.
        assert "tocEl.hidden = !tocEl.hidden" in html
        assert "e.stopPropagation()" in html

    async def test_toc_item_scrolls_to_heading(self) -> None:
        html = await get_app_html()
        assert "scrollIntoView" in html

    async def test_toc_closes_on_outside_click(self) -> None:
        html = await get_app_html()
        assert "preview-toc-wrap" in html
        assert "closest('.preview-toc-wrap')" in html

    async def test_toc_closes_on_tab_change(self) -> None:
        html = await get_app_html()
        assert "vault-tab-changed" in html


@pytest.mark.usefixtures("_mcp_env")
class TestNotePreviewFrontmatter:
    """Frontmatter is stripped from the body and shown as collapsible props."""

    async def test_frontmatter_stripped_before_render(self) -> None:
        html = await get_app_html()
        assert "stripFrontmatter" in html
        # marked.parse receives the stripped body, not the raw content.
        assert "marked.parse(body)" in html

    async def test_strip_regex_matches_server_fence_grammar(self) -> None:
        html = await get_app_html()
        # The server's python-frontmatter parser fences on ``^-{3,}\\s*$``; the
        # strip regex must at least cover the realistic cases (3+ dashes, then
        # optional spaces/tabs) else a ``----`` or ``--- `` fence renders the
        # frontmatter twice.
        assert "-{3,}[ \\t]*\\r?\\n" in html

    async def test_collapsible_properties(self) -> None:
        html = await get_app_html()
        assert "preview-props" in html
        assert "prop-key" in html
        assert "prop-val" in html
        assert "preview-props-toggle" in html
        assert "more properties" in html

    async def test_collapsed_props_hide_extras(self) -> None:
        html = await get_app_html()
        assert ".preview-props.collapsed .prop-extra { display: none; }" in html

    async def test_tags_excluded_from_properties(self) -> None:
        html = await get_app_html()
        # `tags` renders only as chips, never duplicated as a property row.
        assert "k !== 'tags'" in html

    async def test_collapsible_tags(self) -> None:
        html = await get_app_html()
        assert "preview-tags" in html
        assert ".preview-tags.collapsed .tag-extra { display: none; }" in html

    async def test_tags_expand_toggle_present(self) -> None:
        html = await get_app_html()
        # The "+N more" tags control (and its wiring) must be present so tags
        # beyond the first few are reachable.
        assert "preview-tags-toggle" in html
        assert "tag-toggle" in html

    async def test_tags_normalized_from_string_or_list(self) -> None:
        html = await get_app_html()
        # normalizeTags accepts both a YAML list and a comma/space string.
        assert "normalizeTags" in html

    async def test_expand_toggle_swaps_label(self) -> None:
        html = await get_app_html()
        # Expanding a collapsed props/tags block swaps the label to "Show less".
        assert "Show less" in html


@pytest.mark.usefixtures("_mcp_env")
class TestNotePreviewActions:
    """Action footer: copy markdown / copy vault link + preserved buttons."""

    async def test_copy_markdown_uses_full_content(self) -> None:
        html = await get_app_html()
        assert "preview-copy-md" in html
        assert "Copy markdown" in html
        # Copy-markdown copies the full raw content (frontmatter included).
        assert "copyToClipboard(data.content" in html

    async def test_copy_vault_link_uses_path(self) -> None:
        html = await get_app_html()
        assert "preview-copy-link" in html
        assert "Copy vault link" in html
        assert "copyToClipboard(data.path" in html

    async def test_copy_success_shows_transient_confirmation(self) -> None:
        html = await get_app_html()
        # On success the button label swaps to a transient confirmation.
        assert "Copied" in html
        assert "1500" in html

    async def test_preserved_actions(self) -> None:
        html = await get_app_html()
        assert "preview-send-btn" in html
        assert "preview-browse-btn" in html
        assert "preview-ctx-btn" in html
        assert "preview-graph-btn" in html

    async def test_disabled_edit_button(self) -> None:
        html = await get_app_html()
        assert "edit-btn-disabled" in html
        assert "Coming soon" in html

    async def test_paper_footer_styling(self) -> None:
        html = await get_app_html()
        assert "preview-footer" in html

    async def test_copy_failure_falls_back_to_toast_and_logs(self) -> None:
        html = await get_app_html()
        # A clipboard rejection surfaces a toast and logs the error for debugging.
        assert "Copy failed" in html
        assert "clipboard write failed" in html


@pytest.mark.usefixtures("_mcp_env")
class TestNotePreviewErrorStates:
    """Empty-read placeholder, render-failure logging, and navigation routing."""

    async def test_missing_note_placeholder(self) -> None:
        html = await get_app_html()
        assert "Note not found" in html

    async def test_render_failure_is_logged(self) -> None:
        html = await get_app_html()
        # A render/wiring error is logged (not just shown) so it is debuggable.
        assert "note preview render failed" in html

    async def test_browse_navigation_keeps_tab(self) -> None:
        html = await get_app_html()
        # Navigating with view='browse' loads the preview without stealing the tab.
        assert "switchToNote" in html
