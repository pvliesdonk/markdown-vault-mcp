"""CommonMark's destination spellings name the same file (#1353).

An inline link's destination used to be read as raw text up to the first
``)``, so the pointy-bracket form, backslash escapes, balanced parentheses,
a trailing title and entity references were all stored as targets no file
can have. The destination is now parsed by CommonMark §6.3's grammar and
decoded in the order the design doc fixes (escapes and entities, then
percent-escapes, then the fragment split and the attachment classification).
Rows come from ``docs/design/reference/commonmark-gfm.md`` ("Inline links",
"Reference links and definitions") and the two tables on the issue.

``raw_target`` keeps the destination as written, title excluded, because the
rename path searches the file for it — and because a resolving link is now
a backlink a rename will rewrite, the rewrite must keep the pointy form.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.utils.links import (
    compute_new_raw_target,
    decode_markdown_destination,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path

SRC = "sub/src.md"


def one(content: str) -> tuple[str, str, str | None]:
    """The single link in *content* as ``(target_path, raw_target, fragment)``."""
    links = extract_links(content, SRC)
    assert len(links) == 1, links
    link = links[0]
    return link.target_path, link.raw_target, link.fragment


# ---------------------------------------------------------------------------
# The issue's table, exactly
# ---------------------------------------------------------------------------


class TestIssueTable:
    def test_pointy_brackets_hold_spaces(self) -> None:
        assert one("[x](<my note.md>)") == ("sub/my note.md", "<my note.md>", None)

    def test_escaped_parentheses(self) -> None:
        assert one("[x](a\\(b\\).md)") == ("sub/a(b).md", "a\\(b\\).md", None)

    def test_balanced_parentheses(self) -> None:
        assert one("[x](a(b).md)") == ("sub/a(b).md", "a(b).md", None)

    def test_trailing_title_is_not_part_of_the_target(self) -> None:
        assert one('[x](note.md "title")') == ("sub/note.md", "note.md", None)

    def test_entity_reference(self) -> None:
        assert one("[x](a&amp;b.md)") == ("sub/a&b.md", "a&amp;b.md", None)


# ---------------------------------------------------------------------------
# §6.3 rows
# ---------------------------------------------------------------------------


class TestPointyForm:
    def test_may_hold_a_closing_parenthesis(self) -> None:
        # Ex. 492: <b)c> is a destination.
        assert one("[x](<b)c.md>)") == ("sub/b)c.md", "<b)c.md>", None)

    def test_may_not_span_a_line_ending(self) -> None:
        # Ex. 491.
        assert extract_links("[x](<foo\nbar.md>)", SRC) == []

    def test_unescaped_angle_inside_is_not_a_destination(self) -> None:
        # Ex. 493: [a](<b>c>) is not a link.
        assert extract_links("[x](<b>c.md>)", SRC) == []

    def test_escaped_angle_inside_is_literal(self) -> None:
        assert one("[x](<a\\>b.md>)") == ("sub/a>b.md", "<a\\>b.md>", None)

    def test_with_a_title(self) -> None:
        assert one("[x](<my note.md> 'T')") == ("sub/my note.md", "<my note.md>", None)

    def test_fragment_inside_the_brackets(self) -> None:
        assert one("[x](<my note.md#Heading>)") == (
            "sub/my note.md",
            "<my note.md#Heading>",
            "Heading",
        )

    def test_unterminated_pointy_destination_is_no_link(self) -> None:
        assert extract_links("[x](<my note.md", SRC) == []

    def test_empty_pointy_destination_is_no_link(self) -> None:
        # Ex. 485 / 489: an empty destination names nothing here.
        assert extract_links("[x](<>)", SRC) == []


class TestPlainForm:
    def test_three_levels_of_balanced_parentheses(self) -> None:
        # Ex. 496-498.
        assert one("[x](a(b(c(d).md)))") == ("sub/a(b(c(d).md))", "a(b(c(d).md))", None)

    def test_four_levels_of_nesting_is_the_known_cap(self) -> None:
        # §6.3 allows any balanced depth; the scanner honours three, the
        # depth the spec's own examples reach, so a pathological line stays
        # one linear regex pass. Pinned as the recorded cost.
        assert extract_links("[x](a(b(c(d(e).md))))", SRC) == []

    def test_unbalanced_parenthesis_is_no_link(self) -> None:
        # Ex. 494: [link](foo(and(bar)) — the destination never closes.
        assert extract_links("[x](a(b.md)", SRC) == []

    def test_a_space_still_ends_nothing(self) -> None:
        # The recorded departure: spaces are accepted in a plain destination,
        # so a spaced name written without brackets keeps resolving as it
        # did before this change.
        assert one("[x](my note.md)") == ("sub/my note.md", "my note.md", None)

    def test_escaped_space_is_literal(self) -> None:
        # ``\ `` is not an escape (backslash before non-punctuation), Ex. 13.
        assert one("[x](a\\ b.md)") == ("sub/a\\ b.md", "a\\ b.md", None)

    def test_escaped_asterisk(self) -> None:
        # Ex. 22 via the reference's observation: [a](x\*.md) → x*.md.
        assert one("[x](x\\*.md)") == ("sub/x*.md", "x\\*.md", None)

    def test_numeric_entity(self) -> None:
        # Ex. 32 via the reference's observation: [a](x&#46;md) → x.md.
        assert one("[x](x&#46;md)") == ("sub/x.md", "x&#46;md", None)

    def test_invalid_entity_stays_literal(self) -> None:
        assert one("[x](a&notanentity;b.md)") == (
            "sub/a&notanentity;b.md",
            "a&notanentity;b.md",
            None,
        )

    def test_escape_then_percent_decode_in_that_order(self) -> None:
        # ``\%`` is a literal percent sign, so the decoded destination holds
        # ``%5B``, which the URL layer then reads: escapes first, percent
        # second (design.md, "Order against the other decodings").
        assert one("[x](b\\%5B1%5D.md)") == ("sub/b[1].md", "b\\%5B1%5D.md", None)

    def test_a_space_before_the_title_is_optional_whitespace(self) -> None:
        assert one("[x](note.md   'T')") == ("sub/note.md", "note.md", None)

    def test_parenthesised_title(self) -> None:
        assert one("[x](note.md (T))") == ("sub/note.md", "note.md", None)

    def test_leading_whitespace_before_the_destination(self) -> None:
        # Ex. 510: spaces or tabs may surround the destination.
        assert one("[x](  note.md  )") == ("sub/note.md", "note.md", None)

    def test_empty_destination_is_no_link(self) -> None:
        # Ex. 485 allows an empty destination; here it names nothing.
        assert extract_links("[x]()", SRC) == []
        assert extract_links("[x](   )", SRC) == []

    def test_nul_reference_becomes_the_replacement_character(self) -> None:
        # §2.5: ``&#0;`` decodes to U+FFFD.
        assert one("[x](a&#0;b.md)") == ("sub/a\ufffdb.md", "a&#0;b.md", None)

    def test_escaped_hash_is_part_of_the_name(self) -> None:
        # ``\#`` is a literal ``#`` (§2.4), not the fragment marker.
        assert one("[x](a\\#b.md)") == ("sub/a#b.md", "a\\#b.md", None)

    def test_entity_without_semicolon_stays_literal(self) -> None:
        # CommonMark §2.5 requires the ``;``; HTML5's legacy ``&not`` does not
        # apply, so ``&notes`` is not ``¬es``.
        assert one("[x](a&notes.md)") == ("sub/a&notes.md", "a&notes.md", None)

    def test_a_link_written_inside_a_title_is_not_a_second_link(self) -> None:
        # One link to x.md whose title happens to hold link syntax; the scan
        # resumes after the whole link, not inside its title.
        assert one('[a](x.md "see [b](y.md)")') == ("sub/x.md", "x.md", None)

    def test_an_escaped_backslash_before_hash_is_a_fragment(self) -> None:
        # ``\\#``: the backslash is escaped, so the ``#`` is the marker.
        assert one("[x](a\\\\#b.md)") == ("sub/a\\", "a\\\\#b.md", "b.md")

    def test_a_hash_after_a_stray_ampersand_is_still_a_fragment(self) -> None:
        # ``&#zzz`` is not a numeric reference, so its ``#`` is the marker.
        assert one("[x](a&#zzz.md)") == ("sub/a&", "a&#zzz.md", "zzz.md")

    def test_a_surrogate_reference_becomes_the_replacement_character(self) -> None:
        # §2.5: a surrogate is not a valid code point; a lone one would not
        # even survive UTF-8 into the index.
        assert one("[x](a&#xD800;b.md)") == ("sub/a\ufffdb.md", "a&#xD800;b.md", None)

    def test_a_non_breaking_space_is_a_destination_character(self) -> None:
        # Ex. 507: only spaces and tabs separate a title.
        assert one('[x](url\u00a0"title")') == (
            'sub/url\u00a0"title"',
            'url\u00a0"title"',
            None,
        )

    def test_delete_is_a_control_character(self) -> None:
        assert extract_links("[x](a\x7fb.md)", SRC) == []

    def test_a_backslash_does_not_escape_a_line_ending_in_the_pointy_form(
        self,
    ) -> None:
        assert extract_links("[x](<a\\\nb.md>)", SRC) == []

    def test_an_escaped_ampersand_does_not_start_a_reference(self) -> None:
        # ``\&#46;`` is a literal ``&`` then ``#46;`` — one pass, escape
        # first; the ``#`` after an escaped ``&`` is then the fragment marker.
        assert one("[x](a\\&#46;md)") == ("sub/a&", "a\\&#46;md", "46;md")

    def test_a_non_breaking_space_at_the_edge_is_kept(self) -> None:
        # Only spaces and tabs are trimmed around a plain destination.
        assert one("[x](x.md\u00a0)") == ("sub/x.md\u00a0", "x.md\u00a0", None)

    def test_a_line_ending_inside_the_parentheses_is_still_refused(self) -> None:
        # The #1334 departure stands: no line ending anywhere inside.
        assert extract_links("[x](note.md\n'T')", SRC) == []


class TestAnchorsAreJudgedAsWritten:
    """A leading literal ``#`` in a file name is not a same-document anchor."""

    @pytest.mark.parametrize(
        ("content", "target"),
        [
            ("[a](%23x.md)", "sub/#x.md"),
            ("[a](\\#x.md)", "sub/#x.md"),
            ("[a](&#35;x.md)", "sub/#x.md"),
            ("[a](<%23x.md>)", "sub/#x.md"),
        ],
        ids=["percent", "escaped", "entity", "pointy-percent"],
    )
    def test_a_file_named_with_a_leading_hash_is_a_link(
        self, content: str, target: str
    ) -> None:
        assert one(content)[0] == target

    @pytest.mark.parametrize("content", ["[a](#h)", "[a](<#h>)", "[a](#)"])
    def test_a_bare_fragment_is_still_an_anchor(self, content: str) -> None:
        assert extract_links(content, SRC) == []

    def test_a_definition_with_a_leading_hash_name_is_a_link(self) -> None:
        assert one("[r]: %23x.md\n\nSee [a][r].")[0] == "sub/#x.md"


