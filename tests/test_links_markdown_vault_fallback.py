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


@pytest.mark.parametrize(
    ("source", "destination", "reference"),
    [
        ("root.md", "My%20Note.md", False),
        ("Sub/source.md", "Sub/My%20Note.md", False),
        ("root.md", "My%20Note.md", True),
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
        "[x](Note.md) [y](Missing) [z](Alias.md) [p](b/Note.md)",
        [
            "bb/Note.md",
            "aa/Note.md",
            "Missing.md",
            "z/b/Note.md",
            "a/c/Note.md",
            "b/Other.md",
            "long/b/Note.md",
        ],
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
            "z/b/Note.md",
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
        coord.build_index()
        assert coord._fts.get_outlinks("source.md")[0]["target_path"] == "Sub/Note.md"
        assert (
            coord._fts.get_chunking_meta().semantics_version == INDEX_SEMANTICS_VERSION
        )
    finally:
        coord.close(timeout=5)
        coord._fts.close()


@pytest.mark.parametrize(
    ("reference", "move_folder", "root_case"),
    [(False, False, False), (True, True, False), (False, False, True)],
)
def test_rewrite_vault_path_avoids_local_shadow(
    tmp_path: Path, reference: bool, move_folder: bool, root_case: bool
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    if root_case:
        (tmp_path / "Sub").mkdir()
        (tmp_path / "Sub/My Note.md").write_text("# Target\n", encoding="utf-8")
        (tmp_path / "source.md").write_text("[x](My%20Note.md)\n", encoding="utf-8")
        vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
        try:
            vault.index.build_index()
            vault.writer.rename("Sub/My Note.md", "Sub/Renamed.md", update_links=True)
            wait_for_writer_drain(vault)
            assert (
                vault.graph.get_outlinks("source.md")[0].target_path == "Sub/Renamed.md"
            )
            vault.writer.move_folder("Sub", "Moved")
            wait_for_writer_drain(vault)
            assert (
                vault.graph.get_outlinks("source.md")[0].target_path
                == "Moved/Renamed.md"
            )
        finally:
            vault.close()
        return

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
        assert "../B/Note.md#heading" in (tmp_path / "Sub/source.md").read_text(
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


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("folder_move", [False, True])
def test_moving_source_preserves_fallback_targets(
    tmp_path: Path, reference: bool, folder_move: bool
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    for folder in ("A", "B", "C/B"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "B/target.md").write_text("# target\n", encoding="utf-8")
    (tmp_path / "C/B/target.md").write_text("# shadow\n", encoding="utf-8")
    (tmp_path / "A/together.md").write_text("# together\n", encoding="utf-8")
    (tmp_path / "A/local.md").write_text("# local\n", encoding="utf-8")
    links = (
        "[x][r] [y][s]\n\n[r]: B/target.md#h\n[s]: A/together.md\n"
        if reference
        else "[x](B/target.md#h) [y](A/together.md)\n"
    )
    (tmp_path / "A/source.md").write_text(
        links + "[control](../B/target.md) [broken](Missing.md)\n", encoding="utf-8"
    )
    callbacks: list[tuple[Path, str, str]] = []
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False),
        on_write=lambda path, content, operation: callbacks.append(
            (path, content, operation)
        ),
    )
    try:
        vault.index.build_index()
        if folder_move:
            vault.writer.move_folder("A", "C")
        else:
            vault.writer.rename("A/source.md", "C/source.md", update_links=True)
        wait_for_writer_drain(vault)
        source_text = (tmp_path / "C/source.md").read_text(encoding="utf-8")
        source_callbacks = [
            c for c in callbacks if c[0] == (tmp_path / "C/source.md").resolve()
        ]
        assert [(content, op) for _, content, op in source_callbacks] == [
            (source_text, "rename")
        ]
        together = "C/together.md" if folder_move else "A/together.md"
        for _ in range(2):
            rows = vault.graph.get_outlinks("C/source.md")
            assert {r.link_text: r.target_path for r in rows} == {
                "x": "B/target.md",
                "y": together,
                "control": "B/target.md",
                "broken": "C/Missing.md",
            }
            assert len(vault.graph.get_backlinks("B/target.md")) == 2
            assert vault.graph.get_backlinks("C/B/target.md") == []
            assert "[control](../B/target.md)" in (tmp_path / "C/source.md").read_text(
                encoding="utf-8"
            )
            assert "[broken](Missing.md)" in source_text
            vault.index.reindex()
    finally:
        vault.close()


def test_rename_without_link_updates_keeps_source_bytes(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    for folder in ("A", "B", "C/B"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "B/target.md").write_text("# target\n", encoding="utf-8")
    (tmp_path / "C/B/target.md").write_text("# shadow\n", encoding="utf-8")
    body = "[x](B/target.md)\n"
    (tmp_path / "A/source.md").write_text(body, encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault.writer.rename("A/source.md", "C/source.md")
        wait_for_writer_drain(vault)
        assert (tmp_path / "C/source.md").read_text(encoding="utf-8") == body
        assert vault.graph.get_outlinks("C/source.md")[0].target_path == "C/B/target.md"
    finally:
        vault.close()
