"""Unexpected changes to designated generated navigation are reported."""

import logging
from pathlib import Path

import pytest

from tests.fixtures.designated_index import designated_vault


def test_external_edit_is_reported_once_and_regenerated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    header = b'---\nokf_version: "0.2"\n---\n'
    index.write_bytes(header + b"# Original\n")
    (root / "note.md").write_text("# Note\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 0
        index.write_bytes(header + b"# Hand edited\n")
        with caplog.at_level(logging.WARNING):
            vault.index.reindex()
        assert index.read_bytes().startswith(header)
        assert b"Hand edited" not in index.read_bytes()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        index.write_bytes(header + b"# Hand edited\n")
        vault.index.reindex()
        assert vault.reader.okf_stats()["navigation"]["divergences"]["count"] == 1
    finally:
        vault.close()


def test_explicit_generation_tracks_parent_and_leaves_authored_and_log(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    child = root / "child"
    authored = root / "authored"
    child.mkdir(parents=True)
    authored.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (authored / "index.md").write_text("# Authored\n")
    (root / "log.md").write_text("# Log\n")
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
        assert (authored / "index.md").read_text() == "# Authored\n"
        assert (root / "log.md").read_text() == "# Log\n"
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


def test_examples_are_capped_and_warning_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    header = '---\nokf_version: "0.2"\n---\n'
    index.write_text(header + "# Original\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.index.reindex()
        with caplog.at_level(logging.WARNING):
            for i in range(12):
                index.write_text(header + f"# Edit {i}\n")
                vault.index.reindex()
            index.write_text(header + "# Edit 0\n")
            vault.index.reindex()
        stats = vault.reader.okf_stats()
        assert stats is not None
        divergences = stats["navigation"]["divergences"]
        assert divergences["count"] == 12
        assert len(divergences["examples"]) == 10
        assert sum("okf_index_divergence " in r.message for r in caplog.records) == 12
    finally:
        vault.close()


def test_invalid_history_is_reset_and_future_version_refused(tmp_path: Path) -> None:
    import json

    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    header = '---\nokf_version: "0.2"\n---\n'
    index.write_text(header + "# Root\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "db")
    try:
        vault.index.build_index()
        vault.index.reindex()
        ledger = vault._index_mgr.observations
        assert ledger is not None and ledger._path is not None
        data = json.loads(ledger._path.read_text())
        del data["navigation"]
        ledger._path.write_text(json.dumps(data))
        assert ledger.navigation_stats()["count"] == 0
        data["navigation"] = {"version": 1, "generated": [], "reported": {}}
        ledger._path.write_text(json.dumps(data))
        assert ledger.navigation_stats()["count"] == 0
        data["navigation"] = {
            "version": 1,
            "generated": {"index.md": "invalid"},
            "reported": {},
        }
        ledger._path.write_text(json.dumps(data))
        assert ledger.generated_navigation() == {}
        data["navigation"] = {
            "version": 1,
            "generated": {},
            "reported": {"index.md": "not a list"},
        }
        ledger._path.write_text(json.dumps(data))
        index.write_text(header + "# Untracked\n")
        vault.writer.okf_generate_index(folder="")
        assert ledger.navigation_stats()["count"] == 0
        data = json.loads(ledger._path.read_text())
        data["navigation"]["version"] = 999
        ledger._path.write_text(json.dumps(data))
        index.write_text(header + "# Must survive\n")
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.writer.okf_generate_index(folder="")
        assert index.read_text() == header + "# Must survive\n"
    finally:
        vault.close()
