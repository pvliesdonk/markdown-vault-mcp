"""Markdown destinations fall back to vault paths when local paths are missing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.fts_index import INDEX_SEMANTICS_VERSION, FTSIndex
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import LinkInfo
from tests.conftest import wait_for_writer_drain
from tests.test_links import make_note

if TYPE_CHECKING:
    from pathlib import Path


def _index(source: str, body: str, paths: list[str]) -> FTSIndex:
    idx = FTSIndex(":memory:")
    idx.build_from_notes(
        [make_note(path=source, links=extract_links(body, source))]
        + [make_note(path=path) for path in paths]
    )
    idx.resolve_vault_wikilinks()
    return idx


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("root.md", "My%20Note.md"),
        ("root.md", "Sub/My%20Note.md"),
        ("Sub/source.md", "My%20Note.md"),
        ("Sub/source.md", "Sub/My%20Note.md"),
    ],
)
def test_reported_destinations(source: str, destination: str, reference: bool) -> None:
    body = f"[x][r]\n\n[r]: {destination}\n" if reference else f"[x]({destination})"
    idx = _index(source, body, ["Sub/My Note.md"])
    try:
        assert idx.get_outlinks(source)[0]["target_path"] == "Sub/My Note.md"
        assert len(idx.get_backlinks("Sub/My Note.md")) == 1
        assert idx.get_broken_links() == []
    finally:
        idx.close()


def test_local_path_wins_and_refresh_converges() -> None:
    idx = _index("a/source.md", "[x](Note.md)", ["b/Note.md", "Note.md"])
    try:
        assert idx.get_outlinks("a/source.md")[0]["target_path"] == "Note.md"
        idx.upsert_note(make_note(path="a/Note.md"))
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("a/source.md")[0]["target_path"] == "a/Note.md"
        idx.delete_by_path("a/Note.md")
        idx.delete_by_path("Note.md")
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("a/source.md")[0]["target_path"] == "b/Note.md"
        idx.delete_by_path("b/Note.md")
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("a/source.md")[0]["target_path"] == "a/Note.md"
        assert len(idx.get_broken_links()) == 1
    finally:
        idx.close()


@pytest.mark.parametrize(
    "destination",
    ["./Note.md", "%2E/Note.md", "<./Note.md>", "../Note.md", "/Note.md"],
)
def test_explicit_prefix_does_not_fallback(destination: str) -> None:
    idx = _index("a/source.md", f"[x]({destination})", ["b/Note.md"])
    try:
        assert len(idx.get_broken_links()) == 1
    finally:
        idx.close()


@pytest.mark.parametrize(
    "destination",
    ["My%23Note.md#heading", r"My\#Note.md#heading", "<My%23Note.md#heading>"],
)
def test_literal_hash_and_fragment(destination: str) -> None:
    idx = _index("source.md", f"[x]({destination})", ["sub/My#Note.md"])
    try:
        link = idx.get_outlinks("source.md")[0]
        assert (link["target_path"], link["fragment"]) == ("sub/My#Note.md", "heading")
    finally:
        idx.close()


def test_suffix_tie_break_and_no_extension_or_alias_inference() -> None:
    idx = _index(
        "src/source.md",
        "[x](Note.md) [y](Missing) [z](Alias.md)",
        ["bb/Note.md", "aa/Note.md", "Missing.md"],
    )
    try:
        idx.upsert_note(
            make_note(path="sub/other.md", frontmatter={"aliases": ["Alias.md"]})
        )
        idx.resolve_vault_wikilinks()
        links = idx.get_outlinks("src/source.md")
        assert [link["target_path"] for link in links] == [
            "aa/Note.md",
            "src/Missing",
            "src/Alias.md",
        ]
        assert len(idx.get_broken_links()) == 2
    finally:
        idx.close()


@pytest.mark.parametrize("link_type", ["markdown", "reference"])
def test_default_empty_raw_target_preserves_public_link(link_type: str) -> None:
    idx = FTSIndex(":memory:")
    try:
        idx.build_from_notes(
            [
                make_note(
                    path="source.md",
                    links=[
                        LinkInfo(
                            target_path="target.md", link_text="x", link_type=link_type
                        )
                    ],
                ),
                make_note(path="target.md"),
            ]
        )
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("source.md")[0]["target_path"] == "target.md"
        assert len(idx.get_backlinks("target.md")) == 1
    finally:
        idx.close()


def test_rename_and_folder_move_keep_fallback_links(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "Sub").mkdir()
    (tmp_path / "Sub" / "My Note.md").write_text("# Target\n", encoding="utf-8")
    (tmp_path / "source.md").write_text("[x](My%20Note.md)\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        assert vault.graph.get_outlinks("source.md")[0].target_path == "Sub/My Note.md"
        vault.writer.rename("Sub/My Note.md", "Sub/Renamed.md", update_links=True)
        wait_for_writer_drain(vault)
        assert vault.graph.get_outlinks("source.md")[0].target_path == "Sub/Renamed.md"
        vault.writer.move_folder("Sub", "Moved")
        wait_for_writer_drain(vault)
        assert (
            vault.graph.get_outlinks("source.md")[0].target_path == "Moved/Renamed.md"
        )
        assert vault.graph.get_broken_links() == []
    finally:
        vault.close()


def test_version_12_index_rebuilds_markdown_target(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from tests.test_index_coordinator import make_coordinator

    (tmp_path / "Sub").mkdir()
    (tmp_path / "Sub" / "Note.md").write_text("# Note\n", encoding="utf-8")
    (tmp_path / "source.md").write_text("[x](Note.md)\n", encoding="utf-8")
    db = tmp_path / "index.db"
    coord = make_coordinator(tmp_path, db=db)
    try:
        coord.build_index()
    finally:
        coord.close(timeout=5)
        coord._fts.close()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE links SET target_path = 'Note.md'")
        conn.execute(
            "UPDATE meta SET value = '12' WHERE key = 'index_semantics_version'"
        )
    coord = make_coordinator(tmp_path, db=db)
    try:
        assert not coord._chunking_meta_matches()
        coord.build_index()
        assert coord._fts.get_outlinks("source.md")[0]["target_path"] == "Sub/Note.md"
        assert (
            coord._fts.get_chunking_meta().semantics_version == INDEX_SEMANTICS_VERSION
        )
    finally:
        coord.close(timeout=5)
        coord._fts.close()


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("move_folder", [False, True])
def test_rewrite_vault_path_avoids_local_shadow(
    tmp_path: Path, reference: bool, move_folder: bool
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    for name in ("A", "Sub/B"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "A/Note.md").write_text("# Original\n", encoding="utf-8")
    (tmp_path / "Sub/B/Note.md").write_text("# Shadow\n", encoding="utf-8")
    content = (
        "[x][r]\n\n[r]: A/Note.md#heading\n"
        if reference
        else "[x](A/Note.md#heading)\n"
    )
    (tmp_path / "Sub/source.md").write_text(content, encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        assert vault.graph.get_outlinks("Sub/source.md")[0].target_path == "A/Note.md"
        if move_folder:
            vault.writer.move_folder("A", "B")
        else:
            (tmp_path / "B").mkdir()
            vault.writer.rename("A/Note.md", "B/Note.md", update_links=True)
        wait_for_writer_drain(vault)
        assert "/B/Note.md#heading" in (tmp_path / "Sub/source.md").read_text(
            encoding="utf-8"
        )
        for _ in range(2):
            assert (
                vault.graph.get_outlinks("Sub/source.md")[0].target_path == "B/Note.md"
            )
            assert len(vault.graph.get_backlinks("B/Note.md")) == 1
            assert vault.graph.get_backlinks("Sub/B/Note.md") == []
            vault.index.reindex()
    finally:
        vault.close()


def test_suffix_matching_ignores_other_basenames_and_wrong_suffixes() -> None:
    idx = _index(
        "src/source.md",
        "[x](b/Note.md) [[b/Note]]",
        ["z/b/Note.md", "a/c/Note.md", "b/Other.md", "long/b/Note.md"],
    )
    try:
        assert [row["target_path"] for row in idx.get_outlinks("src/source.md")] == [
            "z/b/Note.md",
            "z/b/Note.md",
        ]
    finally:
        idx.close()
