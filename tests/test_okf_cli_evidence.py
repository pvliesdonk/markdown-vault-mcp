from pathlib import Path

import pytest

from markdown_vault_mcp._cli_bundle import _local
from markdown_vault_mcp._cli_read import local_read
from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_local_cli_reads_persistent_receipts_and_removals_without_disk_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    original = "---\ntype: concept\n---\nBody\n"
    note = root / "note.md"
    note.write_text(original)
    lost = root / "lost.md"
    lost.write_text("lost")
    state = tmp_path / "state"
    vault = Vault(
        source_dir=root, settings=VaultSettings(read_only=False, state_path=state)
    )
    try:
        vault._index_mgr.observations.sweep()
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        note.write_text(note.read_text().replace("Body", "Changed"))
        lost.unlink()
        vault._index_mgr.observations.sweep()
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_INDEX_PATH", raising=False)
        args = {"type_state": None, "parse_state": None, "search_state": None}
        inventory = _local(args, None, BundleTreeRequest(root=""))
        assert [
            (e["path"], e["presence"])
            for e in inventory["entries"]
            if e["path"] == "lost.md"
        ] == [("lost.md", "removed")]
        result = local_read("read", {"path": "note.md"}, None)
        assert result["okf"]["observation"] == {
            "state": "available",
            "later_change_observed": True,
        }
    finally:
        vault.close()


@pytest.mark.parametrize("explicit_state", [True, False])
def test_read_of_explicit_other_vault_does_not_borrow_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_state: bool
) -> None:
    import shutil

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    original = "---\ntype: concept\n---\nBody\n"
    (root / "note.md").write_text(original)
    state = tmp_path / "state"
    index = tmp_path / "index.db"
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            state_path=state if explicit_state else None,
            index_path=index,
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        copied = tmp_path / "copy"
        shutil.copytree(root, copied)
        (root / "lost.md").write_text("lost")
        vault._index_mgr.observations.sweep()
        (root / "lost.md").unlink()
        vault._index_mgr.observations.sweep()
        note = copied / "note.md"
        note.write_text(note.read_text().replace("Body", "Changed"))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
        if explicit_state:
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
        else:
            monkeypatch.delenv("MARKDOWN_VAULT_MCP_STATE_PATH", raising=False)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
        result = local_read("read", {"path": "note.md"}, str(copied))
        assert result["okf"]["observation"] == {"state": "unavailable"}
        args = {"type_state": None, "parse_state": None, "search_state": None}
        inventory = _local(args, str(copied), BundleTreeRequest(root=""))
        assert any(e["path"] == "note.md" for e in inventory["entries"])
        assert inventory["enumeration"] == "complete"
        assert inventory["removal_history"] == "unavailable"
        assert not any(e["path"] == "lost.md" for e in inventory["entries"])
    finally:
        vault.close()


def test_in_memory_cli_queries_preserve_persistent_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (root / "note.md").write_text("---\ntype: concept\n---\nBody\n")
    state = tmp_path / "state"
    vault = Vault(
        source_dir=root, settings=VaultSettings(read_only=False, state_path=state)
    )
    try:
        vault.index.build_index()
        vault.writer.okf_verify(
            "note.md", "alice", concept_snapshot((root / "note.md").read_text())
        )
    finally:
        vault.close()
    before = {p.name: p.read_bytes() for p in tmp_path.glob("state*")}
    (root / "new.md").write_text("A change a sweep would record")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_INDEX_PATH", raising=False)
    local_read("get_orphan_notes", {}, None)
    local_read("count_documents", {}, None)
    assert {p.name: p.read_bytes() for p in tmp_path.glob("state*")} == before


def test_inventory_uses_default_state_when_index_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    lost = root / "lost.md"
    lost.write_text("lost")
    index = tmp_path / "index.db"
    vault = Vault(source_dir=root, settings=VaultSettings(index_path=index))
    try:
        vault._index_mgr.observations.sweep()
        lost.unlink()
        vault._index_mgr.observations.sweep()
    finally:
        vault.close()
    index.unlink(missing_ok=True)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_STATE_PATH", raising=False)
    args = {"type_state": None, "parse_state": None, "search_state": None}
    inventory = _local(args, None, BundleTreeRequest(root=""))
    assert any(
        e["path"] == "lost.md" and e["presence"] == "removed"
        for e in inventory["entries"]
    )


@pytest.mark.parametrize("index_exists", [True, False])
def test_source_override_preserves_configured_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index_exists: bool
) -> None:
    import typer

    default = tmp_path / "default"
    chosen = tmp_path / "chosen"
    default.mkdir()
    chosen.mkdir()
    (chosen / "first.md").write_text("first")
    index = tmp_path / "index.db"
    if index_exists:
        vault = Vault(source_dir=chosen, settings=VaultSettings(index_path=index))
        try:
            vault.index.build_index()
        finally:
            vault.close()
    (chosen / "second.md").write_text("second")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(default))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    if not index_exists:
        with pytest.raises(typer.Exit):
            local_read("count_documents", {}, str(chosen))
        assert not index.exists()
    else:
        assert local_read("count_documents", {}, str(chosen))["total"] == 1
