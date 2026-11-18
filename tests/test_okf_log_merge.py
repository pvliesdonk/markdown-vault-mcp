"""Log union preserves entries without duplicating date headings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair

from markdown_vault_mcp._okf_log_merge import merge_log_texts


@pytest.mark.parametrize(
    ("upstream", "local", "expected"),
    [
        (
            "# Log\n\n## 2026-11-18\n\n- upstream\n",
            "# Log\n\n## 2026-11-18\n\n- local\n",
            "# Log\n\n## 2026-11-18\n\n- upstream\n- local\n",
        ),
        *[
            (
                "## 2026-11-18\n\n- upstream\n",
                "## 2026-11-18\n\n" + entry + "\n",
                "## 2026-11-18\n\n- upstream\n" + entry + "\n",
            )
            for entry in [
                "+ plus",
                "* star",
                "-\ttab",
                "- first\n  continuation",
                "- parent\n  - nested",
                "- code\n\n  ```\n  text\n  ```",
            ]
        ],
        (
            "## 2026-11-17\n\n- old\n",
            "## 2026-11-18\n\n- new\n\n## 2026-11-17\n\n- old\n",
            "## 2026-11-18\n\n- new\n\n## 2026-11-17\n\n- old\n",
        ),
        (
            "# Log\n\n  \n## 2026-11-18\n\n- upstream\n",
            "# Log\n\n## 2026-11-18\n\n- local\n",
            "# Log\n\n  \n## 2026-11-18\n\n- upstream\n- local\n",
        ),
        (
            "##  2026-11-18\n\n- remote\n",
            "##  2026-11-18\n\n- local\n",
            "##  2026-11-18\n\n- remote\n- local\n",
        ),
        (
            "# Log\n\n```md\n## 2026-01-01\n- fake\n```\n\n## 2026-11-18\n\n- upstream\n",
            "# Log\n\n```md\n## 2026-01-01\n- fake\n```\n\n## 2026-11-18\n\n- local\n",
            "# Log\n\n```md\n## 2026-01-01\n- fake\n```\n\n## 2026-11-18\n\n- upstream\n- local\n",
        ),
        (
            "# Log\r\n\r\n## 2026-11-18\r\n\r\n- upstream",
            "# Log\n\n## 2026-11-18\n\n- local\n  continuation\n",
            "# Log\r\n\r\n## 2026-11-18\r\n\r\n- upstream\r\n- local\r\n  continuation\r\n",
        ),
        (
            "## 2026-11-18\n\n- upstream\n",
            "## 2026-11-18\n\n- upstream\n",
            "## 2026-11-18\n\n- upstream\n",
        ),
        (
            "## 2026-11-18\n\n- repeated\n- repeated\n",
            "## 2026-11-18\n\n- repeated\n- local\n- local\n",
            "## 2026-11-18\n\n- repeated\n- local\n",
        ),
        (
            "## 2026-11-18\n\n- repeated\n- repeated\n",
            "## 2026-11-18\n\n- repeated\n",
            "## 2026-11-18\n\n- repeated\n",
        ),
        (
            "[ref]: https://example.org\n\n## 2026-11-18\n\n- [remote][ref]\n",
            "[ref]: https://example.org\n\n## 2026-11-18\n\n- [local][ref]\n",
            "[ref]: https://example.org\n\n## 2026-11-18\n\n- [remote][ref]\n- [local][ref]\n",
        ),
    ],
)
def test_union_output(upstream: str, local: str, expected: str) -> None:
    assert merge_log_texts(upstream, local).text == expected


@pytest.mark.parametrize(
    ("upstream", "local", "reason"),
    [
        ("## 2026-11-18\n- x\n", "## Not a date\n- x\n", "non_date_heading"),
        (
            "## 2026-11-18\n- x\n",
            "## 2026-11-18\n- x\n## 2026-11-18\n- y\n",
            "duplicate_date",
        ),
        ("## 2026-11-18\n- x\n", "# no dates\n", "no_date_heading"),
        *[
            (
                "## 2026-11-18\n- x\n",
                "## 2026-11-18\n- x\n\n" + item + "\n",
                "unaccounted_content",
            )
            for item in [
                "prose",
                "1. ordered",
                "> quote",
                "```\ncode\n```",
                "[ref]: https://example.com",
            ]
        ],
        ("## 2026-11-18\n- x\n", "\udcff", "invalid_utf8"),
        (
            "---\ntitle: one\n---\n## 2026-11-18\n- x\n",
            "---\ntitle: two\n---\n## 2026-11-18\n- y\n",
            "frontmatter_differs",
        ),
        (
            "# one\n## 2026-11-18\n- x\n",
            "# two\n## 2026-11-18\n- y\n",
            "preamble_differs",
        ),
        *[
            ("## 2026-11-18\n- x\n", f"## {date}\n- y\n", "invalid_date")
            for date in ["2026-13-40", "2026-02-30"]
        ],
        (
            "## 2026-11-18\n- x\n",
            "## 2026-11-17\n- x\n## 2026-11-18\n- y\n",
            "date_order",
        ),
        (
            "## 2026-11-18\n\n- [Remote][ref]\n\n  [ref]: https://remote.example\n",
            "## 2026-11-18\n\n- [Local][ref]\n\n  [ref]: https://local.example\n",
            "reference_definitions",
        ),
    ],
)
def test_unsafe_log_refuses_union(upstream: str, local: str, reason: str) -> None:
    result = merge_log_texts(upstream, local)
    assert result.text is None
    assert result.reason == reason


@pytest.mark.parametrize(
    ("subdir", "tick"), [(False, False), (True, False), (False, True)]
)
def test_real_git_conflict_merges_entries(
    git_repo_pair: GitRepoPair, tmp_path: Path, subdir: bool, tick: bool
) -> None:
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    vault_root = root / "vault" if subdir else root
    vault_root.mkdir(exist_ok=True)
    path = "vault/log.md" if subdir else "log.md"
    base = "# Log\n\n## 2026-11-17\n\n- old\n"
    _git_commit(root, path, base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(
        other,
        path,
        "# Log\n\n## 2026-11-18\n\n- remote\n\n## 2026-11-17\n\n- old\n",
        "remote",
    )
    _run_git(other, "push", "origin", "main")
    _git_commit(
        root,
        path,
        "# Log\n\n## 2026-11-18\n\n- local\n\n## 2026-11-17\n\n- old\n",
        "local",
    )
    strategy, vault = recovery_vault(vault_root)
    try:
        if tick:
            assert strategy.sync_once(vault_root)
        else:
            result = strategy.force_pull()
            assert result.reason == "rebased"
            assert not result.conflict_files
            assert result.to_sha != result.from_sha
        text = (root / path).read_text()
        assert text.count("## 2026-11-18") == 1
        assert "- remote\n" in text and "- local\n" in text and "- old\n" in text
        assert not list(vault_root.glob("log.conflict-mcp-*.md"))
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


def test_later_local_entry_survives_multiple_replays(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from markdown_vault_mcp.git import conflict
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    spy = Mock(wraps=conflict._merge_log_conflict)
    monkeypatch.setattr(conflict, "_merge_log_conflict", spy)
    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "log.md", base + "- remote\n", "remote")
    _run_git(other, "push", "origin", "main")
    _git_commit(root, "log.md", base + "- local one\n", "one")
    _git_commit(root, "log.md", base + "- local one edited\n- local two\n", "two")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "rebased"
        assert not result.conflict_files
        text = (root / "log.md").read_text()
        for entry in ("base", "remote", "local one", "local one edited", "local two"):
            assert text.count("- " + entry + "\n") == 1
        assert spy.call_count == 2
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("later", ["none", "clean", "deleted"])
def test_abort_after_union_restores_both_versions(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    later: str,
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict
    from markdown_vault_mcp.utils.fs import path_exists
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _git_commit(root, "other.md", "base\n", "seed other")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    upstream = base + "- remote\n"
    _git_commit(other, "log.md", upstream, "remote")
    _git_commit(other, "other.md", "remote\n", "remote other")
    _run_git(other, "push", "origin", "main")
    latest = base + "- local one\n"
    _git_commit(root, "log.md", latest, "local")
    if later != "none":
        latest += "- local two\n"
        _git_commit(root, "log.md", latest, "later")
    if later == "deleted":
        _run_git(root, "rm", "log.md")
        _run_git(root, "commit", "-m", "delete log")
    _git_commit(root, "other.md", "local\n", "local other")
    original_run = subprocess.run
    calls = 0

    def fail_continue(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        command = args[0]
        if "rebase" in command and "--continue" in command:
            calls += 1
            if calls >= 2:
                return subprocess.CompletedProcess(command, 1, "", "forced failure")
        return original_run(*args, **kwargs)

    strategy, vault = recovery_vault(root)
    try:
        monkeypatch.setattr(conflict.subprocess, "run", fail_continue)
        result = strategy.force_pull()
        if later == "deleted":
            assert result.reason == "conflict_resolution_failed"
            assert not path_exists(root / "log.md")
            assert "git_conflict_mcp_version_unreadable path=log.md" in caplog.text
        else:
            assert result.reason == "conflicts_resolved_with_siblings"
            assert (root / "log.md").read_text() == upstream
            sibling = next(
                path
                for path in result.conflict_files
                if path.startswith("log.conflict-mcp-")
            )
            assert (root / sibling).read_text() == latest
    finally:
        vault.close()
        strategy.close()


def test_log_union_and_concept_conflict_only_saves_concept(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed log")
    _git_commit(root, "concept.md", "# Base\n", "seed concept")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    (other / "log.md").write_text(base + "- remote\n")
    (other / "concept.md").write_text("# Remote\n")
    _run_git(other, "add", "log.md", "concept.md")
    _run_git(other, "commit", "-m", "remote")
    _run_git(other, "push", "origin", "main")
    (root / "log.md").write_text(base + "- local\n")
    (root / "concept.md").write_text("# Local\n")
    _run_git(root, "add", "log.md", "concept.md")
    _run_git(root, "commit", "-m", "local")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert result.conflict_files[0].startswith("concept.conflict-mcp-")
        log = (root / "log.md").read_text()
        assert log.count("## 2026-11-18") == 1
        assert "- remote\n" in log and "- local\n" in log
        assert "conflict_with" not in log
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


def test_git_filter_preserves_readable_worktree(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    import shlex
    import sys

    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    script = tmp_path / "filter.py"
    script.write_text(
        "import sys\ndata = sys.stdin.read()\nsys.stdout.write(data.replace(sys.argv[1], sys.argv[2]))\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    for side in ("clean", "smudge"):
        before, after = ("WORK", "BLOB") if side == "clean" else ("BLOB", "WORK")
        _run_git(root, "config", f"filter.note.{side}", f"{command} {before} {after}")
    (root / ".gitattributes").write_text("log.md filter=note\n")
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "add", ".gitattributes")
    _run_git(root, "commit", "-m", "attributes")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    for side in ("clean", "smudge"):
        before, after = ("WORK", "BLOB") if side == "clean" else ("BLOB", "WORK")
        _run_git(other, "config", f"filter.note.{side}", f"{command} {before} {after}")
    _git_commit(other, "log.md", base + "- remote WORK\n", "remote")
    _run_git(other, "push", "origin", "main")
    _git_commit(root, "log.md", base + "- local WORK\n", "local")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "rebased"
        content = (root / "log.md").read_text()
        assert "- remote WORK\n" in content and "- local WORK\n" in content
        blob = _run_git(root, "show", "HEAD:log.md")
        assert "- remote BLOB\n" in blob and "- local BLOB\n" in blob
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


@pytest.mark.parametrize("failure", ["hash-object", "cat-file"])
def test_worktree_conversion_failure_preserves_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict

    upstream = "## 2026-11-18\n\n- remote\n"
    local = "## 2026-11-18\n\n- local\n"
    target = tmp_path / "log.md"
    target.write_text("conflict markers\n")
    commands: list[str] = []

    def capture(_root: Path, command: str, *_args: Any, **_kwargs: Any) -> Any:
        commands.append(command)
        output = upstream if command == "show" else "blob-id\n"
        return subprocess.CompletedProcess(command, int(command == failure), output, "")

    monkeypatch.setattr(conflict, "run_git_capturing", capture)
    monkeypatch.setattr(
        conflict, "run_git", lambda *_args, **_kwargs: pytest.fail("index changed")
    )
    assert not conflict._merge_log_conflict(tmp_path, "log.md", None, local)
    assert target.read_text() == "conflict markers\n"
    assert commands == (
        ["show", "hash-object"]
        if failure == "hash-object"
        else ["show", "hash-object", "cat-file"]
    )
    assert (
        "git_conflict_log_merge_fallback path=log.md reason=worktree_conversion"
        in caplog.text
    )
