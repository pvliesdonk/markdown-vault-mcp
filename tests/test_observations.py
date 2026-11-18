"""Durable observation sweeps cover the inventory rather than search scope."""

from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_build_observes_excluded_hidden_and_unparseable_notes(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".hidden").mkdir()
    (source / "excluded").mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "secret.md").write_text("hidden")
    (source / "index.md").write_text("navigation")
    folder = source
    for _ in range(40):
        folder = folder / "d"
        folder.mkdir()
    (folder / "deep.md").write_text("deep")
    deep = "/".join(["d"] * 40 + ["deep.md"])
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
        assert set(rows) == {
            "good.md",
            ".hidden/broken.md",
            "excluded/large.md",
            "index.md",
            deep,
        }
        assert rows["index.md"].role == "navigation"
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
        note.write_text("---\ntitle: A\n---\n\nsecond")
        vault.index.reindex()
        changed = vault._index_mgr.observations.rows()["a.md"]
        assert changed.first_seen_at == first.first_seen_at
        assert changed.content_digest != first.content_digest
        assert changed.frontmatter_digest == first.frontmatter_digest
        note.write_bytes(b"---\ntitle: A\n---\n\xffbody")
        vault.index.reindex()
        invalid_body = vault._index_mgr.observations.rows()["a.md"]
        assert invalid_body.frontmatter_digest == first.frontmatter_digest
        assert invalid_body.content_digest != changed.content_digest
        note.write_text("---\ntitle: B\n---\nsecond")
        vault.index.reindex()
        assert (
            vault._index_mgr.observations.rows()["a.md"].frontmatter_digest
            != first.frontmatter_digest
        )
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


def test_newer_schema_is_refused_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    state = tmp_path / "state"
    ledger = tmp_path / "state.observations.json"
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
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


def test_warm_build_refreshes_observations(tmp_path: Path, monkeypatch) -> None:
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

        def no_rebuild(*_args, **_kwargs):
            raise AssertionError("warm build must reuse FTS")

        monkeypatch.setattr(vault._index_mgr, "build_index", no_rebuild)
        vault.index.build_index_async().result(timeout=5)
        assert (
            vault._index_mgr.observations.rows()["a.md"].content_digest
            != first.content_digest
        )
    finally:
        vault.close()


def test_missing_ledger_resets_history_without_writing_bundle(
    tmp_path: Path, caplog
) -> None:
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
        rebuilt = vault._index_mgr.observations.rows()["a.md"]
        assert rebuilt.first_seen_at > first.first_seen_at
        assert "observation_ledger_reset" not in caplog.text
        ledger_path.write_text("broken")
        vault.index.reindex()
        assert (
            vault._index_mgr.observations.rows()["a.md"].first_seen_at
            > rebuilt.first_seen_at
        )
        assert "observation_ledger_reset" in caplog.text
        assert note.read_text() == original
        assert {item.name for item in source.iterdir()} == {"a.md"}
        note.unlink()
        vault.index.reindex()
        ledger_path.unlink()
        vault._index_mgr.observations.sweep()
        assert '"rows": {}' in ledger_path.read_text()
        ledger_path.write_text("broken")
        vault._index_mgr.observations.sweep()
        assert '"rows": {}' in ledger_path.read_text()
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
    replaced = source / "replaced.md"
    replaced.write_text("present")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        gone.unlink()
        replaced.unlink()
        replaced.symlink_to("gone.md")
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
        assert rows["replaced.md"].presence == "present"
        assert "alias.md" not in rows
    finally:
        vault.close()


def test_unchanged_ledger_needs_no_replace(tmp_path: Path, monkeypatch) -> None:
    import json

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        path = tmp_path / "state.observations.json"
        data = json.loads(path.read_text())
        data["rows"]["a.md"].update(
            first_seen_at="2000-01-01T00:00:00+00:00",
            last_change_observed_at="2000-01-02T00:00:00-07:00",
        )
        path.write_text(json.dumps(data))

        def no_save(_rows):
            raise AssertionError("unchanged ledger was rewritten")

        monkeypatch.setattr(vault._index_mgr.observations, "_save", no_save)
        vault.index.build_index()
        assert (
            vault._index_mgr.observations.rows()["a.md"].first_seen_at
            == "2000-01-01T00:00:00+00:00"
        )
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "../bad.md"),
        ("path", ""),
        ("first_seen_at", None),
        ("first_seen_at", "nonsense"),
        ("first_seen_at", "2020-01-01T00:00:00"),
        ("last_change_observed_at", "2020-01-01T00:00:00+99:00"),
        ("removed_observed_at", "nonsense"),
        ("content_digest", None),
        ("content_digest", "a" * 63),
        ("frontmatter_digest", "A" * 64),
        ("frontmatter_digest", []),
        ("role", "invented"),
        ("origin", "invented"),
        ("presence", "invented"),
        ("presence", "removed"),
        ("removed_observed_at", "2020-01-01T00:00:00+00:00"),
    ],
)
def test_invalid_evidence_resets_entire_ledger(tmp_path: Path, field, value) -> None:
    import json

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("a")
    (source / "b.md").write_text("b")
    path = tmp_path / "state.observations.json"
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        data = json.loads(path.read_text())
        data["rows"]["b.md"]["first_seen_at"] = "2000-01-01T00:00:00+00:00"
        row = data["rows"]["a.md"]
        row[field] = value
        if field == "removed_observed_at" and value != "2020-01-01T00:00:00+00:00":
            row["presence"] = "removed"
        if field == "path":
            data["rows"][value] = data["rows"].pop("a.md")
        path.write_text(json.dumps(data))
        vault._index_mgr.observations.sweep()
        rows = vault._index_mgr.observations.rows()
        assert set(rows) == {"a.md", "b.md"}
        assert rows["b.md"].first_seen_at != "2000-01-01T00:00:00+00:00"
    finally:
        vault.close()
