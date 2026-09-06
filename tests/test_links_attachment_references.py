"""Attachment references are not note links (#1333).

The link graph is notes-only: a target whose extension is on the attachment
allowlist names a file the index never holds, so recording it as a link
made it broken by construction — ``![[pic.png]]`` stored ``pic.png.md``,
and ``[img](pic.png)`` stored a path no ``documents`` row could match. Such
references are skipped at all three extraction sites, the way ``![alt](src)``
already was. The Obsidian side is in
``docs/design/reference/obsidian-markdown.md`` ("Extension, non-markdown
targets, embeds"); the decision and its consequences are on the issue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import DEFAULT_ATTACHMENT_EXTENSIONS
from markdown_vault_mcp.utils import (
    canonical_attachment_extensions,
    names_attachment,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path

PNG_PDF = frozenset({"png", "pdf"})


# ---------------------------------------------------------------------------
# names_attachment: what kind of name is this
# ---------------------------------------------------------------------------


class TestNamesAttachment:
    """The name's kind, decided from its suffix and the allowlist alone."""

    def test_an_allowlisted_suffix_names_an_attachment(self) -> None:
        assert names_attachment("diagram.png", PNG_PDF) is True

    def test_a_folder_qualified_target_is_judged_by_its_suffix(self) -> None:
        assert names_attachment("Images/diagram.png", PNG_PDF) is True

    def test_the_suffix_is_matched_case_insensitively(self) -> None:
        assert names_attachment("DIAGRAM.PNG", PNG_PDF) is True

    def test_a_suffix_outside_the_allowlist_is_a_note(self) -> None:
        assert names_attachment("data.csv", PNG_PDF) is False

    def test_a_target_without_a_suffix_is_a_note(self) -> None:
        assert names_attachment("Three laws of motion", PNG_PDF) is False

    def test_an_md_target_is_a_note_in_every_configuration(self) -> None:
        assert names_attachment("note.md", frozenset({"*"})) is False
        assert names_attachment("note.MD", frozenset({"*"})) is False
        assert names_attachment("note.md", frozenset({"md"})) is False

    def test_the_wildcard_accepts_an_extension_shaped_suffix(self) -> None:
        assert names_attachment("diagram.png", frozenset({"*"})) is True
        assert names_attachment("archive.tar", frozenset({"*"})) is True

    def test_the_wildcard_rejects_a_suffix_that_is_not_extension_shaped(
        self,
    ) -> None:
        """``Version 2.0 plan`` is a note title, not a ``0 plan`` file type."""
        assert names_attachment("Version 2.0 plan", frozenset({"*"})) is False

    def test_the_wildcard_rejects_a_purely_numeric_suffix(self) -> None:
        """``Python 3.12`` and ``v1.2`` are note titles; no file type is digits only."""
        assert names_attachment("Python 3.12", frozenset({"*"})) is False
        assert names_attachment("v1.2", frozenset({"*"})) is False
        assert names_attachment("clip.3gp", frozenset({"*"})) is True
        assert names_attachment("archive.7z", frozenset({"*"})) is True

    def test_an_empty_allowlist_names_nothing(self) -> None:
        assert names_attachment("diagram.png", frozenset()) is False


# ---------------------------------------------------------------------------
# canonical_attachment_extensions: the allowlist as build provenance
# ---------------------------------------------------------------------------


