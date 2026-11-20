"""Durable observation sweeps cover the inventory rather than search scope."""

from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.git import GitRepoPair


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


def test_forced_build_preserves_marker_until_observation_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    settings = VaultSettings(index_path=tmp_path / "index.sqlite")
    vault = Vault(source_dir=source, settings=settings)
    try:
        vault.index.build_index()
        path = vault._index_mgr.observations._path
        path.write_text('{"version": 999, "rows": {}}')
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.index.build_index(force=True)
        assert vault._fts.is_build_completed()
        path.unlink()
        vault.close()
        vault = Vault(source_dir=source, settings=settings)
        vault.index.build_index()
        assert vault.index.is_queryable()

        def fail_scan(*_args, **_kwargs):
            assert not vault._fts.is_build_completed()
            raise OSError("scan failed")

        monkeypatch.setattr(vault._index_mgr, "_scan_and_index", fail_scan)
        with pytest.raises(OSError, match="scan failed"):
            vault.index.build_index(force=True)
        assert not vault._fts.is_build_completed()
    finally:
        vault.close()


def test_direct_and_raw_build_sweep_once(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.indexing import BuildIndex

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source)
    ledger = vault._index_mgr.observations
    original = ledger.sweep
    calls = []

    def sweep():
        calls.append(True)
        original()

    monkeypatch.setattr(ledger, "sweep", sweep)
    try:
        vault._index_mgr.build_index()
        assert len(calls) == 1
        vault._coordinator.writer.submit(BuildIndex()).result(timeout=5)
        assert len(calls) == 2
    finally:
        vault.close()


@pytest.mark.parametrize("route", ["direct", "git_sync", "webhook", "tick"])
def test_successful_noop_pull_observes_external_edit(
    tmp_path: Path, git_repo_pair, monkeypatch, route: str
) -> None:
    import asyncio
    import hashlib

    from markdown_vault_mcp._tools.git import _run_pull_leg
    from markdown_vault_mcp._webhooks import _process_push
    from markdown_vault_mcp.git import GitWriteStrategy

    source = git_repo_pair.local_path
    note = source / "README.md"
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        assert vault.reconcile_index_with_head(source="test") == "reindexed"
        ledger = vault._index_mgr.observations
        original = ledger.sweep
        calls = []

        def sweep():
            calls.append(True)
            original()

        monkeypatch.setattr(ledger, "sweep", sweep)
        note.write_text("external edit")
        if route == "direct":
            assert vault.force_pull().applied
        elif route == "git_sync":
            assert asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))["applied"]
        elif route == "webhook":
            assert (
                asyncio.run(_process_push(vault, "test", "delivery")).status_code == 200
            )
        else:
            strategy._pull_repo_path = source
            strategy._on_tick = vault._reconcile_on_tick
            monkeypatch.setattr(strategy._pull_stop, "wait", lambda **_kwargs: True)
            strategy._pull_loop()
        assert (
            ledger.rows()["README.md"].content_digest
            == hashlib.sha256(b"external edit").hexdigest()
        )
        assert len(calls) == (2 if route == "webhook" else 1)
    finally:
        vault.close()


def test_pull_reconcile_outcomes_and_failure(
    tmp_path: Path, git_repo_pair, monkeypatch
) -> None:
    from concurrent.futures import Future

    from markdown_vault_mcp.git import GitWriteStrategy
    from tests.fixtures.git import _run_git

    source = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        assert vault.reconcile_index_with_head(source="test") == "reindexed"
        ledger = vault._index_mgr.observations
        original = ledger.sweep
        calls = []

        def sweep():
            calls.append(True)
            original()

        monkeypatch.setattr(ledger, "sweep", sweep)
        (source / "new.md").write_text("changed head")
        _run_git(source, "add", "new.md")
        _run_git(source, "commit", "-m", "external")
        assert vault.reconcile_index_with_head(source="test") == "reindexed"
        assert len(calls) == 1
        original_digest = ledger.rows()["new.md"].content_digest
        pending = Future()
        vault._head_reconciler._pending = pending
        (source / "new.md").write_text("after pending scan started")
        assert vault.reconcile_index_with_head(source="test") == "deferred"
        assert len(calls) == 2
        assert ledger.rows()["new.md"].content_digest != original_digest
        pending.set_result(None)
        monkeypatch.setattr(vault._head_reconciler, "reconcile", lambda **_: "failed")
        assert vault.reconcile_index_with_head(source="test") == "failed"
        assert len(calls) == 3

        def fail():
            raise PermissionError("ledger denied")

        monkeypatch.setattr(ledger, "sweep", fail)
        monkeypatch.setattr(vault._head_reconciler, "reconcile", lambda **_: "current")
        assert vault.reconcile_index_with_head(source="test") == "failed"
    finally:
        vault.close()


