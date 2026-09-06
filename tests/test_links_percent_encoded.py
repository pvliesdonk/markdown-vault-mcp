"""Percent-encoded markdown destinations, from extraction to rename (#1332).

The decoding rules are enumerated on the issue and in
``docs/design/design.md`` (Link Extraction); the primary sources are in
``docs/design/reference/obsidian-markdown.md`` ("Markdown links Obsidian
writes") and ``docs/design/reference/commonmark-gfm.md`` ("Inline links").
One test per row of that table, plus the end-to-end half through a built
vault: both spellings resolve and backlink, and a link-updating rename
rewrites each in the spelling its author used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# extract_links: percent-encoded destinations (#1332)
# ---------------------------------------------------------------------------


class TestPercentEncodedTargets:
    """A markdown destination is a URL, so its escapes name the same file.

    Percent-encoding is the canonical way to write a destination containing
    ``[``, ``]``, spaces or parentheses, which is exactly the population
    #1303 / #1305 taught the git layer to handle.
    """

    def test_encoded_brackets_resolve_to_the_named_note(self) -> None:
        """The #1332 reproduction."""
        links = extract_links("[x](/probe/b%5B1%5D.md)", "hub.md")
        assert links[0].target_path == "probe/b[1].md"

    def test_raw_target_keeps_the_spelling_as_written(self) -> None:
        """raw_target is what a rewrite must find in the file."""
        links = extract_links("[x](/probe/b%5B1%5D.md)", "hub.md")
        assert links[0].raw_target == "/probe/b%5B1%5D.md"

    def test_encoded_space_resolves(self) -> None:
        links = extract_links("[x](notes/my%20note.md)", "hub.md")
        assert links[0].target_path == "notes/my note.md"

    def test_encoded_and_literal_spellings_agree(self) -> None:
        """Both spellings of one name resolve to the same target."""
        content = "[a](/p/b%5B1%5D.md)\n\n[b](/p/b[1].md)\n"
        targets = {lnk.target_path for lnk in extract_links(content, "hub.md")}
        assert targets == {"p/b[1].md"}

    def test_encoded_fragment_separator_is_not_a_fragment(self) -> None:
        """``%23`` is a literal ``#`` in the name, not the fragment marker."""
        links = extract_links("[x](notes/C%23-guide.md)", "hub.md")
        assert links[0].target_path == "notes/C#-guide.md"
        assert links[0].fragment is None

    def test_a_real_fragment_still_splits(self) -> None:
        links = extract_links("[x](notes/my%20note.md#sec)", "hub.md")
        assert links[0].target_path == "notes/my note.md"
        assert links[0].fragment == "sec"

    def test_reference_definitions_decode_too(self) -> None:
        content = "[x][ref]\n\n[ref]: /p/b%5B1%5D.md"
        assert extract_links(content, "hub.md")[0].target_path == "p/b[1].md"

    def test_a_lone_percent_is_left_alone(self) -> None:
        """``%`` that begins no valid escape is a literal character."""
        links = extract_links("[x](notes/50%25%20off.md)", "hub.md")
        assert links[0].target_path == "notes/50% off.md"

    def test_an_encoded_separator_is_data_not_structure(self) -> None:
        """``%2F`` names one impossible file, not a path with a folder in it.

        Decoding it would resolve the link to the unrelated note actually at
        ``dir/note.md``, giving it a false backlink and letting a rename
        rewrite it (review round 1).
        """
        links = extract_links("[x](dir%2Fnote.md)", "hub.md")
        assert links[0].target_path == "dir%2Fnote.md"

    def test_an_encoded_separator_does_not_collide_with_a_real_note(self) -> None:
        content = "[a](dir%2Fnote.md)\n\n[b](dir/note.md)\n"
        targets = [lnk.target_path for lnk in extract_links(content, "hub.md")]
        assert targets == ["dir%2Fnote.md", "dir/note.md"]

    def test_an_invalid_utf8_escape_leaves_the_target_undecoded(self) -> None:
        """``unquote`` would substitute U+FFFD, inventing a name.

        Distinct malformed sequences would also collapse onto that one
        target, so several broken links could share a false backlink.
        """
        links = extract_links("[x](bad%FF.md)", "hub.md")
        assert links[0].target_path == "bad%FF.md"

    def test_distinct_malformed_escapes_stay_distinct(self) -> None:
        content = "[a](bad%FF.md)\n\n[b](bad%FE.md)\n"
        targets = {lnk.target_path for lnk in extract_links(content, "hub.md")}
        assert targets == {"bad%FF.md", "bad%FE.md"}

    def test_valid_multibyte_escapes_still_decode(self) -> None:
        """Strict decoding must not reject legitimate non-ASCII names."""
        links = extract_links("[x](notes/caf%C3%A9.md)", "hub.md")
        assert links[0].target_path == "notes/café.md"

    def test_an_encoded_nul_is_refused(self) -> None:
        """No file system allows NUL in a name."""
        links = extract_links("[x](nul%00.md)", "hub.md")
        assert links[0].target_path == "nul%00.md"

    def test_a_refusal_is_whole_not_partial(self) -> None:
        """A refused destination keeps every escape, not only the refused one."""
        links = extract_links("[x](dir%2Fno%20te.md)", "hub.md")
        assert links[0].target_path == "dir%2Fno%20te.md"

    def test_a_decoded_traversal_is_still_clamped(self) -> None:
        """``%2E%2E`` decodes to ``..`` and meets the same root clamp."""
        links = extract_links("[x](%2E%2E/%2E%2E/up.md)", "sub/hub.md")
        assert links[0].target_path == "up.md"

    def test_wikilinks_are_not_decoded(self) -> None:
        """Obsidian writes wikilink targets literally; it never encodes them.

        Decoding them would break a note genuinely named with a ``%``.
        """
        links = extract_links("[[notes/100%20plan]]", "hub.md")
        assert links[0].target_path == "notes/100%20plan.md"


