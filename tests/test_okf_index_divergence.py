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
                edited = header + f"# Edit {i}\n".encode()
                index.write_bytes(edited)
                vault.index.reindex()
                assert index.read_bytes() == edited
                vault.writer.okf_generate_index(folder="")
            index.write_bytes(header + b"# Edit 0\n")
            vault.writer.okf_generate_index(folder="")
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
        vault.writer.okf_generate_index(folder="")
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
        assert vault.index.wait_for_drain(timeout=10.0)
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
            assert "Repaired manually" in index_b.read_text()
            vault.writer.okf_generate_index(folder="b")
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
            (root / "a/note.md").write_text("# Note\n\nChanged body\n")
            vault.index.reindex()
        assert index.read_bytes() == baseline
        stats = vault.reader.okf_stats()
        assert stats is not None
        assert stats["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


@pytest.mark.parametrize(
    "header",
    [
        b"",
        b"---\ntitle: Root\n---\n",
        b"---\r\ntitle: Root\r\n---\r\n",
        b"---\rtitle: Root\r---\r",
        b"\xef\xbb\xbf---\ntitle: Root\n---\n",
    ],
)
def test_generated_body_matches_stored_bytes(tmp_path: Path, header: bytes) -> None:
    from markdown_vault_mcp.managers.document import stored_body

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_bytes(header + b"# Original\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        generated = index.read_bytes()
        assert generated.startswith(header)
        ledger = vault._index_mgr.observations
        assert ledger is not None
        import json

        from markdown_vault_mcp.hashing import compute_etag

        assert ledger._path is not None
        state = json.loads(ledger._path.read_text())
        assert state["navigation"]["generated"]["index.md"] == compute_etag(
            stored_body(generated.decode("utf-8"))
        )
        vault.writer.okf_generate_index(folder="")
        vault.index.reindex()
        assert index.read_bytes() == generated
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 0
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


def test_policy_off_generation_updates_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Before\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        manager = vault.writer._okf_migrate
        assert manager is not None
        with monkeypatch.context() as patch:
            patch.setattr(manager, "_index_designated", lambda _folder: False)
            (root / "note.md").write_text("# Changed\n")
            vault.index.reindex()
            vault.writer.okf_generate_index(folder="")
        (root / "other.md").write_text("# Other\n")
        vault.index.reindex()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 0
    finally:
        vault.close()


@pytest.mark.parametrize("timing", ["before", "after"])
def test_external_edit_during_generation_is_not_lost_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:
    from markdown_vault_mcp.exceptions import ConcurrentModificationError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    index = root / "index.md"
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        vault.index.reindex()
        ledger = vault._index_mgr.observations
        assert ledger is not None
        with monkeypatch.context() as patch:
            if timing == "before":
                original = ledger.observe_navigation

                def change_before(*args: object) -> None:
                    original(*args)
                    index.write_text("# External edit\n")

                patch.setattr(ledger, "observe_navigation", change_before)
                with pytest.raises(ConcurrentModificationError):
                    vault.writer.okf_generate_index(folder="")
            else:
                original_replace = vault._doc_mgr.replace_body

                def change_after(*args: object, **kwargs: object) -> bool:
                    result = original_replace(*args, **kwargs)
                    index.write_text("# External edit\n")
                    return result

                patch.setattr(vault._doc_mgr, "replace_body", change_after)
                vault.writer.okf_generate_index(folder="")
        assert index.read_text() == "# External edit\n"
        vault.index.reindex()
        assert index.read_text() == "# External edit\n"
        (root / "note.md").write_text("# Note\n\nChanged body\n")
        vault.index.reindex()
        assert index.read_text() != "# External edit\n"
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


@pytest.mark.parametrize(
    "replacement",
    [
        b"---\ntitle: Changed\n---\n# Edit\n",
        b"---\ntitle: [broken\n---\n",
        b"\xff",
        b"\xef\xbb\xbf---\ntitle: [broken\n---\n",
    ],
)
def test_snapshot_change_after_preliminary_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bytes
) -> None:
    from markdown_vault_mcp.exceptions import DocumentUnreadableError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    index = root / "index.md"
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        original = vault._doc_mgr._read_note
        with monkeypatch.context() as patch:

            def change_after_read(path: str, capped: bool) -> object:
                result = original(path, capped)
                if path == "index.md":
                    index.write_bytes(replacement)
                return result

            patch.setattr(vault._doc_mgr, "_read_note", change_after_read)
            if b"broken" in replacement or replacement == b"\xff":
                with pytest.raises(DocumentUnreadableError):
                    vault.writer.okf_generate_index(folder="")
                assert index.read_bytes() == replacement
            else:
                vault.writer.okf_generate_index(folder="")
                assert index.read_bytes().startswith(b"---\ntitle: Changed\n---\n")
                assert (
                    vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
                )
    finally:
        vault.close()


def test_conflict_during_reindex_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    note = root / "note.md"
    note.write_text("# Before\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        vault.index.reindex()
        note.write_text("# After\n")
        ledger = vault._index_mgr.observations
        assert ledger is not None
        original = ledger.observe_navigation
        with monkeypatch.context() as patch:

            def change_before(path: str, old: bytes | None, new: bytes) -> None:
                original(path, old, new)
                index.write_text("# External edit\n")

            patch.setattr(ledger, "observe_navigation", change_before)
            vault.index.reindex()
        assert index.read_text() == "# External edit\n"
        vault.index.reindex()
        assert "After" in index.read_text()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


def test_future_ledger_refuses_new_index(tmp_path: Path) -> None:
    import json

    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        assert vault.index.wait_for_drain(timeout=10.0)
        ledger = vault._index_mgr.observations
        assert ledger is not None and ledger._path is not None
        data = json.loads(ledger._path.read_text())
        data["version"] = 4
        ledger._path.write_text(json.dumps(data))
        index = root / "index.md"
        index.unlink()
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.writer.okf_generate_index(folder="")
        assert not index.is_file()
    finally:
        vault.close()


@pytest.mark.parametrize("first,second", [("alias", "a"), ("a", "alias")])
@pytest.mark.parametrize("restart", [False, True])
def test_generation_alias_shares_canonical_history(
    tmp_path: Path, first: str, second: str, restart: bool
) -> None:
    root = tmp_path / "vault"
    (root / "a").mkdir(parents=True)
    (root / "a/note.md").write_text("# Note\n")
    (root / "alias").symlink_to(root / "a", target_is_directory=True)
    db = tmp_path / "db"
    vault = designated_vault(root, ("a",), index_path=db)
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder=first)
        if restart:
            vault.close()
            vault = designated_vault(root, ("a",), index_path=db)
            vault.index.build_index()
        index = root / "a/index.md"
        index.write_text("# Unexpected\n")
        vault.writer.okf_generate_index(folder=second)
        divergences = vault.reader.okf_stats()["navigation"]["divergences"]
        assert divergences["count"] == 1
        assert divergences["examples"][0]["path"] == "a/index.md"
        index.write_text("# Unexpected\n")
        vault.writer.okf_generate_index(folder=first)
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()
