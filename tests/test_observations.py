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

        from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

        with pytest.raises(
            StoreUnsupportedSchemaError, match="Unsupported observation schema"
        ):
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
        monkeypatch.setattr(ledger._inspector, "observe_files", lambda _visit: {""})
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


def test_unrelated_symlink_and_denied_subtree_do_not_hide_removal(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp import bundle_inventory

    source = tmp_path / "vault"
    source.mkdir()
    (source / "private").mkdir()
    (source / "private" / "kept.md").write_text("kept")
    gone = source / "gone.md"
    gone.write_text("gone")
    (source / "other.png").symlink_to("nonexistent")
    (source / "alias.md").symlink_to("gone.md")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        gone.unlink()
        original = bundle_inventory.open_child_directory

        def denied(fd, name):
            if name == "private":
                raise PermissionError("denied")
            return original(fd, name)

        monkeypatch.setattr(bundle_inventory, "open_child_directory", denied)
        vault._index_mgr.observations.sweep()
        rows = vault._index_mgr.observations.rows()
        assert rows["gone.md"].presence == "removed"
        assert rows["private/kept.md"].presence == "present"
        assert "alias.md" not in rows
    finally:
        vault.close()


def test_deep_observable_note(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    folder = source
    for _ in range(40):
        folder = folder / "d"
        folder.mkdir()
    (folder / "deep.md").write_text("deep")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        assert (
            "/".join(["d"] * 40 + ["deep.md"]) in vault._index_mgr.observations.rows()
        )
    finally:
        vault.close()


def test_frontmatter_digest_is_exact_header_bytes(tmp_path: Path) -> None:
    import hashlib

    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    header = b"\xef\xbb\xbf---\r\ntitle: A\r\n---"
    note.write_bytes(header + b"\r\nbody")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"].frontmatter_digest
        assert first == hashlib.sha256(header).hexdigest()
        note.write_bytes(header + b"\r\n\r\nbody")
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["a.md"].frontmatter_digest == first
    finally:
        vault.close()


def test_unchanged_ledger_needs_no_replace(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()

        def no_save(_rows):
            raise AssertionError("unchanged ledger was rewritten")

        monkeypatch.setattr(vault._index_mgr.observations, "_save", no_save)
        vault.index.build_index()
    finally:
        vault.close()


def test_warm_ledger_failure_preserves_search_readiness(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(
        source_dir=source, settings=VaultSettings(index_path=tmp_path / "index.sqlite")
    )
    try:
        vault.index.build_index()
        vault.close()
        vault = Vault(
            source_dir=source,
            settings=VaultSettings(index_path=tmp_path / "index.sqlite"),
        )

        def fail():
            raise PermissionError("ledger denied")

        monkeypatch.setattr(vault._index_mgr.observations, "sweep", fail)
        with pytest.raises(PermissionError, match="ledger denied"):
            vault.index.build_index()
        assert vault.index.is_queryable()
    finally:
        vault.close()


def test_symlink_replacement_retains_old_row_without_blocking_other_removal(
    tmp_path: Path,
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    first = source / "first.md"
    first.write_text("first")
    second = source / "second.md"
    second.write_text("second")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        first.unlink()
        first.symlink_to("second.md")
        second.unlink()
        vault._index_mgr.observations.sweep()
        rows = vault._index_mgr.observations.rows()
        assert rows["first.md"].presence == "present"
        assert rows["second.md"].presence == "removed"
    finally:
        vault.close()


def test_empty_missing_or_corrupt_ledger_is_recreated(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, InspectionPolicy
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    source = tmp_path / "vault"
    source.mkdir()
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(
        path, BundleInspector(source, (), frozenset(), 0, InspectionPolicy())
    )
    ledger.sweep()
    assert '"version": 1' in path.read_text()
    path.write_text("corrupt")
    ledger.sweep()
    assert '"version": 1' in path.read_text()


def test_stream_hash_handles_long_header_and_split_crlf() -> None:
    import hashlib
    import io

    from markdown_vault_mcp.bundle_inventory import _observation_digests

    class Limited(io.BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= 65536
            return super().readline(size)

    header = b"---\r\nkey: " + b"x" * 65530 + b"\r\n---"
    content = header + b"\r\n\r\nbody"
    full, frontmatter = _observation_digests(Limited(content))
    assert full == hashlib.sha256(content).hexdigest()
    assert frontmatter == hashlib.sha256(header).hexdigest()
    header = b"---\nkey: x\n" + b"-" * 65535
    full, frontmatter = _observation_digests(Limited(header + b"\r\nbody"))
    assert frontmatter == hashlib.sha256(header).hexdigest()


def test_header_digest_handles_empty_body_json_and_missing_closer() -> None:
    import hashlib
    import io

    from markdown_vault_mcp.bundle_inventory import _observation_digests

    for content, header in [
        (b"---\na: b\n---", b"---\na: b\n---"),
        (b'{\n"a": 1\n}\n\nbody', b'{\n"a": 1\n}'),
        (b"---\na: b\n", None),
    ]:
        digest, frontmatter = _observation_digests(io.BytesIO(content))
        assert digest == hashlib.sha256(content).hexdigest()
        assert frontmatter == (hashlib.sha256(header).hexdigest() if header else None)


def test_frontmatter_hash_includes_leading_whitespace_recognized_by_scanner() -> None:
    import hashlib
    import io

    from markdown_vault_mcp.bundle_inventory import _observation_digests

    header = b"\n  ---\ntitle: A\n---"
    _, digest = _observation_digests(io.BytesIO(header + b"\nbody"))
    assert digest == hashlib.sha256(header).hexdigest()
