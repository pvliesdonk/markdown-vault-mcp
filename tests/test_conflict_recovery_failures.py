"""Recovery failures preserve local data and remain visible to pull callers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.git import GitWriteStrategy
from tests.fixtures.conflict_recovery import recovery_vault

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("name", ["index.md", "concept.md"])
def test_non_utf8_conflict_retains_local_bytes(
    git_repo_pair: GitRepoPair, name: str
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    note = root / name
    note.write_bytes(b"base\n")
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    (other / name).write_bytes(b"remote\n")
    _run_git(other, "add", name)
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    local = b"local\xff\n"
    note.write_bytes(local)
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "local")
    strategy = GitWriteStrategy(
        enable_pull=True, enable_push=False, repo_path=root, git_lfs=False
    )
    try:
        result = strategy.force_pull()
        if name == "concept.md":
            assert result.reason == "conflict_resolution_failed"
            assert note.read_bytes() == local
        else:
            assert result.reason == "conflicts_resolved_with_siblings"
            assert (root / result.conflict_files[0]).read_bytes() == local
    finally:
        strategy.close()


def test_deleted_index_is_not_recreated(git_repo_pair: GitRepoPair) -> None:
    root = git_repo_pair.local_path
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n')
    strategy, vault = recovery_vault(root, build=True)
    try:
        assert vault._okf_convention is not None
        index.unlink()
        vault._refresh_recovered_indexes([index])
        assert not index.exists()
    finally:
        vault.close()
        strategy.close()


def test_no_longer_designated_index_is_not_refreshed(
    git_repo_pair: GitRepoPair,
) -> None:
    root = git_repo_pair.local_path
    index = root / "index.md"
    index.write_text("authored\n")
    strategy, vault = recovery_vault(root, authority="outside", build=True)
    try:
        assert vault._okf_convention is not None
        vault._refresh_recovered_indexes([index])
        assert index.read_text() == "authored\n"
    finally:
        vault.close()
        strategy.close()


def test_local_delete_upstream_modify_concept_keeps_failure_outcome(
    git_repo_pair: GitRepoPair,
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    (root / "c.md").write_text("base\n")
    _run_git(root, "add", "c.md")
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.email", "other@example.com")
    _run_git(other, "config", "user.name", "Other")
    (other / "c.md").write_text("remote edit\n")
    _run_git(other, "add", "c.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    _run_git(root, "rm", "c.md")
    _run_git(root, "commit", "-m", "local delete")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflict_resolution_failed"
        assert not result.applied
        assert (root / "c.md").read_text() == "remote edit\n"
        assert not list(root.glob("c.conflict-mcp-*.md"))
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("point", ["refresh", "generate"])
def test_unexpected_refresh_failure_is_logged_and_convergence_heals(
    git_repo_pair: GitRepoPair,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    point: str,
) -> None:
    from tests.conftest import wait_for_writer_drain
    from tests.fixtures.conflict_recovery import _setup_generated_conflict

    _root, vault_root, index = _setup_generated_conflict(git_repo_pair, False)
    strategy, vault = recovery_vault(vault_root, build=True)
    assert vault._okf_convention is not None

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected recovery error")

    try:
        assert strategy.force_pull().reason == "rebased"
        with monkeypatch.context() as patch:
            if point == "refresh":
                patch.setattr(vault, "_refresh_recovered_indexes", fail)
                vault._regenerate_recovered_indexes(defer_group_recovery=False)
            else:
                patch.setattr(vault._okf_migrate, "generate_index", fail)
                vault.reconcile_index_with_head(source="git_sync")
        marker = (
            "git_pending_index_regeneration_failed"
            if point == "refresh"
            else "okf_convention_index_failed"
        )
        assert marker in caplog.text
        assert "local.md" not in index.read_text()
        vault.reconcile_index_with_head(source="git_sync")
        wait_for_writer_drain(vault)
        assert "local.md" in index.read_text()
    finally:
        vault.close()
        strategy.close()


def test_generated_index_refuses_path_outside_vault(git_repo_pair: GitRepoPair) -> None:
    root = git_repo_pair.local_path
    outside = root.parent / "outside" / "index.md"
    outside.parent.mkdir()
    outside.write_text("authored\n")
    strategy, vault = recovery_vault(root, build=True)
    try:
        assert vault._okf_convention is not None
        assert not vault._is_generated_index(outside)
        vault._refresh_recovered_indexes([outside])
        assert outside.read_text() == "authored\n"
    finally:
        vault.close()
        strategy.close()


def _setup_deleted_generated_conflict(
    git_repo_pair: GitRepoPair, subdir: bool, mixed: bool
) -> tuple[Path, Path]:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    vault_root = root / "vault" if subdir else root
    vault_root.mkdir(exist_ok=True)
    prefix = "vault/" if subdir else ""
    index = vault_root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Base\n')
    if mixed:
        (vault_root / "concept.md").write_text("base concept\n")
    _run_git(
        root, "add", f"{prefix}index.md", *([f"{prefix}concept.md"] if mixed else [])
    )
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    (other / prefix / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Remote\n')
    (other / prefix / "remote.md").write_text("# Remote\n")
    if mixed:
        (other / prefix / "concept.md").write_text("remote concept\n")
    _run_git(
        other,
        "add",
        f"{prefix}index.md",
        f"{prefix}remote.md",
        *([f"{prefix}concept.md"] if mixed else []),
    )
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    _run_git(
        root, "rm", f"{prefix}index.md", *([f"{prefix}concept.md"] if mixed else [])
    )
    vault_root.mkdir(exist_ok=True)
    (vault_root / "local.md").write_text("# Local\n")
    _run_git(root, "add", f"{prefix}local.md")
    _run_git(root, "commit", "-m", "local delete")
    return vault_root, index


@pytest.mark.parametrize(
    ("subdir", "tick", "mixed"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
    ],
)
def test_local_delete_upstream_modify_generated_index_is_regenerated(
    git_repo_pair: GitRepoPair, subdir: bool, tick: bool, mixed: bool
) -> None:
    from tests.conftest import wait_for_writer_drain

    vault_root, index = _setup_deleted_generated_conflict(git_repo_pair, subdir, mixed)
    strategy, vault = recovery_vault(vault_root, build=True)
    try:
        if tick:
            assert strategy.sync_once(vault_root)
        else:
            result = strategy.force_pull()
            assert result.reason == "rebased"
            assert result.applied and not result.conflict_files
            assert result.to_sha != result.from_sha
        assert not list(vault_root.glob("index.conflict-mcp-*.md"))
        if mixed:
            assert (vault_root / "concept.md").read_text() == "remote concept\n"
            assert not list(vault_root.glob("concept.conflict-mcp-*.md"))
        vault.reconcile_index_with_head(source="git_sync")
        wait_for_writer_drain(vault)
        text = index.read_text()
        assert "local.md" in text and "remote.md" in text and "okf_version" in text
        assert "conflict_with" not in text and "conflict_date" not in text
        vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)
        assert index.read_text() == text
    finally:
        vault.close()
        strategy.close()


def test_mixed_generated_and_local_concept_deletion_reports_failure(
    git_repo_pair: GitRepoPair,
) -> None:
    vault_root, _index = _setup_deleted_generated_conflict(git_repo_pair, False, True)
    strategy, vault = recovery_vault(vault_root, build=True)
    try:
        result = strategy.force_pull()
        assert not result.applied and result.reason == "conflict_resolution_failed"
        assert not result.conflict_files
        assert (vault_root / "concept.md").read_text() == "remote concept\n"
    finally:
        vault.close()
        strategy.close()


def test_unreadable_local_concept_is_not_masked_by_generated_index(
    git_repo_pair: GitRepoPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict
    from tests.fixtures.conflict_recovery import _setup_multiple_conflicts

    root, _index, _concept = _setup_multiple_conflicts(git_repo_pair)
    original = conflict.run_git_capturing

    def unreadable(
        git_root: Path, *args: str, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if "REBASE_HEAD:concept.md" in args:
            return subprocess.CompletedProcess(args, 1, "", "unreadable")
        return original(git_root, *args, **kwargs)

    monkeypatch.setattr(conflict, "run_git_capturing", unreadable)
    strategy, vault = recovery_vault(root, build=True)
    try:
        result = strategy.force_pull()
        assert not result.applied and result.reason == "conflict_resolution_failed"
        assert not result.conflict_files
    finally:
        vault.close()
        strategy.close()


def test_saved_sibling_does_not_mask_unsaved_concept_deletion(
    git_repo_pair: GitRepoPair,
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    (root / "concept.md").write_text("base concept\n")
    (root / "log.md").write_text("base log\n")
    _run_git(root, "add", "concept.md", "log.md")
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    (other / "concept.md").write_text("remote concept\n")
    (other / "log.md").write_text("remote log\n")
    _run_git(other, "add", "concept.md", "log.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    _run_git(root, "rm", "concept.md")
    (root / "log.md").write_text("local log\n")
    _run_git(root, "add", "log.md")
    _run_git(root, "commit", "-m", "local")
    strategy, vault = recovery_vault(root, authority="disabled")
    try:
        result = strategy.force_pull()
        assert not result.applied and result.reason == "conflict_resolution_failed"
        siblings = list(root.glob("log.conflict-mcp-*.md"))
        assert len(siblings) == 1 and siblings[0].read_text() == "local log\n"
        assert result.conflict_files == (str(siblings[0].relative_to(root)),)
        assert result.to_sha == _run_git(root, "rev-parse", "HEAD").strip()
        assert (root / "concept.md").read_text() == "remote concept\n"
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize(
    ("subdir", "authority"),
    [(False, "enabled"), (True, "enabled"), (False, "inactive"), (True, "inactive")],
)
def test_upstream_deleted_generated_index_is_regenerated(
    git_repo_pair: GitRepoPair, subdir: bool, authority: str
) -> None:
    import subprocess

    from tests.conftest import wait_for_writer_drain
    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    vault_root = root / "vault [odd]" if subdir else root
    vault_root.mkdir(exist_ok=True)
    prefix = "vault [odd]/" if subdir else ""
    index = vault_root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Base\n')
    _run_git(root, "add", f"{prefix}index.md")
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    _run_git(other, "rm", f"{prefix}index.md")
    (other / prefix).mkdir(exist_ok=True)
    (other / prefix / "remote.md").write_text("# Remote\n")
    _run_git(other, "add", f"{prefix}remote.md")
    _run_git(other, "commit", "-m", "upstream delete")
    _run_git(other, "push", "origin", "main")
    index.write_text('---\nokf_version: "0.2"\n---\n# Local\n')
    (vault_root / "local.md").write_text("# Local\n")
    _run_git(root, "add", f"{prefix}index.md", f"{prefix}local.md")
    _run_git(root, "commit", "-m", "local change")
    strategy, vault = recovery_vault(vault_root, authority=authority, build=True)
    try:
        result = strategy.force_pull()
        assert result.applied and result.reason == "rebased"
        assert not result.conflict_files
        assert not list(vault_root.glob("index.conflict-mcp-*.md"))
        vault.reconcile_index_with_head(source="git_sync")
        wait_for_writer_drain(vault)
        text = index.read_text()
        assert "okf_version" in text and "0.2" in text
        assert "local.md" in text and "remote.md" in text
        assert "conflict_with" not in text and "conflict_date" not in text
        vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)
        assert index.read_text() == text
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize(
    ("name", "authority"),
    [("concept.md", "enabled"), ("index.md", "disabled"), ("log.md", "enabled")],
)
def test_upstream_deleted_authored_file_keeps_local_copy(
    git_repo_pair: GitRepoPair, name: str, authority: str
) -> None:
    import subprocess

    from tests.fixtures.git import _run_git

    root = git_repo_pair.local_path
    path = root / name
    path.write_text("base\n")
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "seed")
    _run_git(root, "push", "origin", "main")
    other = root.parent / "other"
    subprocess.run(
        ["git", "clone", str(git_repo_pair.remote_path), str(other)],
        check=True,
        capture_output=True,
    )
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "config", "user.email", "other@example.com")
    _run_git(other, "rm", name)
    _run_git(other, "commit", "-m", "delete")
    _run_git(other, "push", "origin", "main")
    local = b"local content\xff\n" if name != "concept.md" else b"local content\n"
    path.write_bytes(local)
    _run_git(root, "add", name)
    _run_git(root, "commit", "-m", "local change")
    strategy, vault = recovery_vault(root, authority=authority)
    try:
        result = strategy.force_pull()
        if name == "concept.md":
            assert not result.applied and result.reason == "conflict_resolution_failed"
            assert path.read_bytes() == local
            assert not list(root.glob("concept.conflict-mcp-*.md"))
        else:
            assert (
                result.applied and result.reason == "conflicts_resolved_with_siblings"
            )
            assert not path.exists()
            assert len(result.conflict_files) == 1
            assert (root / result.conflict_files[0]).read_bytes() == local
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize(
    ("returncode", "entries"),
    [
        (1, ""),
        (0, "100644 abc 2\tindex.md\0"),
        (0, "100644 abc 2\tindex.md\x00100644 def 3\tindex.md\0"),
    ],
)
def test_upstream_deletion_check_refuses_git_error_and_other_stages(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, entries: str
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict

    monkeypatch.setattr(
        conflict,
        "run_git_capturing",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], returncode, entries, ""
        ),
    )
    assert not conflict._upstream_deleted_generated(
        git_repo, "index.md", None, lambda _path: True
    )
