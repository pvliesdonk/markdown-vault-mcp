"""External changes converge designated OKF indexes on reindex."""

import shutil
from pathlib import Path

import pytest

from markdown_vault_mcp._okf_index_checkpoint import IndexCheckpoint
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.designated_index import designated_vault
from tests.fixtures.git import _git_commit, _git_head


@pytest.mark.parametrize(
    "change", ["delete", "move", "metadata", "metadata_after_sweep"]
)
def test_external_changes_update_listing(tmp_path: Path, change: str) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = root / "concept.md"
    concept.write_text("---\ndescription: Before\n---\n# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        if change == "metadata_after_sweep":
            vault.index.reindex()
        if change == "delete":
            concept.unlink()
        elif change == "move":
            concept.rename(root / "moved.md")
        else:
            concept.write_text(
                "---\ndescription: After\n---\n# Concept\n", encoding="utf-8"
            )
        if change == "metadata_after_sweep":
            assert vault._index_mgr.observations is not None
            vault._index_mgr.observations.sweep()
        vault.index.reindex()
        listing = index.read_text(encoding="utf-8")
        if change == "delete":
            assert "concept.md" not in listing
        elif change == "move":
            assert "concept.md" not in listing
            assert "moved.md" in listing
        else:
            assert "After" in listing
            assert "Before" not in listing
    finally:
        vault.close()


def test_identical_reindex_does_not_write_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = tmp_path / "concept.md"
    concept.write_text("# Concept\nBefore\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        mtime = index.stat().st_mtime_ns
        writes: list[str] = []
        original = vault._doc_mgr.write

        def record(path: str, *args: object, **kwargs: object) -> object:
            writes.append(path)
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "write", record)
        concept.write_text("# Concept\nAfter\n", encoding="utf-8")
        vault.index.reindex()
        assert writes == []
        assert index.stat().st_mtime_ns == mtime
    finally:
        vault.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reindex_completion_includes_convergence(
    tmp_path: Path, asynchronous: bool
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        if asynchronous:
            vault.index.reindex_async().result(timeout=10)
        else:
            with vault.pause_writes():
                vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


@pytest.mark.parametrize("baseline", [False, True])
def test_arrival_creates_designated_anchor_after_baseline(
    tmp_path: Path, baseline: bool
) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    folder = tmp_path / "projects" / "one" / "deep" / "nested"
    folder.mkdir(parents=True)
    vault = designated_vault(tmp_path, (".", "projects/*"))
    try:
        vault.index.build_index()
        if baseline:
            vault.index.reindex()
        project_index = tmp_path / "projects" / "one" / "index.md"
        assert not path_exists(project_index)
        (folder / "concept.md").write_text("# Concept\n")
        vault.index.reindex()
        assert "concept.md" in project_index.read_text()
        assert not path_exists(folder / "index.md")
    finally:
        vault.close()


def test_persistent_restart_preserves_index_and_creates_offline_arrival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    for name in ("one", "two"):
        (root / "projects" / name).mkdir(parents=True)
    existing = root / "projects/one/index.md"
    existing.write_text("# Original\n")
    (root / "projects/one/note.md").write_text("# One\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, ("projects/*",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
    finally:
        vault.close()
    edited = "# Hand-edited\n"
    existing.write_text(edited)
    (root / "projects/two/offline.md").write_text("# Offline\n")
    vault = designated_vault(root, ("projects/*",), index_path=db)
    try:
        vault.index.build_index()
        calls: list[str] = []
        original = vault._okf_migrate.generate_index

        def record(**kwargs: object) -> object:
            calls.append(str(kwargs["folder"]))
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", record)
        vault.index.reindex()
        assert calls == ["projects/two"]
        assert existing.read_text() == edited
        assert path_exists(root / "projects/two/index.md")
    finally:
        vault.close()


def test_noop_does_not_canonicalize_each_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp._okf_convention as convention

    for n in range(20):
        (tmp_path / f"note{n}.md").write_text(f"# Note {n}\n")
    vault = designated_vault(tmp_path, ("**",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        calls: list[str] = []
        original = convention.canonical_path

        def count(path: str, source_dir: Path | None) -> str:
            calls.append(path)
            return original(path, source_dir)

        monkeypatch.setattr(convention, "canonical_path", count)
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


@pytest.mark.parametrize("boundary", [False, True])
def test_external_folder_removal_refreshes_designated_ancestor(
    tmp_path: Path, boundary: bool
) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    folder = tmp_path / "projects/one"
    folder.mkdir(parents=True)
    if boundary:
        (folder.parent / "index.md").write_text("# Authored boundary\n")
    (folder / "index.md").write_text("# One\n")
    (folder / "note.md").write_text("# Note\n")
    vault = designated_vault(tmp_path, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.index.reindex()
        root_index = tmp_path / "index.md"
        if boundary:
            root_index.write_text('---\nokf_version: "0.2"\n---\n# Hand edited\n')
        else:
            assert "projects/one/index.md" in root_index.read_text()
        shutil.rmtree(folder)
        vault.index.reindex()
        if boundary:
            assert "Hand edited" in root_index.read_text()
            assert (folder.parent / "index.md").read_text() == "# Authored boundary\n"
        else:
            assert "projects/one/index.md" not in root_index.read_text()
    finally:
        vault.close()


def test_log_only_does_not_schedule_index_convergence(tmp_path: Path) -> None:
    capabilities = ContentConfig(
        okf_mode="on", okf_index=False, okf_log="writes"
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=capabilities
        ),
    )
    try:
        assert vault._index_facet._convergence_executor is None
    finally:
        vault.close()


def test_checkpoint_corruption_scope_and_future_version(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint = IndexCheckpoint(path, "first")
    concepts = {"note.md": "a" * 64}
    checkpoint.save(concepts)
    assert IndexCheckpoint(path, "first").load() == concepts
    assert IndexCheckpoint(path, "second").load() is None
    path.write_text("not json")
    assert IndexCheckpoint(path, "first").load() is None
    path.write_text('{"version": 2}')
    with pytest.raises(StoreUnsupportedSchemaError):
        IndexCheckpoint(path, "first").load()


def test_failed_checkpoint_save_keeps_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint = IndexCheckpoint(path, "scope")
    before = {"before.md": "a" * 64}
    checkpoint.save(before)
    original = path.read_bytes()

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("disk failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        checkpoint.save({"after.md": "b" * 64})
    assert checkpoint.load() == before
    assert path.read_bytes() == original


def test_search_admission_policy_change_reconciles_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "note.md").write_text("---\ntitle: Custom title\n---\n# Body\n")
    db = tmp_path / "index.sqlite"
    capabilities = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities

    def open_vault(required: list[str] | None) -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                read_only=False,
                okf_mode="on",
                okf_capabilities=capabilities,
                index_path=db,
                required_frontmatter=required,
            ),
        )

    vault = open_vault(None)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "Custom title" in index.read_text()
    finally:
        vault.close()
    vault = open_vault(["missing"])
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "Custom title" not in index.read_text()
        assert "note.md" in index.read_text()
        calls: list[str] = []
        original = vault._okf_migrate.generate_index

        def record(**kwargs: object) -> object:
            calls.append(str(kwargs["folder"]))
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", record)
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


def test_future_checkpoint_leaves_index_untouched(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    original = '---\nokf_version: "0.2"\n---\n# Authored\n'
    index.write_text(original)
    (root / "note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",), index_path=tmp_path / "index.sqlite")
    try:
        assert vault._okf_checkpoint_path is not None
        vault._okf_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        vault._okf_checkpoint_path.write_text('{"version": 2}')
        vault.index.build_index()
        vault.index.reindex()
        assert index.read_text() == original
        assert vault._okf_checkpoint_path.read_text() == '{"version": 2}'
    finally:
        vault.close()


def test_failed_refresh_keeps_persistent_checkpoint_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",), index_path=tmp_path / "index.sqlite")
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault._okf_checkpoint_path is not None
        before = vault._okf_checkpoint_path.read_bytes()
        original = vault._okf_migrate.generate_index

        def fail(**_kwargs: object) -> object:
            raise OSError("temporary failure")

        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail)
        (root / "new.md").write_text("# New\n")
        vault.index.reindex()
        assert vault._okf_checkpoint_path.read_bytes() == before
        monkeypatch.setattr(vault._okf_migrate, "generate_index", original)
        vault.index.reindex()
        assert "new.md" in (root / "index.md").read_text()
        assert vault._okf_checkpoint_path.read_bytes() != before
    finally:
        vault.close()


def test_reserved_alias_does_not_mask_other_observed_arrivals(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    alias = root / "alias.md"
    alias.write_text("# Previously a concept\n")
    db = tmp_path / "index.sqlite"
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert vault._okf_checkpoint_path is not None
        checkpoint = vault._okf_checkpoint_path
    finally:
        vault.close()
    checkpoint.unlink()
    alias.unlink()
    alias.symlink_to("index.md")
    (root / "new.md").write_text("# New\n")
    vault = designated_vault(root, (".",), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "new.md" in index.read_text()
    finally:
        vault.close()


def test_close_waits_for_async_convergence(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(tmp_path, (".",))
    try:
        vault.index.build_index()
        (tmp_path / "external.md").write_text("# External\n")
        pending = vault.index.reindex_async()
        vault.close()
        pending.result(timeout=10)
        assert "external.md" in index.read_text()
    finally:
        vault.close()


def test_head_reconcile_runs_convergence(git_repo: Path) -> None:
    index = git_repo / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    strategy = GitWriteStrategy(
        enable_pull=False, enable_push=False, git_lfs=False, repo_path=git_repo
    )
    vault = Vault(
        source_dir=git_repo,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
        git_strategy=strategy,
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        vault.mark_index_reconciled(vault.git_head())
        _git_commit(git_repo, "external.md", "# External\n", "external edit")
        assert vault.reconcile_index_with_head(source="test") == "reindexed"
        assert "external.md" in index.read_text()
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["upsert", "parse"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("arrival", [False, True])
def test_search_retry_reconciles_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    restart: bool,
    arrival: bool,
) -> None:
    import markdown_vault_mcp.managers.index as index_module

    root = tmp_path / "vault"
    for name in ("one", "two"):
        (root / name).mkdir(parents=True)
        (root / name / "index.md").write_text(f"# {name}\n")
    note = root / "one/note.md"
    if not arrival:
        note.write_text("---\ntitle: Before\n---\n# Body\n")
    db = tmp_path / "index.sqlite" if restart else None
    vault = designated_vault(root, ("one", "two"), index_path=db)
    try:
        vault.index.build_index()
        vault.index.reindex()
        sibling = root / "two/index.md"
        sibling.write_text("# Hand edited\n")
        note.write_text("---\ntitle: After\n---\n# Body\n")
        if failure == "upsert":
            original_upsert = vault._index_mgr._fts.upsert_note

            def fail_upsert(parsed: object) -> object:
                if parsed.path == "one/note.md":  # type: ignore[attr-defined]
                    raise OSError("transient")
                return original_upsert(parsed)  # type: ignore[arg-type]

            monkeypatch.setattr(vault._index_mgr._fts, "upsert_note", fail_upsert)
        else:
            original_parse = index_module.parse_note_categorized

            def fail_parse(*args: object, **kwargs: object) -> object:
                if kwargs.get("rel_path") == "one/note.md":
                    return None
                return original_parse(*args, **kwargs)  # type: ignore[arg-type]

            monkeypatch.setattr(index_module, "parse_note_categorized", fail_parse)
        vault.index.reindex()
        listing = (root / "one/index.md").read_text()
        assert "After" not in listing
        assert ("note.md" if arrival else "Before") in listing
        monkeypatch.undo()
        if restart:
            vault.close()
            vault = designated_vault(root, ("one", "two"), index_path=db)
            vault.index.build_index()
        calls: list[str] = []
        original_generate = vault._okf_migrate.generate_index

        def record(**kwargs: object) -> object:
            calls.append(str(kwargs["folder"]))
            return original_generate(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", record)
        vault.index.reindex()
        assert "After" in (root / "one/index.md").read_text()
        assert calls == ["one"]
        assert sibling.read_text() == "# Hand edited\n"
        calls.clear()
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


@pytest.mark.parametrize("change", ["add", "edit", "delete", "move"])
@pytest.mark.parametrize("deep", [False, True])
def test_changes_below_preserved_boundary_do_not_refresh_parent(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, change: str, deep: bool
) -> None:
    root_index = git_repo / "index.md"
    root_index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    authored = git_repo / "authored"
    folder = authored / "deep" if deep else authored
    folder.mkdir(parents=True)
    boundary = authored / "index.md"
    boundary.write_text("# Authored\n")
    note = folder / "note.md"
    note.write_text("# Before\n")
    strategy = GitWriteStrategy(
        enable_pull=False, enable_push=False, git_lfs=False, repo_path=git_repo
    )
    vault = Vault(
        source_dir=git_repo,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=ContentConfig(
                okf_mode="on", okf_index=True, okf_folders=(".",)
            ).okf_capabilities,
            index_path=git_repo.parent / "index.sqlite",
        ),
        git_strategy=strategy,
        on_write=strategy,
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        root_index.write_text('---\nokf_version: "0.2"\n---\n# Hand edited\n')
        before = root_index.read_bytes(), boundary.read_bytes()
        assert vault._okf_checkpoint_path is not None
        checkpoint = vault._okf_checkpoint_path.read_bytes()
        assert vault._write_callback.drain()
        head = _git_head(git_repo)
        calls: list[str] = []
        original = vault._okf_migrate.generate_index

        def record(**kwargs: object) -> object:
            calls.append(str(kwargs["folder"]))
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", record)
        if change == "add":
            (folder / "new.md").write_text("# New\n")
        elif change == "edit":
            note.write_text("# After\n")
        elif change == "delete":
            note.unlink()
        else:
            note.rename(folder / "moved.md")
        vault.index.reindex()
        assert (root_index.read_bytes(), boundary.read_bytes()) == before
        assert calls == []
        assert vault._okf_checkpoint_path.read_bytes() != checkpoint
        assert vault._write_callback.drain()
        assert _git_head(git_repo) == head
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


def test_mixed_changes_respect_preserved_boundary(tmp_path: Path) -> None:
    root_index = tmp_path / "index.md"
    root_index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    authored = tmp_path / "authored"
    authored.mkdir()
    boundary = authored / "index.md"
    boundary.write_text("# Authored\n")
    note = authored / "note.md"
    note.write_text("# Before\n")
    vault = designated_vault(tmp_path, (".", "authored/proj"))
    try:
        vault.index.build_index()
        vault.index.reindex()
        note.write_text("# After\n")
        (tmp_path / "new.md").write_text("# New\n")
        vault.index.reindex()
        assert "new.md" in root_index.read_text()
        assert boundary.read_text() == "# Authored\n"
        boundary.write_text("# Edited boundary\n")
        (tmp_path / "new.md").write_text("# Changed root note\n")
        vault.index.reindex()
        assert "Changed root note" in root_index.read_text()
        assert boundary.read_text() == "# Edited boundary\n"
        root_index.write_text('---\nokf_version: "0.2"\n---\n# Hand edited\n')
        project = authored / "proj"
        project.mkdir()
        (project / "new.md").write_text("# Project\n")
        vault.index.reindex()
        assert "new.md" in (project / "index.md").read_text()
        assert "Hand edited" in root_index.read_text()
        assert boundary.read_text() == "# Edited boundary\n"
    finally:
        vault.close()


def test_excluded_and_dot_arrivals_create_no_index(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    for name in ("private", ".hidden"):
        (tmp_path / name).mkdir()
    vault = designated_vault(tmp_path, ("**",), exclude=("private/**",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        for name in ("private", ".hidden"):
            (tmp_path / name / "note.md").write_text("# Note\n")
        vault.index.reindex()
        for name in ("private", ".hidden"):
            assert not path_exists(tmp_path / name / "index.md")
    finally:
        vault.close()


def test_title_policy_restart_refreshes_only_changed_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    for name, label in (("one", "New label"), ("two", "Old label")):
        (root / name).mkdir(parents=True)
        (root / name / "index.md").write_text("# Index\n")
        (root / name / "note.md").write_text(
            f"---\ntitle: Old label\nlabel: {label}\n---\n# Body\n"
        )
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("one", "two")
    ).okf_capabilities

    def open_vault(field: str) -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                read_only=False,
                okf_mode="on",
                okf_capabilities=caps,
                index_path=tmp_path / "index.sqlite",
                title_field=field,
            ),
        )

    vault = open_vault("title")
    try:
        vault.index.build_index()
        vault.index.reindex()
    finally:
        vault.close()
    sibling = root / "two/index.md"
    sibling.write_text("# Hand edited\n")
    vault = open_vault("label")
    try:
        vault.index.build_index()
        calls: list[str] = []
        original = vault._okf_migrate.generate_index

        def record(**kwargs: object) -> object:
            calls.append(str(kwargs["folder"]))
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", record)
        vault.index.reindex()
        assert "New label" in (root / "one/index.md").read_text()
        assert sibling.read_text() == "# Hand edited\n"
        assert calls == ["one"]
        calls.clear()
        vault.index.reindex()
        assert calls == []
    finally:
        vault.close()


def test_cancelled_async_reindex_still_converges(tmp_path: Path) -> None:
    from threading import Event

    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "before.md").write_text("# Before\n")
    vault = designated_vault(tmp_path, (".",))
    reached, release = Event(), Event()
    try:
        vault.index.build_index()
        vault.index.reindex()
        callback = vault._index_facet._after_reindex
        assert callback is not None

        def paused() -> None:
            callback()
            reached.set()
            assert release.wait(5)

        vault._index_facet._after_reindex = paused
        first = vault.index.reindex_async()
        assert reached.wait(5)
        (tmp_path / "arrival.md").write_text("# Arrival\n")
        second = vault.index.reindex_async()
        vault._coordinator.prepare_index_read(timeout=5)
        assert second.cancel()
        release.set()
        first.result(timeout=5)
        vault.close()
        assert "arrival.md" in index.read_text()
    finally:
        release.set()
        vault.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_forced_build_converges_but_plain_build_does_not(
    tmp_path: Path, asynchronous: bool
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(tmp_path, (".",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        (tmp_path / "late.md").write_text("# Late\n")
        vault.index.build_index()
        assert "late.md" not in index.read_text()
        if asynchronous:
            vault.index.build_index_async(force=True).result(timeout=5)
        else:
            vault.index.build_index(force=True)
        assert "late.md" in index.read_text()
    finally:
        vault.close()


def test_async_reindex_inside_nested_pause_writes(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(tmp_path, (".",))
    try:
        vault.index.build_index()
        (tmp_path / "late.md").write_text("# Late\n")
        with vault.pause_writes():
            with pytest.raises(ValueError), vault.pause_writes():
                raise ValueError("restore depth")
            assert vault._pause_depth.value == 1
            vault.index.reindex_async().result(timeout=5)
            assert "late.md" in index.read_text()
        assert vault._pause_depth.value == 0
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert (
                pool.submit(lambda: getattr(vault._pause_depth, "value", 0)).result()
                == 0
            )
    finally:
        vault.close()


@pytest.mark.parametrize("ancestor", [False, True])
def test_outside_replacement_does_not_block_safe_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor: bool
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    folder = root / "old" if ancestor else root
    folder.mkdir(exist_ok=True)
    note = folder / "note.md"
    note.write_text("# Old\n")
    other = root / "other.md"
    other.write_text("---\ndescription: before\n---\n# Other\n")
    outside = tmp_path / ("outside" if ancestor else "outside.md")
    if ancestor:
        outside.mkdir()
        outside_note = outside / "note.md"
    else:
        outside_note = outside
    outside_note.write_text("# Outside\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        vault.index.reindex()
        if ancestor:
            shutil.rmtree(folder)
            folder.symlink_to(outside, target_is_directory=True)
        else:
            note.unlink()
            note.symlink_to(outside)
        other.write_text("---\ndescription: after\n---\n# Other\n")
        vault.index.reindex()
        listing = index.read_text()
        assert "after" in listing
        assert "/old/note.md" not in listing if ancestor else "/note.md" not in listing
        assert outside_note.read_text() == "# Outside\n"
        writes: list[str] = []
        original = vault._doc_mgr.write

        def record(path: str, *args: object, **kwargs: object) -> object:
            writes.append(path)
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "write", record)
        vault.index.reindex()
        assert writes == []
    finally:
        vault.close()