def test_post_pull_observation_failure_is_reported_and_webhook_retries(
    tmp_path: Path, git_repo_pair, monkeypatch, caplog
) -> None:
    import asyncio

    from markdown_vault_mcp._tools.git import _run_pull_leg
    from markdown_vault_mcp._webhooks import _process_push
    from markdown_vault_mcp.git import GitWriteStrategy, PullResult

    source = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        vault.reconcile_index_with_head(source="test")
        ledger = vault._index_mgr.observations
        original = ledger.sweep
        (source / "README.md").write_text("retry edit")

        def fail():
            raise PermissionError("ledger denied")

        monkeypatch.setattr(ledger, "sweep", fail)
        with pytest.raises(PermissionError, match="ledger denied"):
            vault.force_pull()
        assert "git_pull_observation_failed applied=True" in caplog.text
        assert asyncio.run(_run_pull_leg(strategy, vault, dry_run=True))["applied"]
        refused = PullResult(
            applied=False,
            fast_forward=False,
            commits_pulled=0,
            from_sha="a",
            to_sha="a",
            reason="fetch_failed",
        )
        with monkeypatch.context() as patch:
            patch.setattr(strategy, "force_pull", lambda: refused)
            assert vault.force_pull() is refused
        assert asyncio.run(_process_push(vault, "test", "one")).status_code == 503
        result = asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))
        assert result["applied"] and result["reindex_failed"]
        monkeypatch.setattr(ledger, "sweep", original)
        assert asyncio.run(_process_push(vault, "test", "two")).status_code == 200
        import hashlib

        assert (
            ledger.rows()["README.md"].content_digest
            == hashlib.sha256(b"retry edit").hexdigest()
        )
    finally:
        vault.close()


