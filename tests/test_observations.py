"""Durable observation sweeps cover the inventory rather than search scope."""

from pathlib import Path

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_build_observes_excluded_hidden_and_unparseable_notes(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".hidden").mkdir()
    (source / "excluded").mkdir()
    (source / "good.md").write_text("---\ntitle: Good\n---\nbody")
    (source / ".hidden" / "broken.md").write_bytes(b"\xff")
    (source / "excluded" / "large.md").write_text("x" * 70000)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(
            state_path=tmp_path / "state.json", exclude_patterns=["excluded/**"]
        ),
    )
    try:
        vault.index.build_index()
        rows = vault._index_mgr.observations.rows()
        assert set(rows) == {"good.md", ".hidden/broken.md", "excluded/large.md"}
        assert all(row.content_digest for row in rows.values())
        assert all(row.origin == "unknown" for row in rows.values())
    finally:
        vault.close()


def test_reindex_compares_state_and_records_removal(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("---\ntitle: A\n---\nfirst")
    (source / "keep.md").write_text("keep")
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"]
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["a.md"] == first
        note.write_text("---\ntitle: A\n---\nsecond")
        vault.index.reindex()
        changed = vault._index_mgr.observations.rows()["a.md"]
        assert changed.first_seen_at == first.first_seen_at
        assert changed.content_digest != first.content_digest
        assert changed.frontmatter_digest == first.frontmatter_digest
        note.unlink()
        vault.index.reindex()
        removed = vault._index_mgr.observations.rows()["a.md"]
        assert removed.presence == "removed"
        assert removed.removed_observed_at is not None
        assert "never.md" not in vault._index_mgr.observations.rows()
        note.write_text("restored")
        vault.index.reindex()
        restored = vault._index_mgr.observations.rows()["a.md"]
        assert restored.presence == "present"
        assert restored.first_seen_at == first.first_seen_at
        assert restored.removed_observed_at is None
    finally:
        vault.close()


def test_corrupt_ledger_rebuilds_and_newer_version_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    state = tmp_path / "state"
    ledger = tmp_path / "state.observations.json"
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"]
        ledger.write_text("broken")
        vault.index.reindex()
        rebuilt = vault._index_mgr.observations.rows()["a.md"]
        assert rebuilt.first_seen_at > first.first_seen_at
        ledger.write_text('{"version": 999, "rows": {}}')
        import pytest

        with pytest.raises(RuntimeError, match="Unsupported observation schema"):
            vault.index.reindex()
        assert ledger.read_text() == '{"version": 999, "rows": {}}'
    finally:
        vault.close()


def test_incomplete_scope_does_not_create_removals(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        first = ledger.rows()["a.md"]
        monkeypatch.setattr(ledger._inspector, "observe_files", lambda _visit: False)
        vault.index.reindex()
        assert ledger.rows()["a.md"] == first
    finally:
        vault.close()


def test_warm_build_refreshes_observations(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("before")
    settings = VaultSettings(index_path=tmp_path / "index.sqlite")
    vault = Vault(source_dir=source, settings=settings)
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"]
        note.write_text("after")
        vault.index.build_index_async().result()
        assert (
            vault._index_mgr.observations.rows()["a.md"].content_digest
            != first.content_digest
        )
    finally:
        vault.close()


def test_missing_ledger_resets_history_without_writing_bundle(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    original = "---\ntitle: A\n---\nbody"
    note.write_text(original)
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    ledger_path = tmp_path / "state.observations.json"
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"]
        ledger_path.unlink()
        vault.index.reindex()
        assert (
            vault._index_mgr.observations.rows()["a.md"].first_seen_at
            > first.first_seen_at
        )
        assert note.read_text() == original
        assert {item.name for item in source.iterdir()} == {"a.md"}
    finally:
        vault.close()


def test_removal_of_last_file_is_recorded(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "only.md"
    note.write_text("hello")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        note.unlink()
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["only.md"].presence == "removed"
    finally:
        vault.close()


def test_unreadable_ledger_is_not_reset(tmp_path: Path, monkeypatch) -> None:
    import pytest

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    ledger_path = tmp_path / "state.observations.json"
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        before = ledger_path.read_bytes()
        original = Path.read_text

        def denied(path: Path, *args, **kwargs):
            if path == ledger_path:
                raise PermissionError("denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
        with pytest.raises(PermissionError, match="denied"):
            vault.index.reindex()
        assert ledger_path.read_bytes() == before
    finally:
        vault.close()


def test_corrupt_row_data_rebuilds_instead_of_preserving_false_history(
    tmp_path: Path,
) -> None:
    import json

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    ledger_path = tmp_path / "state.observations.json"
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        data = json.loads(ledger_path.read_text())
        data["rows"]["a.md"]["first_seen_at"] = None
        ledger_path.write_text(json.dumps(data))
        vault.index.reindex()
        assert isinstance(
            vault._index_mgr.observations.rows()["a.md"].first_seen_at, str
        )
    finally:
        vault.close()


def test_sweep_respects_inventory_roles_and_admission(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "secret.md").write_text("hidden")
    (source / "index.md").write_text("navigation")
    (source / "log.md").write_text("history")
    (source / "_conventions.md").write_text("support")
    (source / "untyped.md").write_text("no frontmatter")
    (source / "malformed.md").write_text("---\ntitle: [\n---\nbody")
    vault = Vault(
        source_dir=source, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        rows = vault._index_mgr.observations.rows()
        assert {path: row.role for path, row in rows.items()} == {
            "index.md": "navigation",
            "log.md": "history",
            "_conventions.md": "support",
            "untyped.md": "concept_candidate",
            "malformed.md": "concept_candidate",
        }
        assert rows["malformed.md"].frontmatter_digest is not None
    finally:
        vault.close()


def test_denied_directory_does_not_make_removal(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp import bundle_inventory

    source = tmp_path / "vault"
    source.mkdir()
    folder = source / "private"
    folder.mkdir()
    (folder / "a.md").write_text("hello")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        before = ledger.rows()["private/a.md"]
        original = bundle_inventory.open_child_directory

        def denied(fd: int, name: str) -> int:
            if name == "private":
                raise PermissionError("denied")
            return original(fd, name)

        monkeypatch.setattr(bundle_inventory, "open_child_directory", denied)
        ledger.sweep()
        assert ledger.rows()["private/a.md"] == before
    finally:
        vault.close()