class TestCanonicalAttachmentExtensions:
    """The rendering the index records, so a change rejects a warm restart."""

    def test_the_default_set_renders_empty(self) -> None:
        """An index predating the key reads back ``""`` and must still match."""
        assert canonical_attachment_extensions(DEFAULT_ATTACHMENT_EXTENSIONS) == ""

    def test_an_explicitly_empty_allowlist_is_not_the_default(self) -> None:
        rendered = canonical_attachment_extensions(frozenset())
        assert rendered != ""
        assert rendered != canonical_attachment_extensions(
            DEFAULT_ATTACHMENT_EXTENSIONS
        )

    def test_an_explicit_allowlist_renders_as_a_sorted_json_list(self) -> None:
        assert canonical_attachment_extensions(frozenset({"png", "csv"})) == (
            '["csv", "png"]'
        )

    def test_the_wildcard_renders_as_a_list_too(self) -> None:
        assert canonical_attachment_extensions(frozenset({"*"})) == '["*"]'

    def test_a_member_containing_the_delimiter_cannot_collide(self) -> None:
        """``["a,b"]`` and ``["a", "b"]`` derive different rows; they must differ."""
        assert canonical_attachment_extensions(
            frozenset({"a,b"})
        ) != canonical_attachment_extensions(frozenset({"a", "b"}))

    def test_a_member_spelled_like_the_empty_rendering_cannot_collide(self) -> None:
        empty = canonical_attachment_extensions(frozenset())
        assert canonical_attachment_extensions(frozenset({empty})) != empty


# ---------------------------------------------------------------------------
# extract_links: the three sites
# ---------------------------------------------------------------------------


