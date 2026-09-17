"""A destination the server writes is markdown, not a raw path (#1494).

Three producers interpolate a vault path into a markdown destination: the
OKF index builder, the OKF wikilink converter, and the rename/move link
rewriter. A vault path may hold a space; a *plain* markdown destination may
not (CommonMark 0.31.2 §6.3, recorded in
``docs/design/reference/commonmark-gfm.md``, "Inline links"), so a path
written verbatim stopped being a link the moment a note or folder name
carried one — and Obsidian agrees it is no link
(``docs/design/reference/obsidian-markdown.md``, "Markdown links Obsidian
writes": ``[w](Sub/My Note.md)`` is not a link, while Obsidian's own writer
emits ``%20``).

Resolution through this project's own index proves nothing here: the scanner
is deliberately tolerant and reads the raw-space, pointy and percent-encoded
spellings all onto the same note (a departure pinned in the same reference).
So every case below asserts the *emitted spelling* as well, and the
spec-property test asserts the one thing §6.3 actually forbids.

Spaces are the *invalidity* half. The other half is a name that stays a
link and means something else: a ``#`` read as a fragment, or an unbalanced
parenthesis ending the destination early so the link resolves to the shorter
path (#1513, #1516). Those are escaped rather than encoded, and only when
they would not parse — a balanced ``a(b).md`` is legal and stays literal.
A bracket in generated *link text* is escaped too; that output is valid
CommonMark but this project's own scanner still will not index it (#1517),
which the tests below state rather than paper over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.okf import build_index_markdown, convert_wikilinks_to_markdown
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import OutlinkInfo
from markdown_vault_mcp.utils.links import (
    _MAX_PLAIN_PAREN_DEPTH,
    build_plain_destination,
    compute_new_raw_target,
    encode_plain_destination,
    escape_link_text,
)
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SRC = "source.md"

#: What §6.3 forbids in a plain destination, minus the NUL no path holds.
_FORBIDDEN = " \t\n\r\x0b\x0c\x7f"


def _wl(
    raw_target: str, target_path: str, *, fragment: str | None = None
) -> OutlinkInfo:
    return OutlinkInfo(
        target_path=target_path,
        link_text=raw_target,
        link_type="wikilink",
        raw_target=raw_target,
        fragment=fragment,
        exists=True,
    )


def _destination(content: str) -> str:
    """The single link's destination in *content*, as written."""
    links = extract_links(content, SRC)
    assert len(links) == 1, links
    return links[0].raw_target


def _index_line(title: str, path: str) -> str:
    """The one entry line ``build_index_markdown`` writes for *title*."""
    return build_index_markdown("I", [(title, path, None)]).splitlines()[-1]


def _one(line: str) -> tuple[str, str | None, str]:
    """The single link in *line* as ``(target_path, fragment, link_text)``."""
    links = extract_links(line, "index.md")
    assert len(links) == 1, links
    link = links[0]
    return link.target_path, link.fragment, link.link_text


# ---------------------------------------------------------------------------
# The issue's table, exactly
# ---------------------------------------------------------------------------


class TestIssueTable:
    def test_the_index_builder_encodes_the_space(self) -> None:
        assert (
            build_index_markdown(
                "Index", [("Target", "/Project Notes/target.md", None)]
            )
            == "# Index\n\n- [Target](/Project%20Notes/target.md)\n"
        )

    def test_the_wikilink_converter_encodes_the_space(self) -> None:
        new, converted, _ = convert_wikilinks_to_markdown(
            "[[Target]]", [_wl("Target", "Project Notes/target.md")]
        )
        assert new == "[Target](/Project%20Notes/target.md)"
        assert converted == 1

    def test_the_rename_rewriter_encodes_the_space(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown",
                "/target.md",
                None,
                "Project Notes/target.md",
                source_path=SRC,
                old_path="target.md",
            )
            == "/Project%20Notes/target.md"
        )


# ---------------------------------------------------------------------------
# The property §6.3 states, over the producers
# ---------------------------------------------------------------------------