# Descriptor fixture and cleanup remain together to exercise real exhaustion.
def test_deep_resource_failure_blocks_only_its_subtree(  # noqa: PLR0915, RUF100
    tmp_path: Path, monkeypatch
) -> None:
    import os
    import sys

    source = tmp_path / "vault"
    source.mkdir()
    (source / "excluded").mkdir()
    removed = source / "removed.md"
    changed = source / "changed.md"
    removed.write_text("remove")
    changed.write_text("before")
    parents = [os.open(source / "excluded", os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for _ in range(510):
            os.mkdir("d", dir_fd=parents[-1])
            parents.append(
                os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=parents[-1])
            )
        leaf = os.open("leaf.md", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=parents[-1])
        os.write(leaf, b"deep")
        os.close(leaf)
        vault = Vault(
            source_dir=source, settings=VaultSettings(exclude_patterns=["excluded/**"])
        )
        try:
            ledger = vault._index_mgr.observations
            deep = "/".join(["excluded"] + ["d"] * 510 + ["leaf.md"])
            limit = sys.getrecursionlimit()
            try:
                sys.setrecursionlimit(5000)
                vault.index.build_index()
            finally:
                sys.setrecursionlimit(limit)
            first = ledger.rows()
            assert deep in first
            removed.unlink()
            changed.write_text("after")
            observed = []
            original = ledger._inspector.observe_files

            def capture(visit):
                blocked = original(visit)
                observed.append(blocked)
                return blocked

            monkeypatch.setattr(ledger._inspector, "observe_files", capture)
            vault.index.build_index(force=True)
            vault.index.reindex()
            rows = ledger.rows()
            assert len(observed) == 2
            assert all(blocked and "" not in blocked for blocked in observed)
            assert rows[deep] == first[deep]
            assert rows["removed.md"].presence == "removed"
            assert (
                rows["changed.md"].content_digest != first["changed.md"].content_digest
            )
        finally:
            vault.close()
    finally:
        os.unlink("leaf.md", dir_fd=parents[-1])
        for i in range(len(parents) - 1, 0, -1):
            os.close(parents[i])
            os.rmdir("d", dir_fd=parents[i - 1])
        os.close(parents[0])


@pytest.mark.parametrize("state_is_ancestor", [False, True])
def test_observation_refuses_protected_root_without_removals(
    tmp_path: Path, monkeypatch, state_is_ancestor: bool
) -> None:
    from dataclasses import replace

    import markdown_vault_mcp.bundle_inventory as inventory

    source = tmp_path / "vault"
    source.mkdir()
    note = source / "note.md"
    note.write_text("body")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        first = ledger.rows()["note.md"]
        note.unlink()
        inspector = ledger._inspector
        state = tmp_path if state_is_ancestor else source
        inspector._policy = replace(inspector._policy, state_paths=(state,))

        def refuse(*_args, **_kwargs):
            raise AssertionError("protected root opened")

        monkeypatch.setattr(inventory, "open_directory_nofollow", refuse)
        assert inspector.observe_files(lambda *_args: None) == {""}
        vault.index.reindex()
        assert ledger.rows()["note.md"] == first
    finally:
        vault.close()


# Keep the concurrent schedule and its cleanup visible together.
def test_shared_vault_ledgers_serialize_sweeps(  # noqa: PLR0915, RUF100
    tmp_path: Path, monkeypatch
) -> None:
    import threading

    source = tmp_path / "vault"
    source.mkdir()
    note = source / "note.md"
    note.write_text("initial")
    removed = source / "removed.md"
    removed.write_text("initial")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(state_dir, target_is_directory=True)
    index = state_dir / "index.db"
    a = Vault(
        source_dir=source,
        settings=VaultSettings(index_path=index, state_path=state_dir / "state.json"),
    )
    b = Vault(
        source_dir=source,
        settings=VaultSettings(index_path=index, state_path=alias / "state.json"),
    )
    release = threading.Event()
    threads = []
    try:
        ledger_a = a._index_mgr.observations
        ledger_b = b._index_mgr.observations
        ledger_a.sweep()
        first = ledger_a.rows()
        note.write_text("intermediate")
        ready = threading.Event()
        started = threading.Event()
        entered = threading.Event()
        errors = []
        save = ledger_a._save
        load = ledger_b._load_full

        def paused_save(rows, navigation):
            ready.set()
            if not release.wait(10):
                raise TimeoutError("first sweep not released")
            save(rows, navigation)

        def marked_load():
            entered.set()
            return load()

        def run(ledger, started_event=None):
            try:
                if started_event is not None:
                    started_event.set()
                ledger.sweep()
            except Exception as exc:
                errors.append(exc)

        monkeypatch.setattr(ledger_a, "_save", paused_save)
        monkeypatch.setattr(ledger_b, "_load_full", marked_load)
        threads.append(threading.Thread(target=run, args=(ledger_a,)))
        threads[-1].start()
        assert ready.wait(5)
        note.write_text("newest")
        removed.unlink()
        threads.append(threading.Thread(target=run, args=(ledger_b, started)))
        threads[-1].start()
        assert started.wait(5)
        assert not entered.wait(0.25)
        release.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
        assert not errors
        rows = ledger_b.rows()
        import hashlib

        assert rows["note.md"].content_digest == hashlib.sha256(b"newest").hexdigest()
        assert rows["note.md"].first_seen_at == first["note.md"].first_seen_at
        assert rows["removed.md"].presence == "removed"
        assert rows["removed.md"].first_seen_at == first["removed.md"].first_seen_at
    finally:
        release.set()
        for thread in threads:
            thread.join(10)
        b.close()
        a.close()


def test_pull_observes_when_search_reindex_fails_before_scan(
    tmp_path: Path, git_repo_pair, monkeypatch
) -> None:
    import asyncio

    from markdown_vault_mcp._tools.git import _run_pull_leg
    from markdown_vault_mcp.git import GitWriteStrategy
    from tests.fixtures.git import _run_git

    source = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        assert vault.reconcile_index_with_head(source="fixture") == "reindexed"
        other = tmp_path / "other"
        _run_git(tmp_path, "clone", str(git_repo_pair.remote_path), str(other))
        _run_git(other, "config", "user.email", "fixture@example.invalid")
        _run_git(other, "config", "user.name", "Fixture")
        (other / "incoming.md").write_text("arrived")
        _run_git(other, "add", "incoming.md")
        _run_git(other, "commit", "-m", "fixture")
        _run_git(other, "push")

        def fail_reindex():
            raise OSError("search writer unavailable")

        monkeypatch.setattr(vault._head_reconciler, "_reindex", fail_reindex)
        result = asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))
        assert result["applied"] and result["reindex_failed"]
        assert (source / "incoming.md").read_text() == "arrived"
        assert "incoming.md" in vault._index_mgr.observations.rows()
    finally:
        vault.close()


