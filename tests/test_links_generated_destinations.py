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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.okf import build_index_markdown, convert_wikilinks_to_markdown
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import OutlinkInfo
from markdown_vault_mcp.utils.links import (
    compute_new_raw_target,
    encode_plain_destination,
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