class TestNothingForbiddenSurvives:
    @pytest.mark.parametrize(
        "path",
        [
            "Project Notes/target.md",
            "a/b c/d  e.md",
            "tabbed\tname.md",
            "vertical\x0btab.md",
            "del\x7fete.md",
            " leading and trailing .md",
        ],
        ids=["space", "runs", "tab", "vtab", "del", "edges"],
    )
    def test_no_producer_emits_a_forbidden_character(self, path: str) -> None:
        index = build_index_markdown("I", [("T", f"/{path}", None)])
        converted, _, _ = convert_wikilinks_to_markdown("[[T]]", [_wl("T", path)])
        renamed = compute_new_raw_target(
            "markdown", "/old.md", None, path, source_path=SRC, old_path="old.md"
        )
        for destination in (
            _destination(index.splitlines()[-1]),
            _destination(converted),
            renamed,
        ):
            assert not set(destination) & set(_FORBIDDEN), destination

    def test_the_encoded_link_still_names_the_note(self) -> None:
        converted, _, _ = convert_wikilinks_to_markdown(
            "[[T]]", [_wl("T", "Project Notes/target.md")]
        )
        links = extract_links(converted, SRC)
        assert [link.target_path for link in links] == ["Project Notes/target.md"]

    def test_a_fragment_is_encoded_too(self) -> None:
        # A space in the fragment ends the destination just as one in the
        # path does, and what follows is read as a title.
        new, _, _ = convert_wikilinks_to_markdown(
            "[[Target#My Heading]]",
            [_wl("Target#My Heading", "Project Notes/t.md", fragment="My Heading")],
        )
        assert new == "[Target](/Project%20Notes/t.md#My%20Heading)"
        assert extract_links(new, SRC)[0].target_path == "Project Notes/t.md"


class TestWhatEncodePlainDestinationLeavesAlone:
    @pytest.mark.parametrize(
        "path",
        [
            "guides/note.md",
            "a(b).md",
            "b[1].md",
            "50%.md",
            "café/día.md",
            "emoji-🙂.md",
        ],
        ids=["plain", "parens", "brackets", "percent", "accents", "emoji"],
    )
    def test_a_legal_destination_is_returned_untouched(self, path: str) -> None:
        # Only what §6.3 forbids is touched, so the destination stays
        # readable — the same restraint Obsidian's writer shows (it encodes
        # spaces and leaves ``(``, ``)``, ``[``, ``]`` literal).
        assert encode_plain_destination(path) == path

    def test_a_percent_beside_a_space_still_reads_back(self) -> None:
        # ``50% done.md`` becomes ``50%%20done.md``: the author's ``%`` is
        # not followed by two hex digits, so it is not an escape and only
        # the ``%20`` decodes.
        encoded = encode_plain_destination("50% done.md")
        assert encoded == "50%%20done.md"
        assert extract_links(f"[t]({encoded})", SRC)[0].target_path == "50% done.md"


# ---------------------------------------------------------------------------
# The rewriter's other spellings are unaffected
# ---------------------------------------------------------------------------


class TestTheOtherSpellingsAreUnchanged:
    def test_the_pointy_form_keeps_its_literal_space(self) -> None:
        # ``<…>`` may hold a space, so there is nothing to repair and the
        # spelling the author chose survives (#1353).
        assert (
            compute_new_raw_target(
                "markdown", "<old.md>", None, "new name.md", SRC, "old.md"
            )
            == "<new name.md>"
        )

    def test_an_already_encoded_original_is_re_encoded_as_before(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown", "/old%20name.md", None, "new name.md", SRC, "old name.md"
            )
            == "/new%20name.md"
        )

    def test_a_hash_is_still_backslash_escaped_beside_an_encoded_space(self) -> None:
        # The two bounded repairs compose: ``#`` would re-point the link,
        # the space would end it.
        new_raw = compute_new_raw_target(
            "markdown", "old.md", None, "#y note.md", SRC, "old.md"
        )
        assert new_raw == "\\#y%20note.md"
        assert extract_links(f"[a]({new_raw})", SRC)[0].target_path == "#y note.md"

    def test_a_wikilink_target_is_never_encoded(self) -> None:
        # Obsidian writes wikilink targets literally, so ``%20`` in one is
        # part of the name; the one place the two families disagree.
        assert (
            compute_new_raw_target(
                "wikilink", "old", None, "new name.md", SRC, "old.md"
            )
            == "new name"
        )


