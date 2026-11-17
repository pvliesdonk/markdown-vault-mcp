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


def test_suffix_tie_break_and_no_extension_inference() -> None:
    idx = _index(
        "src/source.md",
        "[x](Note.md) [y](Missing) [p](b/Note.md)",
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
        links = idx.get_outlinks("src/source.md")
        assert [link["target_path"] for link in links] == [
            "aa/Note.md",
            "src/Missing",
            "z/b/Note.md",
        ]
        assert len(idx.get_broken_links()) == 1
    finally:
        idx.close()


def test_markdown_does_not_infer_aliases() -> None:
    idx = _index("src/source.md", "[x](Alias.md)", [])
    try:
        idx.upsert_note(
            make_note(path="sub/other.md", frontmatter={"aliases": ["Alias.md"]})
        )
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("src/source.md")[0]["target_path"] == "src/Alias.md"
        assert len(idx.get_broken_links()) == 1
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


def test_root_source_fallback_survives_target_rename(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "Sub").mkdir()
    (tmp_path / "Sub/My Note.md").write_text("# Target\n", encoding="utf-8")
    (tmp_path / "source.md").write_text("[x](My%20Note.md)\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault.writer.rename("Sub/My Note.md", "Sub/Renamed.md", update_links=True)
        wait_for_writer_drain(vault)
        assert vault.graph.get_outlinks("source.md")[0].target_path == "Sub/Renamed.md"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("reference", "move_folder"),
    [(False, False), (True, True)],
)
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


def _source_move_fixture(tmp_path: Path) -> None:
    for folder in ("A", "B", "C/B"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "B/target.md").write_text("# target\n", encoding="utf-8")
    (tmp_path / "C/B/target.md").write_text("# shadow\n", encoding="utf-8")


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("folder_move", [False, True])
def test_moving_source_preserves_fallback_targets(
    tmp_path: Path, reference: bool, folder_move: bool
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    _source_move_fixture(tmp_path)
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
        assert vault._write_callback.drain()
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

    _source_move_fixture(tmp_path)
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


def test_source_move_does_not_cascade_rewrites(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    for folder in ("A", "B", "C"):
        (tmp_path / folder).mkdir()
    (tmp_path / "A/source.md").write_text(
        "[self](source.md) [other](new.md)\n", encoding="utf-8"
    )
    (tmp_path / "B/new.md").write_text("# Other\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        before = vault.reader.read("A/source.md").etag
        vault.writer.rename("A/source.md", "C/new.md", update_links=True)
        wait_for_writer_drain(vault)
        after = vault.reader.read("C/new.md").etag
        assert (
            vault.writer._doc_mgr._link_rewrites.rewrites_since(
                "C/new.md", etag=before, current=after
            )
            == 1
        )
        assert {
            row.link_text: row.target_path
            for row in vault.graph.get_outlinks("C/new.md")
        } == {"self": "C/new.md", "other": "B/new.md"}
    finally:
        vault.close()


@pytest.mark.parametrize("raw", ["Bad%2FName.md", "Bad%00Name.md", "Bad%FFName.md"])
def test_refused_escapes_do_not_fallback_and_are_reported(
    tmp_path: Path, raw: str
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "Sub").mkdir()
    (tmp_path / "Sub" / raw).write_text("# Unrelated\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        result = vault.writer.write(
            "root.md", f"[x]({raw})\n", report_unresolved_links=True
        )
        assert result.unresolved_links == [raw]
        assert not vault.graph.get_outlinks("root.md")[0].exists
        assert [b.raw_target for b in vault.graph.get_broken_links()] == [raw]
        (tmp_path / raw).write_text("# Local literal\n", encoding="utf-8")
        vault.index.reindex()
        assert vault.graph.get_outlinks("root.md")[0].exists
    finally:
        vault.close()


def test_failed_source_rewrite_reports_partial_rename(tmp_path: Path) -> None:
    from unittest.mock import patch

    from markdown_vault_mcp.vault import Vault, VaultSettings

    _source_move_fixture(tmp_path)
    (tmp_path / "A/source.md").write_text("[x](B/target.md)\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        with patch.object(
            vault.writer._doc_mgr,
            "_rewrite_one_source",
            side_effect=OSError("disk full"),
        ):
            result = vault.writer.rename(
                "A/source.md", "C/source.md", update_links=True
            )
        wait_for_writer_drain(vault)
        assert (tmp_path / "C/source.md").is_file()
        assert result.hint is not None
        assert "C/source.md" in result.hint
        assert result.updated_links == 0
        assert vault.graph.get_outlinks("C/source.md")[0].target_path == "C/B/target.md"
    finally:
        vault.close()


def test_double_encoded_escape_can_resolve_literal_name(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "Sub").mkdir()
    (tmp_path / "Sub/Bad%2FName.md").write_text("# Target\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        result = vault.writer.write(
            "root.md", "[x](Bad%252FName.md)", report_unresolved_links=True
        )
        assert result.unresolved_links == []
        assert vault.graph.get_outlinks("root.md")[0].target_path == "Sub/Bad%2FName.md"
    finally:
        vault.close()


def test_folder_move_does_not_cascade_suffix_rewrites(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "A/A2").mkdir(parents=True)
    (tmp_path / "A/a.md").write_text("# First\n", encoding="utf-8")
    (tmp_path / "A/A2/a.md").write_text("# Second\n", encoding="utf-8")
    (tmp_path / "source.md").write_text(
        "[first](A/a.md) [second](A2/a.md)\n", encoding="utf-8"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        assert {
            r.link_text: r.target_path for r in vault.graph.get_outlinks("source.md")
        } == {"first": "A/a.md", "second": "A/A2/a.md"}
        vault.writer.move_folder("A", "A2")
        wait_for_writer_drain(vault)
        assert {
            r.link_text: r.target_path for r in vault.graph.get_outlinks("source.md")
        } == {"first": "A2/a.md", "second": "A2/A2/a.md"}
    finally:
        vault.close()


@pytest.mark.parametrize("folder_move", [False, True])
@pytest.mark.parametrize("reference", [False, True])
def test_moved_source_preserves_code_examples(
    tmp_path: Path, folder_move: bool, reference: bool
) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "B/target.md").write_text("# target\n", encoding="utf-8")
    real = (
        "[real][r]\n\n[r]: B/target.md\n"
        if reference
        else '[real](B/target.md "a `title` survives") trailing prose\n'
    )
    example = "[r]: B/target.md" if reference else "[example](B/target.md)"
    code = f"`{example}`\n\n```md\n{example}\n```\n"
    (tmp_path / "A/source.md").write_text(real + code, encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        if folder_move:
            vault.writer.move_folder("A", "C")
        else:
            (tmp_path / "C").mkdir()
            vault.writer.rename("A/source.md", "C/source.md", update_links=True)
        wait_for_writer_drain(vault)
        result = (tmp_path / "C/source.md").read_text(encoding="utf-8")
        assert code in result
        assert "../B/target.md" in result
        if not reference:
            assert '"a `title` survives") trailing prose' in result
        assert vault.graph.get_outlinks("C/source.md")[0].target_path == "B/target.md"
    finally:
        vault.close()