def test_failed_tick_observation_still_retries_push_and_reports_failure(
    tmp_path: Path, git_repo_pair, monkeypatch
) -> None:
    import asyncio

    from markdown_vault_mcp._tools.git import _run_pull_leg
    from markdown_vault_mcp.git import GitWriteStrategy

    source = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        monkeypatch.setattr(
            vault._head_reconciler, "reconcile", lambda **_kwargs: "current"
        )

        def fail_sweep():
            raise PermissionError("ledger denied")

        monkeypatch.setattr(vault._index_mgr.observations, "sweep", fail_sweep)
        strategy._pull_repo_path = source
        strategy._on_tick = vault._reconcile_on_tick
        strategy._enable_push = True
        monkeypatch.setattr(strategy, "sync_once", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(strategy._pull_stop, "wait", lambda **_kwargs: True)
        pushes = []
        monkeypatch.setattr(
            strategy._push_scheduler,
            "do_push_safe",
            lambda **kwargs: pushes.append(kwargs),
        )
        strategy._pull_loop()
        assert pushes == [{"retry": True}]
        assert vault.reconcile_index_with_head(source="test") == "failed"
        result = asyncio.run(_run_pull_leg(strategy, vault, dry_run=False))
        assert result["applied"] and result["reindex_failed"]
    finally:
        vault.close()


def test_role_reclassification_preserves_byte_change_time(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "rules.md").write_text("same bytes")
    state = tmp_path / "state"
    first_vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        first_vault.index.build_index()
        before = first_vault._index_mgr.observations.rows()["rules.md"]
    finally:
        first_vault.close()
    second_vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=state, conventions_file="rules.md"),
    )
    try:
        second_vault.index.build_index()
        after = second_vault._index_mgr.observations.rows()["rules.md"]
        assert before.role == "concept_candidate"
        assert after.role == "support"
        assert after.content_digest == before.content_digest
        assert after.frontmatter_digest == before.frontmatter_digest
        assert after.presence == before.presence
        assert after.first_seen_at == before.first_seen_at
        assert after.last_change_observed_at == before.last_change_observed_at
    finally:
        second_vault.close()