# ---------------------------------------------------------------------------
# End to end, through the vault
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


class TestEndToEnd:
    def test_a_rename_into_a_folder_with_a_space_keeps_the_link(
        self, tmp_path: Path, vault: Vault
    ) -> None:
        (tmp_path / "Project Notes").mkdir()
        (tmp_path / "target.md").write_text("# Target\n", encoding="utf-8")
        (tmp_path / SRC).write_text("See [it](/target.md).\n", encoding="utf-8")
        vault.index.build_index()

        result = vault.writer.rename(
            "target.md", "Project Notes/target.md", update_links=True
        )

        assert result.updated_links == 1
        assert (tmp_path / SRC).read_text(encoding="utf-8") == (
            "See [it](/Project%20Notes/target.md).\n"
        )

    def test_a_generated_index_links_a_note_whose_folder_has_a_space(
        self, tmp_path: Path, vault: Vault
    ) -> None:
        (tmp_path / "Project Notes").mkdir()
        (tmp_path / "Project Notes" / "target.md").write_text(
            "# Target\n", encoding="utf-8"
        )
        vault.index.build_index()

        vault.writer.okf_generate_index(folder="Project Notes")

        body = (tmp_path / "Project Notes" / "index.md").read_text(encoding="utf-8")
        assert "(/Project%20Notes/target.md)" in body


# ---------------------------------------------------------------------------
# The re-pointing class: a name that stays a link and means something else
# ---------------------------------------------------------------------------


class TestIssue1513Table:
    """The three rows #1513 reported, through the index builder."""

    def test_a_hash_in_the_path_no_longer_becomes_a_fragment(self) -> None:
        # The quiet one: it stayed a link, to a note that does not exist.
        line = _index_line("Hash", "/notes/a#b.md")
        assert line == "- [Hash](/notes/a\\#b.md)"
        assert _one(line) == ("notes/a#b.md", None, "Hash")

    def test_an_unbalanced_open_paren_no_longer_kills_the_link(self) -> None:
        line = _index_line("P", "/notes/a(b.md")
        assert line == "- [P](/notes/a\\(b.md)"
        assert _one(line) == ("notes/a(b.md", None, "P")

    def test_an_unbalanced_close_paren_no_longer_truncates_the_target(self) -> None:
        # Reported as "no link"; it was worse — the link resolved to
        # ``notes/a``, a different note.
        line = _index_line("P", "/notes/a)b.md")
        assert line == "- [P](/notes/a\\)b.md)"
        assert _one(line) == ("notes/a)b.md", None, "P")


class TestOnlyWhatWouldNotParseIsEscaped:
    def test_balanced_parentheses_stay_literal(self) -> None:
        # Legal in the plain form and readable, so escaping them would be
        # noise — the same restraint the space encoder shows.
        line = _index_line("P", "/notes/a(b).md")
        assert line == "- [P](/notes/a(b).md)"
        assert _one(line) == ("notes/a(b).md", None, "P")

    @pytest.mark.parametrize("depth", range(1, _MAX_PLAIN_PAREN_DEPTH + 3))
    def test_the_write_side_depth_limit_matches_the_parser(self, depth: int) -> None:
        # ``_MAX_PLAIN_PAREN_DEPTH`` mirrors the parser's own limit. If the
        # parser's limit moves and this one does not, the generated link
        # stops resolving — so pin the agreement, not either number.
        name = "a" + "(" * depth + "x" + ")" * depth + ".md"
        assert _one(_index_line("P", f"/notes/{name}")) == (
            f"notes/{name}",
            None,
            "P",
        )

    def test_escaping_composes_with_the_space_encoder(self) -> None:
        line = _index_line("P", "/Project Notes/a#b.md")
        assert line == "- [P](/Project%20Notes/a\\#b.md)"
        assert _one(line) == ("Project Notes/a#b.md", None, "P")

    def test_a_fragment_marker_survives_a_hash_in_the_path(self) -> None:
        # ``build_plain_destination`` attaches the marker after escaping, so
        # the separator stays a separator and the name's ``#`` does not.
        written = build_plain_destination("notes/a#b.md", "My Heading")
        assert written == "notes/a\\#b.md#My%20Heading"
        target, fragment, _text = _one(f"[t]({written})")
        assert (target, fragment) == ("notes/a#b.md", "My%20Heading")