class TestWikilinkSite:
    """``[[target]]`` and ``![[target]]`` share one grammar and one rule."""

    def test_an_embed_of_an_attachment_is_not_a_link(self) -> None:
        """The #1333 reproduction: ``![[Images/pic.png]]`` stored ``Images/pic.png.md``."""
        assert (
            extract_links(
                "![[Images/pic.png]]", "note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_a_plain_wikilink_to_an_attachment_is_not_a_link(self) -> None:
        assert (
            extract_links("[[pic.png]]", "note.md", attachment_extensions=PNG_PDF) == []
        )

    def test_the_fragment_is_split_before_the_kind_is_decided(self) -> None:
        """``![[Document.pdf#page=3]]`` is how Obsidian embeds a PDF page."""
        assert (
            extract_links(
                "![[Document.pdf#page=3]]", "note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_the_alias_slot_does_not_change_the_kind(self) -> None:
        """``![[Engelbart.jpg|100x145]]`` carries a size in the alias slot."""
        assert (
            extract_links(
                "![[pic.png|100x145]]", "note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_a_relative_prefixed_attachment_is_not_a_link(self) -> None:
        assert (
            extract_links(
                "[[../assets/pic.png]]", "a/note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_an_explicit_md_target_stays_a_note_under_the_wildcard(self) -> None:
        (link,) = extract_links(
            "[[note.md]]", "hub.md", attachment_extensions=frozenset({"*"})
        )
        assert link.target_path == "note.md"

    def test_a_note_title_with_a_dot_stays_a_note_under_the_wildcard(self) -> None:
        (link,) = extract_links(
            "[[Version 2.0 plan]]", "hub.md", attachment_extensions=frozenset({"*"})
        )
        assert link.target_path == "Version 2.0 plan.md"

    def test_a_suffix_outside_the_allowlist_still_gains_md(self) -> None:
        """With nothing allowlisted, ``[[pic.png]]`` is a note reference."""
        (link,) = extract_links(
            "[[pic.png]]", "hub.md", attachment_extensions=frozenset()
        )
        assert link.target_path == "pic.png.md"
        assert link.raw_target == "pic.png"

    def test_the_default_allowlist_applies_when_none_is_given(self) -> None:
        assert "png" in DEFAULT_ATTACHMENT_EXTENSIONS
        assert extract_links("[[pic.png]]", "hub.md") == []

    def test_a_note_embed_is_still_a_link(self) -> None:
        """``![[Other]]`` transcludes a note; the note is a graph node."""
        (link,) = extract_links("![[Other]]", "hub.md", attachment_extensions=PNG_PDF)
        assert link.target_path == "Other.md"


class TestInlineSite:
    """``[text](target)`` — the non-image spelling of an attachment reference."""

    def test_a_link_to_an_attachment_is_not_a_link(self) -> None:
        assert (
            extract_links(
                "[img](Images/pic.png)", "note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_the_kind_is_decided_on_the_decoded_destination(self) -> None:
        """``my%20pic.png`` names ``my pic.png``; the suffix is the same either way."""
        assert (
            extract_links(
                "[img](my%20pic.png)", "note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_a_root_relative_attachment_is_not_a_link(self) -> None:
        assert (
            extract_links(
                "[paper](/papers/x.pdf)", "a/note.md", attachment_extensions=PNG_PDF
            )
            == []
        )

    def test_a_note_destination_is_untouched(self) -> None:
        (link,) = extract_links(
            "[n](other.md)", "note.md", attachment_extensions=PNG_PDF
        )
        assert link.target_path == "other.md"

    def test_a_suffix_outside_the_allowlist_is_still_a_link(self) -> None:
        (link,) = extract_links(
            "[d](data.csv)", "note.md", attachment_extensions=PNG_PDF
        )
        assert link.target_path == "data.csv"


class TestReferenceSite:
    """``[text][ref]`` with ``[ref]: target``."""

    def test_a_definition_naming_an_attachment_yields_no_link(self) -> None:
        assert (
            extract_links(
                "[img][r]\n\n[r]: Images/pic.png",
                "note.md",
                attachment_extensions=PNG_PDF,
            )
            == []
        )

    def test_a_definition_naming_a_note_is_untouched(self) -> None:
        (link,) = extract_links(
            "[n][r]\n\n[r]: other.md", "note.md", attachment_extensions=PNG_PDF
        )
        assert link.target_path == "other.md"


# ---------------------------------------------------------------------------
# End to end: the vault the issue describes
# ---------------------------------------------------------------------------


def _build(tmp_path: Path, attachment_extensions: list[str] | None) -> Vault:
    src = tmp_path / "vault"
    (src / "Images").mkdir(parents=True)
    (src / "Images" / "pic.png").write_bytes(b"\x89PNG")
    (src / "other.md").write_text("# O\n", encoding="utf-8")
    (src / "note.md").write_text(
        "# N\n\n"
        "![[Images/pic.png]]\n"
        "[[pic.png]]\n"
        "[[Doc.pdf#page=3]]\n"
        "[img](Images/pic.png)\n"
        "![img](Images/pic.png)\n"
        "[ref][r]\n\n[r]: Images/pic.png\n\n"
        "[[other.md]]\n",
        encoding="utf-8",
    )
    col = Vault(source_dir=src, attachment_extensions=attachment_extensions)
    col.index.build_index()
    return col


class TestBuiltVault:
    def test_attachment_references_are_not_outlinks(self, tmp_path: Path) -> None:
        col = _build(tmp_path, ["png", "pdf"])
        (outlink,) = col.graph.get_outlinks("note.md")
        assert outlink.target_path == "other.md"
        assert outlink.exists is True

    def test_attachment_references_are_not_broken_links(self, tmp_path: Path) -> None:
        col = _build(tmp_path, ["png", "pdf"])
        assert col.graph.get_broken_links() == []

    def test_the_stats_count_agrees_with_the_list(self, tmp_path: Path) -> None:
        col = _build(tmp_path, ["png", "pdf"])
        assert col.reader.stats().broken_link_count == 0

    def test_a_note_linking_only_attachments_is_an_orphan(self, tmp_path: Path) -> None:
        """No link rows in either direction is what orphanhood means."""
        src = tmp_path / "vault"
        (src / "Images").mkdir(parents=True)
        (src / "Images" / "pic.png").write_bytes(b"\x89PNG")
        (src / "gallery.md").write_text(
            "# G\n\n![[Images/pic.png]]\n", encoding="utf-8"
        )
        col = Vault(source_dir=src, attachment_extensions=["png"])
        col.index.build_index()
        assert [n.path for n in col.graph.get_orphan_notes()] == ["gallery.md"]
        assert col.reader.stats().orphan_count == 1

    def test_with_nothing_allowlisted_the_references_are_note_links(
        self, tmp_path: Path
    ) -> None:
        """The allowlist decides; an explicitly empty one keeps today's rows."""
        col = _build(tmp_path, [])
        targets = {o.target_path for o in col.graph.get_outlinks("note.md")}
        assert "Images/pic.png.md" in targets
        assert "pic.png.md" in targets
