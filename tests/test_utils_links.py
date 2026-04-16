"""Tests for markdown_vault_mcp.utils.links."""

from __future__ import annotations

from markdown_vault_mcp.utils.links import (
    apply_link_replacement,
    compute_new_raw_target,
)

# ---------------------------------------------------------------------------
# compute_new_raw_target
# ---------------------------------------------------------------------------


class TestComputeNewRawTarget:
    def test_wikilink_without_md_extension(self) -> None:
        result = compute_new_raw_target(
            link_type="wikilink",
            raw_target="old-note",
            fragment=None,
            new_path="notes/new-note.md",
        )
        # Should strip .md since original didn't have it
        assert result == "notes/new-note"

    def test_wikilink_with_md_extension(self) -> None:
        result = compute_new_raw_target(
            link_type="wikilink",
            raw_target="old-note.md",
            fragment=None,
            new_path="notes/new-note.md",
        )
        assert result == "notes/new-note.md"

    def test_wikilink_with_fragment(self) -> None:
        result = compute_new_raw_target(
            link_type="wikilink",
            raw_target="old-note#heading",
            fragment="heading",
            new_path="new-note.md",
        )
        # original has no .md before #
        assert result == "new-note#heading"

    def test_markdown_link_root_relative(self) -> None:
        result = compute_new_raw_target(
            link_type="markdown",
            raw_target="notes/old.md",
            fragment=None,
            new_path="notes/new.md",
            source_path="index.md",
            old_path="notes/old.md",
        )
        # raw_target matches old_path, so root-relative
        assert result == "notes/new.md"

    def test_markdown_link_source_relative(self) -> None:
        result = compute_new_raw_target(
            link_type="markdown",
            raw_target="../archive/old.md",
            fragment=None,
            new_path="archive/new.md",
            source_path="docs/index.md",
            old_path="archive/old.md",
        )
        # raw_target != old_path, so recompute relative
        assert result == "../archive/new.md"

    def test_reference_link(self) -> None:
        result = compute_new_raw_target(
            link_type="reference",
            raw_target="notes/old.md",
            fragment=None,
            new_path="notes/new.md",
            source_path="index.md",
            old_path="notes/old.md",
        )
        assert result == "notes/new.md"

    def test_markdown_link_with_fragment(self) -> None:
        result = compute_new_raw_target(
            link_type="markdown",
            raw_target="old.md#section",
            fragment="section",
            new_path="new.md",
            source_path="index.md",
            old_path="old.md",
        )
        # raw_target (old.md#section) split on # gives old.md != old.md? No,
        # raw_path_part = old.md, old_path = old.md => root-relative
        assert result == "new.md#section"

    def test_wikilink_case_insensitive_md(self) -> None:
        result = compute_new_raw_target(
            link_type="wikilink",
            raw_target="old-note.MD",
            fragment=None,
            new_path="new-note.md",
        )
        # .MD endswith check is case-insensitive
        assert result == "new-note.md"


# ---------------------------------------------------------------------------
# apply_link_replacement
# ---------------------------------------------------------------------------


class TestApplyLinkReplacement:
    def test_markdown_link(self) -> None:
        content = "See [my link](old.md) for details."
        result = apply_link_replacement(content, "markdown", "old.md", "new.md")
        assert result == "See [my link](new.md) for details."

    def test_markdown_link_with_title(self) -> None:
        content = '[link](old.md "My Title")'
        result = apply_link_replacement(content, "markdown", "old.md", "new.md")
        assert result == '[link](new.md "My Title")'

    def test_markdown_image_not_affected(self) -> None:
        content = "![alt](old.md)"
        result = apply_link_replacement(content, "markdown", "old.md", "new.md")
        # Image links should NOT be replaced
        assert result == "![alt](old.md)"

    def test_wikilink(self) -> None:
        content = "See [[old-note]] for details."
        result = apply_link_replacement(content, "wikilink", "old-note", "new-note")
        assert result == "See [[new-note]] for details."

    def test_wikilink_alias_preserved(self) -> None:
        content = "See [[old-note|display text]] for details."
        result = apply_link_replacement(content, "wikilink", "old-note", "new-note")
        assert result == "See [[new-note|display text]] for details."

    def test_reference_link(self) -> None:
        content = "[ref]: old.md\nSee [ref] for details."
        result = apply_link_replacement(content, "reference", "old.md", "new.md")
        assert "[ref]: new.md" in result

    def test_reference_link_with_title(self) -> None:
        content = '[ref]: old.md "Title"'
        result = apply_link_replacement(content, "reference", "old.md", "new.md")
        assert result == '[ref]: new.md "Title"'

    def test_unknown_link_type_returns_unchanged(self) -> None:
        content = "some content"
        result = apply_link_replacement(content, "unknown", "old", "new")
        assert result == content

    def test_multiple_occurrences(self) -> None:
        content = "[[old]] and [[old]]"
        result = apply_link_replacement(content, "wikilink", "old", "new")
        assert result == "[[new]] and [[new]]"