class TestTheConverterSharesTheRepairs:
    def test_a_resolved_path_with_a_hash_is_escaped(self) -> None:
        new, _, _ = convert_wikilinks_to_markdown("[[T]]", [_wl("T", "notes/a#b.md")])
        assert new == "[T](/notes/a\\#b.md)"
        assert extract_links(new, SRC)[0].target_path == "notes/a#b.md"

    def test_an_alias_bracket_is_escaped(self) -> None:
        # An unmatched ``[`` makes a spec reader show different text than
        # the alias intended; the wikilink grammar cannot deliver a ``]``.
        new, _, _ = convert_wikilinks_to_markdown(
            "[[T|Bra[cket]]", [_wl("T", "notes/x.md")]
        )
        assert new == "[Bra\\[cket](/notes/x.md)"
        assert extract_links(new, SRC)[0].target_path == "notes/x.md"


class TestGeneratedLinkText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Bra]cket", "Bra\\]cket"),
            ("Note [draft]", "Note \\[draft\\]"),
            ("back\\slash", "back\\\\slash"),
            ("plain", "plain"),
        ],
        ids=["close", "pair", "backslash", "untouched"],
    )
    def test_brackets_and_backslashes_are_escaped(
        self, text: str, expected: str
    ) -> None:
        assert escape_link_text(text) == expected

    def test_a_backslash_is_escaped_before_the_bracket_it_precedes(self) -> None:
        # Escaping ``]`` first would leave the author's own backslash
        # escaping the escape, and the ``]`` would close the text again.
        assert escape_link_text("a\\]b") == "a\\\\\\]b"

    def test_a_title_bracket_is_escaped_but_still_not_indexed_here(self) -> None:
        # Valid CommonMark, which is what Obsidian and the docs site read.
        # This project's own link-text pattern is not escape-aware, so the
        # entry contributes no edge until #1517 lands. Stated, not hidden:
        # if this starts passing, #1517 is fixed and this test should say so.
        line = _index_line("Bra]cket", "/notes/x.md")
        assert line == "- [Bra\\]cket](/notes/x.md)"
        assert extract_links(line, "index.md") == []


class TestIssue1516RenameRewriter:
    """A rename into a name whose parentheses would not parse (#1516)."""

    @pytest.mark.parametrize(
        ("new_path", "expected"),
        [
            ("notes/a(b.md", "/notes/a\\(b.md"),
            ("notes/a)b.md", "/notes/a\\)b.md"),
            ("notes/a(b).md", "/notes/a(b).md"),
        ],
        ids=["open", "close", "balanced"],
    )
    def test_the_rewrite_lands_on_the_renamed_file(
        self, new_path: str, expected: str
    ) -> None:
        raw = compute_new_raw_target(
            "markdown", "/old.md", None, new_path, source_path=SRC, old_path="old.md"
        )
        assert raw == expected
        assert extract_links(f"[t]({raw})", SRC)[0].target_path == new_path

    def test_the_pointy_form_needs_no_paren_escape(self) -> None:
        # ``<…>`` admits a parenthesis, so the author's spelling survives.
        assert (
            compute_new_raw_target(
                "markdown", "<old.md>", None, "a(b.md", SRC, "old.md"
            )
            == "<a(b.md>"
        )

    def test_an_encoded_original_is_still_re_encoded_not_escaped(self) -> None:
        assert (
            compute_new_raw_target(
                "markdown", "/old%20name.md", None, "a(b.md", SRC, "old name.md"
            )
            == "/a%28b.md"
        )

    def test_end_to_end_a_rename_into_an_unbalanced_name(
        self, tmp_path: Path, vault: Vault
    ) -> None:
        (tmp_path / "target.md").write_text("# Target\n", encoding="utf-8")
        (tmp_path / SRC).write_text("See [it](/target.md).\n", encoding="utf-8")
        vault.index.build_index()

        result = vault.writer.rename("target.md", "a)b.md", update_links=True)

        assert result.updated_links == 1
        assert (tmp_path / SRC).read_text(encoding="utf-8") == (
            "See [it](/a\\)b.md).\n"
        )