# ---------------------------------------------------------------------------
# A link-updating rename reaches encoded destinations too (#1332)
# ---------------------------------------------------------------------------


class TestRenameRewritesEncodedLinks:
    """The half of #1332 that silently left a link dangling.

    Before the fix ``rename(update_links=True)`` reported ``updated_links: 1``
    on a note carrying both spellings: it rewrote the literal one and skipped
    the encoded one, which then pointed at a path that no longer existed.
    """

    @staticmethod
    def _vault(tmp_path: Path) -> Vault:
        src = tmp_path / "vault"
        (src / "probe").mkdir(parents=True)
        (src / "probe" / "b[1].md").write_text("# B\n", encoding="utf-8")
        (src / "hub.md").write_text(
            "[encoded](/probe/b%5B1%5D.md)\n\n[literal](/probe/b[1].md)\n",
            encoding="utf-8",
        )
        col = Vault(source_dir=src, read_only=False)
        col.index.build_index()
        return col

    def test_both_spellings_resolve_and_backlink(self, tmp_path: Path) -> None:
        col = self._vault(tmp_path)
        outlinks = col.graph.get_outlinks("hub.md")
        assert {o.target_path for o in outlinks} == {"probe/b[1].md"}
        assert all(o.exists for o in outlinks)
        assert len(col.graph.get_backlinks("probe/b[1].md")) == 2

    def test_rename_rewrites_both_spellings(self, tmp_path: Path) -> None:
        col = self._vault(tmp_path)
        result = col.writer.rename("probe/b[1].md", "probe/b2.md", update_links=True)
        assert result.updated_links == 1  # one source note
        body = (tmp_path / "vault" / "hub.md").read_text(encoding="utf-8")
        assert "b%5B1%5D" not in body
        assert "b[1]" not in body
        assert body.count("/probe/b2.md") == 2

    def test_rename_into_a_name_needing_escapes_keeps_the_spelling(
        self, tmp_path: Path
    ) -> None:
        """The encoded link stays encoded; the literal one stays literal.

        No encoding is introduced where the author used none — that is the
        #1105 rule, and it is why a rename cannot repair a destination whose
        new name would need escaping to parse. Out of scope here.
        """
        col = self._vault(tmp_path)
        col.writer.rename("probe/b[1].md", "probe/c[2].md", update_links=True)
        body = (tmp_path / "vault" / "hub.md").read_text(encoding="utf-8")
        assert "[encoded](/probe/c%5B2%5D.md)" in body
        assert "[literal](/probe/c[2].md)" in body

    def test_a_refused_destination_stays_visible_as_broken(
        self, tmp_path: Path
    ) -> None:
        """Refusing to decode must not hide the link from the broken list."""
        src = tmp_path / "vault"
        (src / "dir").mkdir(parents=True)
        (src / "dir" / "note.md").write_text("# real\n", encoding="utf-8")
        (src / "hub.md").write_text("[x](dir%2Fnote.md)\n", encoding="utf-8")
        col = Vault(source_dir=src, read_only=False)
        col.index.build_index()
        broken = col.graph.get_broken_links()
        assert [b.target_path for b in broken] == ["dir%2Fnote.md"]
        assert col.graph.get_backlinks("dir/note.md") == []

    def test_move_folder_rewrites_encoded_links_too(self, tmp_path: Path) -> None:
        """rename and move_folder share one rewrite path, so both are fixed."""
        col = self._vault(tmp_path)
        col.writer.move_folder("probe", "moved")
        body = (tmp_path / "vault" / "hub.md").read_text(encoding="utf-8")
        assert "[encoded](/moved/b%5B1%5D.md)" in body
        assert "[literal](/moved/b[1].md)" in body
