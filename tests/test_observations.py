"""Durable observation sweeps cover the inventory rather than search scope."""

from pathlib import Path

import pytest

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


def test_frontmatter_digest_ignores_body_edits(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("---\ntitle: A\n---\nbody")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations.rows()["a.md"].frontmatter_digest
        note.write_text("---\ntitle: A\n---\n\nchanged body")
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["a.md"].frontmatter_digest == first
        note.write_text("---\ntitle: B\n---\nbody")
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["a.md"].frontmatter_digest != first
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


@pytest.mark.parametrize(
    "raw",
    [
        b"---\ntitle: A\n---",
        b"---\ntitle: [\n---\nbody",
        "---\ntitle: A\n---\u00a0\nbody".encode(),
        b"---\ntitle: A\n" + b"-" * 65536,
        b"---\n" + b"key: value\n" * 30000 + b"---\nbody",
        b"---\ntitle: A\nunterminated",
        b"plain note",
        b"\xffbad",
        b"\xef\xbb\xbf---\r\ntitle: A\r\n---\r\nbody",
        b'{\n"a": 1\n}\n\nbody',
    ],
)
def test_digest_uses_parser_boundary_and_full_content(raw: bytes) -> None:
    import hashlib
    import io

    from markdown_vault_mcp.bundle_inventory import _observation_digests
    from markdown_vault_mcp.scanner import strip_frontmatter_block
    from markdown_vault_mcp.utils.text import decode_utf8

    class Limited(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(size)

    digest, header = _observation_digests(Limited(raw))
    assert digest == hashlib.sha256(raw).hexdigest()
    try:
        text = decode_utf8(raw)
    except UnicodeDecodeError:
        expected = None
    else:
        body = strip_frontmatter_block(text)
        block = text[: len(text) - len(body)].strip() if body != text else None
        expected = hashlib.sha256(block.encode()).hexdigest() if block else None
    assert header == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "../bad.md"),
        ("path", "/bad.md"),
        ("path", "bad\\name.md"),
        ("path", ""),
        ("path", "bad\x00.md"),
        ("path", "a//b.md"),
        ("first_seen_at", "nonsense"),
        ("first_seen_at", "2020-01-01T00:00:00"),
        ("last_change_observed_at", "2020-01-01T00:00:00+99:00"),
        ("removed_observed_at", "nonsense"),
        ("removed_observed_at", "2020-01-01"),
        ("content_digest", None),
        ("content_digest", "a" * 63),
        ("content_digest", "G" * 64),
        ("frontmatter_digest", "A" * 64),
        ("frontmatter_digest", []),
        ("role", "invented"),
        ("role", []),
        ("origin", "invented"),
        ("origin", []),
        ("presence", "invented"),
        ("presence", "removed"),
        ("presence", []),
        ("removed_observed_at", "2020-01-01T00:00:00+00:00"),
    ],
)
def test_invalid_evidence_resets_entire_ledger(
    tmp_path: Path, caplog, field, value
) -> None:
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
        assert "observation_ledger_reset" in caplog.text
    finally:
        vault.close()


def test_valid_aware_evidence_is_preserved_without_write(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("a")
    path = tmp_path / "state.observations.json"
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        data = json.loads(path.read_text())
        row = data["rows"]["a.md"]
        row.update(
            first_seen_at="2000-01-01T00:00:00Z",
            last_change_observed_at="2000-01-02T00:00:00-07:00",
            origin="server_write",
        )
        path.write_text(json.dumps(data))
        ledger = vault._index_mgr.observations
        monkeypatch.setattr(
            ledger, "_save", lambda _: pytest.fail("unchanged ledger saved")
        )
        ledger.sweep()
        assert ledger.rows()["a.md"].origin == "server_write"
        assert ledger.rows()["a.md"].first_seen_at == "2000-01-01T00:00:00Z"
    finally:
        vault.close()


def test_missing_ledger_is_quiet_and_corrupt_ledger_warns(
    tmp_path: Path, caplog
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, InspectionPolicy
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    source = tmp_path / "vault"
    source.mkdir()
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(
        path, BundleInspector(source, (), frozenset(), 0, InspectionPolicy())
    )
    ledger.sweep()
    assert "observation_ledger_reset" not in caplog.text
    path.write_text("not json")
    ledger.sweep()
    assert "observation_ledger_reset" in caplog.text