class TestReferenceDefinitions:
    def test_pointy_destination(self) -> None:
        content = "[r]: <my note.md>\n\nSee [x][r]."
        assert one(content) == ("sub/my note.md", "<my note.md>", None)

    def test_pointy_destination_with_title(self) -> None:
        content = '[r]: <my note.md> "T"\n\nSee [x][r].'
        assert one(content) == ("sub/my note.md", "<my note.md>", None)

    def test_escaped_and_entity_destination(self) -> None:
        content = "[r]: a\\(b\\)&amp;c.md\n\nSee [x][r]."
        assert one(content) == ("sub/a(b)&c.md", "a\\(b\\)&amp;c.md", None)


# ---------------------------------------------------------------------------
# The attachment rows from the issue (#1333's rule reaches these spellings)
# ---------------------------------------------------------------------------


class TestAttachmentRows:
    PDF = frozenset({"pdf"})

    @pytest.mark.parametrize(
        "content",
        [
            '[paper](report.pdf "PDF")',
            "[paper](<my report.pdf>)",
            "[paper](<report.pdf>)",
            "[r][x]\n\n[x]: <my report.pdf>",
        ],
        ids=["titled", "pointy-spaced", "pointy", "definition-pointy"],
    )
    def test_an_attachment_in_any_spelling_is_not_a_link(self, content: str) -> None:
        assert extract_links(content, SRC, attachment_extensions=self.PDF) == []