class TestParenthesesBalanceAcrossTheWholeDestination:
    """§6.3 reads a destination's parentheses as one run (#1516).

    Checking the path and the fragment separately is wrong in both
    directions: it misses an unbalanced parenthesis that lives only in the
    fragment, and it *breaks* a destination whose two parts balance each
    other.
    """

    @pytest.mark.parametrize(
        ("fragment", "expected"),
        [
            ("Heading (draft", "notes/x.md#Heading%20\\(draft"),
            ("Heading) draft", "notes/x.md#Heading\\)%20draft"),
            ("Heading (draft)", "notes/x.md#Heading%20(draft)"),
        ],
        ids=["open", "close", "balanced"],
    )
    def test_a_fragment_parenthesis_counts(self, fragment: str, expected: str) -> None:
        written = build_plain_destination("notes/x.md", fragment)
        assert written == expected
        assert _one(f"[t]({written})")[0] == "notes/x.md"

    def test_a_fragment_may_close_what_the_path_opened(self) -> None:
        # ``a(b.md#c)d`` balances as a whole, so it parses and nothing is
        # escaped. Escaping the path's ``(`` on its own would unbalance the
        # run and break a destination that worked.
        written = build_plain_destination("notes/a(b.md", "c)d")
        assert written == "notes/a(b.md#c)d"
        assert _one(f"[t]({written})")[:2] == ("notes/a(b.md", "c)d")

    def test_the_rewriter_balances_the_assembled_destination_too(self) -> None:
        raw = compute_new_raw_target(
            "markdown", "/old.md", "c)d", "notes/a(b.md", SRC, "old.md"
        )
        assert raw == "/notes/a(b.md#c)d"
        assert _one(f"[t]({raw})")[:2] == ("notes/a(b.md", "c)d")

    def test_the_rewriter_still_encodes_a_space_beside_a_fragment(self) -> None:
        raw = compute_new_raw_target(
            "markdown", "/old.md", "My Heading", "Project Notes/a.md", SRC, "old.md"
        )
        assert raw == "/Project%20Notes/a.md#My%20Heading"
        assert _one(f"[t]({raw})")[0] == "Project Notes/a.md"


class TestTheRepairsReachTheAuthorsFragment:
    def test_a_fragment_the_author_left_unparsable_is_repaired_on_rename(
        self,
    ) -> None:
        # ``[x](old.md#My Heading)`` is not a link to begin with — the
        # tolerant scanner reads it, CommonMark does not. The rename is
        # already rewriting that destination, so it emits one that parses
        # rather than re-emitting the broken spelling verbatim. The fragment
        # is still never *reformatted*: both repairs are properties of the
        # destination as a whole, not edits to the part that changed.
        raw = compute_new_raw_target(
            "markdown", "old.md#My Heading", "My Heading", "new.md", SRC, "old.md"
        )
        assert raw == "new.md#My%20Heading"
        assert _one(f"[t]({raw})")[:2] == ("new.md", "My%20Heading")

    def test_a_fragment_that_already_parses_is_left_exactly_as_found(self) -> None:
        raw = compute_new_raw_target(
            "markdown", "old.md#Top", "Top", "new.md", SRC, "old.md"
        )
        assert raw == "new.md#Top"
