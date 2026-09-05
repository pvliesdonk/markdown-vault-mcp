"""Rename rewrites of percent-encoded link targets (#1332).

An encoded destination and its literal spelling name the same file, so a
link-updating rename must find and rewrite both. Before #1332 it rewrote
only the literal one and left the encoded link pointing at a path that no
longer existed.
"""

from __future__ import annotations

import pytest

from markdown_vault_mcp.utils import apply_link_replacement, compute_new_raw_target


class TestComputeNewRawTargetEncoded:
    def test_root_relative_encoded_target_is_recognised(self) -> None:
        """The encoded spelling must compare equal to old_path, not fall
        through to the relative-to-source branch."""
        assert (
            compute_new_raw_target(
                "markdown",
                "probe/b%5B1%5D.md",
                None,
                "probe/b2.md",
                source_path="hub.md",
                old_path="probe/b[1].md",
            )
            == "probe/b2.md"
        )

    def test_root_relative_encoded_target_from_a_subfolder_source(self) -> None:
        """The shape test compares raw_target to old_path, so an encoded
        target never matched and fell into the relative-to-source branch.

        From a root-level source that branch coincidentally produces the
        right string; from a subfolder it converts a root-relative link into
        a relative one, which is the fidelity defect #1105 was about.
        """
        assert (
            compute_new_raw_target(
                "markdown",
                "probe/b%5B1%5D.md",
                None,
                "probe/b2.md",
                source_path="sub/hub.md",
                old_path="probe/b[1].md",
            )
            == "probe/b2.md"
        )

    def test_leading_slash_encoded_target_keeps_its_shape(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown",
                "/probe/b%5B1%5D.md",
                None,
                "probe/b2.md",
                source_path="hub.md",
                old_path="probe/b[1].md",
            )
            == "/probe/b2.md"
        )

    def test_a_new_name_needing_escapes_is_re_encoded(self) -> None:
        """The author wrote an encoded destination; the rewrite keeps that
        convention rather than emitting raw brackets into a URL slot."""
        assert (
            compute_new_raw_target(
                "markdown",
                "/probe/b%5B1%5D.md",
                None,
                "probe/c[2].md",
                source_path="hub.md",
                old_path="probe/b[1].md",
            )
            == "/probe/c%5B2%5D.md"
        )

    def test_an_unencoded_target_is_still_written_literally(self) -> None:
        """No encoding is introduced where the author used none (#1105)."""
        assert (
            compute_new_raw_target(
                "markdown",
                "probe/b[1].md",
                None,
                "probe/c[2].md",
                source_path="hub.md",
                old_path="probe/b[1].md",
            )
            == "probe/c[2].md"
        )

    def test_fragment_is_preserved(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown",
                "/p/my%20note.md",
                "sec",
                "p/renamed.md",
                source_path="hub.md",
                old_path="p/my note.md",
            )
            == "/p/renamed.md#sec"
        )

    def test_relative_to_source_encoded_target(self) -> None:
        """A genuinely relative encoded link still gets a relative answer."""
        assert (
            compute_new_raw_target(
                "markdown",
                "../p/my%20note.md",
                None,
                "p/renamed.md",
                source_path="sub/hub.md",
                old_path="p/my note.md",
            )
            == "../p/renamed.md"
        )

    @pytest.mark.parametrize("link_type", ["markdown", "reference"])
    def test_both_url_shaped_link_types(self, link_type: str) -> None:
        assert (
            compute_new_raw_target(
                link_type,
                "/p/a%20b.md",
                None,
                "p/c.md",
                source_path="hub.md",
                old_path="p/a b.md",
            )
            == "/p/c.md"
        )

    def test_wikilinks_are_untouched_by_the_encoding_rule(self) -> None:
        """A wikilink target is a literal name; ``%20`` is part of it."""
        assert (
            compute_new_raw_target(
                "wikilink",
                "notes/100%20plan",
                None,
                "notes/renamed.md",
                source_path="hub.md",
                old_path="notes/100%20plan.md",
            )
            == "notes/renamed"
        )


class TestApplyReplacementEncoded:
    def test_the_encoded_occurrence_is_rewritten_in_place(self) -> None:
        content = "See [x](/probe/b%5B1%5D.md) and [y](/probe/other.md)\n"
        out = apply_link_replacement(
            content, "markdown", "/probe/b%5B1%5D.md", "/probe/b2.md"
        )
        assert out == "See [x](/probe/b2.md) and [y](/probe/other.md)\n"