# ---------------------------------------------------------------------------
# The decoder, on its own
# ---------------------------------------------------------------------------


class TestDecodeMarkdownDestination:
    def test_pointy_brackets_are_removed(self) -> None:
        assert decode_markdown_destination("<my note.md>") == "my note.md"

    def test_escapes_and_entities_then_percent(self) -> None:
        assert decode_markdown_destination("a\\(b\\)&amp;%5B1%5D.md") == "a(b)&[1].md"

    def test_a_refused_percent_escape_still_leaves_that_layer_as_written(
        self,
    ) -> None:
        assert decode_markdown_destination("dir%2Fnote.md") == "dir%2Fnote.md"

    def test_a_plain_destination_is_unchanged(self) -> None:
        assert decode_markdown_destination("notes/topic.md") == "notes/topic.md"


# ---------------------------------------------------------------------------
# The forced consequence: a rename keeps the pointy form
# ---------------------------------------------------------------------------


class TestRenameKeepsTheSpelling:
    def test_pointy_root_relative_stays_pointy(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown",
                "</old name.md>",
                None,
                "new name.md",
                "sub/src.md",
                "old name.md",
            )
            == "</new name.md>"
        )

    def test_pointy_relative_with_fragment_stays_pointy(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown",
                "<old name.md#Top>",
                "Top",
                "sub/new name.md",
                "sub/src.md",
                "sub/old name.md",
            )
            == "<new name.md#Top>"
        )

    def test_escaped_destination_is_recognised_as_the_old_path(self) -> None:
        # Compared decoded, so the root-relative shape is kept, not turned
        # into a relative path (the #1105 / #1332 fidelity rule).
        assert (
            compute_new_raw_target(
                "markdown", "a\\(b\\).md", None, "c.md", "sub/src.md", "a(b).md"
            )
            == "c.md"
        )

    def test_numeric_reference_hash_is_not_a_fragment_on_rename_either(self) -> None:
        # ``x&#46;md`` is root-relative ``x.md``; the ``#`` inside the
        # reference must not split it, or the shape test would see ``x&``
        # and rewrite a root-relative link as a relative one.
        assert (
            compute_new_raw_target(
                "markdown", "x&#46;md", None, "y.md", "sub/src.md", "x.md"
            )
            == "y.md"
        )

    def test_a_new_name_with_a_hash_is_escaped_so_it_stays_a_file(self) -> None:
        # Written literally, ``#y.md`` would re-parse as an anchor and the
        # backlink would vanish; the one escape a rename does introduce.
        new_raw = compute_new_raw_target(
            "markdown", "x.md", None, "#y.md", "src.md", "x.md"
        )
        assert new_raw == "\\#y.md"
        assert one(f"[a]({new_raw})")[0] == "sub/#y.md"

    def test_a_new_name_with_an_angle_bracket_is_escaped_in_the_pointy_form(
        self,
    ) -> None:
        new_raw = compute_new_raw_target(
            "markdown", "<old.md>", None, "new>x.md", "src.md", "old.md"
        )
        assert new_raw == "<new\\>x.md>"
        assert one(f"[a]({new_raw})")[0] == "sub/new>x.md"

    def test_end_to_end_rename_rewrites_a_pointy_link(self, tmp_path: Path) -> None:
        (tmp_path / "my note.md").write_text("# Target\n")
        (tmp_path / "src.md").write_text("See [it](<my note.md> 'T').\n")
        vault = Vault(source_dir=tmp_path, read_only=False)
        vault.index.build_index()
        try:
            result = vault.writer.rename("my note.md", "new name.md", update_links=True)
            assert result.updated_links == 1
            assert (tmp_path / "src.md").read_text() == "See [it](<new name.md> 'T').\n"
        finally:
            vault.close()
