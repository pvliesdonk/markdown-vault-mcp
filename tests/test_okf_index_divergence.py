"""Unexpected changes to designated generated navigation are reported."""

import logging
from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault
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


def _refuse_raw_after_parse(
    vault: Vault, index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read = Path.read_bytes
    original_note = vault._doc_mgr._read_note

    def refuse_raw(path: Path) -> bytes:
        if path == index:
            raise PermissionError("temporary read failure")
        return original_read(path)

    def arm_raw_failure(path: str, capped: bool) -> object:
        note = original_note(path, capped)
        if path == "b/index.md":
            monkeypatch.setattr(Path, "read_bytes", refuse_raw)
        return note

    monkeypatch.setattr(vault._doc_mgr, "_read_note", arm_raw_failure)


@pytest.mark.parametrize("bad", [b"\xff", b"---\ntitle: [broken\n---\n", None])
@pytest.mark.parametrize("concept_change", [False, True])
def test_unreadable_index_does_not_block_other_folders_and_retries(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    bad: bytes | None,
    concept_change: bool,
    monkeypatch: pytest.MonkeyPatch,
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
        before = index_b.read_bytes()
        if bad is not None:
            index_b.write_bytes(bad)
        else:
            _refuse_raw_after_parse(vault, index_b, monkeypatch)
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                vault.index.reindex()
        assert not any(
            "okf_convention_index_failed" in r.message for r in caplog.records
        )
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 0
        if not concept_change:
            monkeypatch.undo()
            assert index_b.read_bytes() == (bad if bad is not None else before)
            index_b.write_text("# Repaired manually\n")
            vault.index.reindex()
            assert "Repaired manually" not in index_b.read_text()
            stats = vault.reader.okf_stats()
            assert stats is not None
            assert stats["navigation"]["divergences"]["count"] == 1
            return
        for folder in ("a", "b"):
            (root / folder / "note.md").write_text("---\ntitle: After\n---\n# Note\n")
        with caplog.at_level(logging.WARNING):
            vault.index.reindex()
        assert "After" in (root / "a/index.md").read_text()
        assert any("path=b/index.md" in record.message for record in caplog.records)
        monkeypatch.undo()
        assert index_b.read_bytes() == (bad if bad is not None else before)
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


@pytest.mark.parametrize("initial_newline", [b"", b"\n# Root\n"])
def test_header_only_edit_is_preserved_without_divergence(
    tmp_path: Path, initial_newline: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_bytes(b'---\nokf_version: "0.2"\n---' + initial_newline)
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        vault.index.reindex()
        old = index.read_bytes()
        with monkeypatch.context() as patch:
            original = vault._doc_mgr._read_note
            patch.setattr(
                vault._doc_mgr,
                "_read_note",
                lambda path, capped: (
                    None if path == "index.md" else original(path, capped)
                ),
            )
            vault.index.reindex()
        assert index.read_bytes() == old
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