def test_single_path_observation_refuses_internal_and_nonfiles(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".git").mkdir()
    (source / "folder.md").mkdir()
    vault = Vault(source_dir=source)
    try:
        inspector = vault._index_mgr.observations._inspector
        assert inspector.observe_path("a.txt", lambda *_args: None) == "blocked"
        assert inspector.observe_path(".git/a.md", lambda *_args: None) == "blocked"
        assert inspector.observe_path("folder.md", lambda *_args: None) == "blocked"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "data", ['{"version": "1", "rows": {}}', '{"version": 1, "rows": []}']
)
def test_malformed_ledger_rebuilds_history(tmp_path: Path, data: str) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    state = tmp_path / "state"
    ledger_path = tmp_path / "state.observations.json"
    ledger_path.write_text(data)
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
        assert vault._index_mgr.observations.rows()["a.md"].presence == "present"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "navigation",
    [
        "not a mapping",
        {"generated": [], "reported": {}},
        {"generated": {"index.md": "invalid"}, "reported": {}},
        {"generated": {}, "reported": {"index.md": "not a list"}},
    ],
)
def test_malformed_navigation_history_resets_without_losing_rows(
    tmp_path: Path, navigation: object
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    before = ledger.rows()
    data = json.loads(path.read_text())
    data["navigation"] = navigation
    path.write_text(json.dumps(data))
    assert ledger.navigation_stats() == {"count": 0, "examples": []}
    assert ledger.rows() == before


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_ledger_rows_survive_version_three_upgrade(
    tmp_path: Path, version: int
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    note.unlink()
    ledger.sweep()
    before = ledger.rows()
    data = json.loads(path.read_text())
    data["version"] = version
    data.pop("navigation", None)
    path.write_text(json.dumps(data))
    assert ledger.rows() == before
    ledger.record_navigation("index.md", b"# Index\n")
    assert ledger.rows() == before
    assert json.loads(path.read_text())["version"] == 3


@pytest.mark.parametrize(
    "version,missing,warning",
    [(1, True, False), (2, True, False), (3, True, True), (3, False, True)],
)
def test_navigation_absence_respects_ledger_version(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    version: int,
    missing: bool,
    warning: bool,
) -> None:
    import json
    import logging

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    before = ledger.rows()
    data = json.loads(path.read_text())
    data["version"] = version
    if missing:
        del data["navigation"]
    else:
        data["navigation"] = None
    path.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING):
        assert ledger.rows() == before
    assert (
        any("okf_navigation_history_reset" in r.message for r in caplog.records)
        is warning
    )


@pytest.mark.parametrize("operation", ["refresh_many", "sweep"])
@pytest.mark.parametrize("future_version", [False, True])
def test_reconciliation_uses_one_validated_ledger_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    future_version: bool,
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Before\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    ledger.record_navigation("index.md", b"# Generated\n")
    ledger.observe_navigation("index.md", b"# Edited\n", b"# Generated\n")
    original = json.loads(path.read_text())
    if future_version:
        original["version"] = 4
        path.write_text(json.dumps(original))
    note.write_text("# After\n")
    original_read = Path.read_text
    reads = 0

    def count_read(target: Path, *args: object, **kwargs: object) -> str:
        nonlocal reads
        if target == path:
            reads += 1
        return original_read(target, *args, **kwargs)

    def reconcile() -> None:
        if operation == "sweep":
            ledger.sweep()
        else:
            ledger.refresh_many({"note.md"})

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", count_read)
        if future_version:
            with pytest.raises(StoreUnsupportedSchemaError):
                reconcile()
        else:
            reconcile()
    assert reads == 1
    after = json.loads(path.read_text())
    assert after["navigation"] == original["navigation"]
    if future_version:
        assert after == original
    else:
        assert (
            after["rows"]["note.md"]["content_digest"]
            != original["rows"]["note.md"]["content_digest"]
        )


def test_navigation_operations_skip_row_validation(tmp_path: Path, monkeypatch) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.indexing import observations

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = observations.ObservationLedger(
        path, BundleInspector(root, (), frozenset(), 0)
    )
    ledger.sweep()
    data = json.loads(path.read_text())
    row = data["rows"].pop("note.md")
    for number in range(500):
        name = f"note{number}.md"
        data["rows"][name] = {**row, "path": name}
    path.write_text(json.dumps(data))
    original_rows = ledger.rows()
    validations = 0
    saves = 0
    validate = observations._valid_row
    save = ledger._save

    def count_validation(*args):
        nonlocal validations
        validations += 1
        return validate(*args)

    def count_save(*args):
        nonlocal saves
        saves += 1
        return save(*args)

    with monkeypatch.context() as patch:
        patch.setattr(observations, "_valid_row", count_validation)
        patch.setattr(ledger, "_save", count_save)
        for number in range(20):
            name = f"folder{number}/index.md"
            ledger.observe_navigation(name, None, b"# Generated\n")
            ledger.record_navigation(name, b"# Generated\n")
        assert ledger.navigation_stats()["count"] == 0
    assert validations == 0
    assert saves == 20
    assert ledger.rows() == original_rows


@pytest.mark.parametrize("operation", ["refresh_many", "sweep"])
def test_bad_observation_row_preserves_navigation_history(
    tmp_path: Path, operation: str
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.hashing import compute_etag
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    inspector = BundleInspector(root, (), frozenset(), 0)
    ledger = ObservationLedger(path, inspector)
    ledger.sweep()
    ledger.record_navigation("index.md", b"# Before\n")
    ledger.observe_navigation("index.md", b"# Edit\n", b"# Before\n")
    data = json.loads(path.read_text())
    data["rows"]["note.md"]["content_digest"] = "bad"
    path.write_text(json.dumps(data))
    assert ledger.navigation_stats()["count"] == 1
    ledger.record_navigation("index.md", b"# After\n")
    if operation == "sweep":
        ledger.sweep()
    else:
        ledger.refresh_many({"note.md"})
    restarted = ObservationLedger(path, inspector)
    state = json.loads(path.read_text())
    assert state["navigation"]["generated"]["index.md"] == compute_etag(b"# After\n")
    assert restarted.rows()["note.md"].content_digest != "bad"
    restarted.observe_navigation("index.md", b"# Edit\n", b"# After\n")
    assert restarted.navigation_stats()["count"] == 1
    restarted.observe_navigation("index.md", b"# Another edit\n", b"# After\n")
    restarted.observe_navigation("index.md", b"# Another edit\n", b"# After\n")
    assert restarted.navigation_stats()["count"] == 2


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_optional_row_defaults_survive_navigation_save(
    tmp_path: Path, version: int
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    data = json.loads(path.read_text())
    data["version"] = version
    data.pop("navigation")
    for key in ("presence", "removed_observed_at", "origin", "moved_from", "moved_to"):
        data["rows"]["note.md"].pop(key)
    path.write_text(json.dumps(data))
    before = ledger.rows()
    ledger.record_navigation("index.md", b"# Index\n")
    assert json.loads(path.read_text())["version"] == 3
    assert ledger.rows() == before
    ledger.sweep()
    assert ledger.rows() == before


@pytest.mark.parametrize("version", [0, -1, True, "3", 4])
def test_invalid_ledger_envelope_resets_or_refuses(
    tmp_path: Path, version: object
) -> None:
    import json

    from markdown_vault_mcp.bundle_inventory import BundleInspector
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
    from markdown_vault_mcp.indexing.observations import ObservationLedger

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    path = tmp_path / "ledger.json"
    ledger = ObservationLedger(path, BundleInspector(root, (), frozenset(), 0))
    ledger.sweep()
    ledger.record_navigation("index.md", b"# Index\n")
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    if type(version) is int and version == 4:
        with pytest.raises(StoreUnsupportedSchemaError):
            ledger.observe_navigation("missing.md", None, b"# Index\n")
        assert path.read_bytes() == original
    else:
        assert ledger.rows() == {}
        assert ledger.navigation_stats() == {"count": 0, "examples": []}
        ledger.sweep()
        result = json.loads(path.read_text())
        assert result["navigation"] == {"generated": {}, "reported": {}}


def test_automatic_pull_sweeps_only_after_applied_result(
    tmp_path: Path,
    git_repo_pair: GitRepoPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsuccessful automatic pull does not sweep observations."""

    from markdown_vault_mcp.git import GitWriteStrategy, PullResult

    source = git_repo_pair.local_path
    strategy = GitWriteStrategy(
        token=None, push_delay_s=0, git_lfs=False, repo_path=source
    )
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=tmp_path / "state"),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        original_digest = ledger.rows()["README.md"].content_digest
        (source / "README.md").write_text("external edit")
        result = PullResult(
            applied=False,
            fast_forward=False,
            commits_pulled=0,
            from_sha="a",
            to_sha="a",
            reason="fetch_failed",
        )
        monkeypatch.setattr(strategy, "automatic_pull", lambda: result)
        assert vault._automatic_pull() is result
        assert ledger.rows()["README.md"].content_digest == original_digest
    finally:
        vault.close()
        strategy.close()
