"""Unexpected changes to designated generated navigation are reported."""

import logging
from pathlib import Path

import pytest

from tests.fixtures.designated_index import designated_vault


@pytest.mark.parametrize("persistent", [False, True])
def test_fresh_baseline_and_divergences_are_capped_and_deduplicated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, persistent: bool
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    header = b'---\nokf_version: "0.2"\n---\n'
    index.write_bytes(header + b"# Original\n")
    (root / "note.md").write_text("# Note\n")
    db = tmp_path / "db" if persistent else None
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 0
        with caplog.at_level(logging.WARNING):
            for i in range(12):
                index.write_bytes(header + f"# Edit {i}\n".encode())
                vault.index.reindex()
            index.write_bytes(header + b"# Edit 0\n")
            vault.index.reindex()
        assert index.read_bytes().startswith(header)
        assert b"# Edit" not in index.read_bytes()
        divergences = vault.reader.okf_stats()["navigation"]["divergences"]
        assert divergences["count"] == 12
        assert len(divergences["examples"]) == 10
        assert sum("okf_index_divergence " in r.message for r in caplog.records) == 12
    finally:
        vault.close()
    if not persistent:
        return
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        index.write_bytes(header + b"# Edit 0\n")
        vault.index.reindex()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 12
    finally:
        vault.close()


def test_explicit_generation_tracks_parent(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    child = root / "child"
    child.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (child / "note.md").write_text("# Note\n")
    vault = designated_vault(root, (".", "child"), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        vault.writer.okf_generate_index(folder="child")
        (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Edited root\n')
        (child / "index.md").write_text("# Edited child\n")
        vault.writer.okf_generate_index(folder="child")
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 2
        ledger = vault._index_mgr.observations
        assert ledger is not None and ledger._path is not None
        vault.index.reindex()
        before = ledger._path.stat().st_mtime_ns
        vault.writer.okf_generate_index(folder="child")
        assert ledger._path.stat().st_mtime_ns == before
    finally:
        vault.close()


def test_new_desired_body_on_disk_is_not_divergence(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Original\n')
    note = root / "note.md"
    note.write_text("# Before\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.index.reindex()
        note.write_text("# After\n")
        vault.index.reindex()
        desired = index.read_text()
        note.write_text("# Before\n")
        vault.index.reindex()
        note.write_text("# After\n")
        index.write_text(desired)
        vault.index.reindex()
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 0
    finally:
        vault.close()


def test_future_ledger_refusal_leaves_index_untouched(tmp_path: Path) -> None:
    import json

    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text("# Root\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        ledger = vault._index_mgr.observations
        assert ledger is not None and ledger._path is not None
        data = json.loads(ledger._path.read_text())
        data["version"] = 4
        ledger._path.write_text(json.dumps(data))
        index.write_text("# Must survive\n")
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.writer.okf_generate_index(folder="")
        assert index.read_text() == "# Must survive\n"
    finally:
        vault.close()


@pytest.mark.parametrize("bad", [b"\xff", b"---\ntitle: [broken\n---\n"])
def test_unreadable_index_does_not_block_other_folders_and_retries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, bad: bytes
) -> None:
    root = tmp_path / "vault"
    for folder in ("a", "b"):
        (root / folder).mkdir(parents=True)
        (root / folder / "note.md").write_text("---\ntitle: Before\n---\n# Note\n")
    vault = designated_vault(root, ("a", "b"), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="a")
        vault.writer.okf_generate_index(folder="b")
        vault.index.reindex()
        index_b = root / "b/index.md"
        index_b.write_bytes(bad)
        for folder in ("a", "b"):
            (root / folder / "note.md").write_text("---\ntitle: After\n---\n# Note\n")
        with caplog.at_level(logging.WARNING):
            vault.index.reindex()
        assert "After" in (root / "a/index.md").read_text()
        assert index_b.read_bytes() == bad
        assert any("path=b/index.md" in record.message for record in caplog.records)
        index_b.write_text("# Repaired manually\n")
        vault.index.reindex()
        assert "After" in index_b.read_text()
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


@pytest.mark.parametrize("ending", [b"\r\n", b"\r"])
@pytest.mark.parametrize("entry", ["explicit", "reindex"])
def test_body_line_endings_are_compared_as_stored(
    tmp_path: Path, ending: bytes, entry: str
) -> None:
    root = tmp_path / "vault"
    for folder in ("a", "b"):
        (root / folder).mkdir(parents=True)
        (root / folder / "note.md").write_text("# Note\n")
    index = root / "a/index.md"
    header = b'---\r\nokf_version: "0.2"\r\n---\r\n'
    index.write_bytes(header + b"# Before\n")
    vault = designated_vault(root, ("a", "b"), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="a")
        vault.writer.okf_generate_index(folder="b")
        vault.index.reindex()
        baseline = index.read_bytes()
        assert baseline.startswith(header)
        body = baseline[len(header) :]
        index.write_bytes(header + body.replace(b"\n", ending))
        if entry == "explicit":
            vault.writer.okf_generate_index(folder="a")
        else:
            vault.index.reindex()
        assert index.read_bytes() == baseline
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


def test_bare_cr_header_and_leading_newline_body_stay_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.managers.okf_migrate as migration

    root = tmp_path / "vault"
    root.mkdir()
    header = b'---\rokf_version: "0.2"\r---\r'
    index = root / "index.md"
    index.write_bytes(header + b"# Original\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        monkeypatch.setattr(migration, "_render_fold", lambda *_args: "\n# Listing\n")
        vault.writer.okf_generate_index(folder="")
        generated = index.read_bytes()
        assert generated == header + b"\r\n# Listing\n"
        vault.writer.okf_generate_index(folder="")
        vault.index.reindex()
        assert index.read_bytes() == generated
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 0
    finally:
        vault.close()


def test_header_only_edit_is_preserved_without_divergence(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_bytes(b'---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        old = index.read_bytes()
        header = b'---\r\nokf_version: "0.2"\r\ntitle: Local\r\n---\r\n'
        index.write_bytes(header + old.split(b"---\n", 2)[2])
        vault.index.reindex()
        vault.writer.okf_generate_index(folder="")
        assert index.read_bytes().startswith(header)
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 0
    finally:
        vault.close()
