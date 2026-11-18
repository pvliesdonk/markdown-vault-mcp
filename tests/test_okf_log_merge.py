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


def _prepare_abort(
    git_repo_pair: GitRepoPair, tmp_path: Path, case: tuple[str, bool]
) -> tuple[Path, str, str]:
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    later, filtered = case
    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    if filtered:
        _hex_filter(tmp_path, root)
        (root / ".gitattributes").write_text("log.md filter=note\n")
        _run_git(root, "add", ".gitattributes")
    _git_commit(root, "log.md", base, "seed")
    _git_commit(root, "other.md", "base\n", "seed other")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    if filtered:
        _hex_filter(tmp_path, other)
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
    return root, upstream, latest


@pytest.mark.parametrize(
    "case",
    [("none", False), ("clean", False), ("deleted", False), ("clean", True)],
)
def test_abort_after_union_restores_both_versions(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: tuple[str, bool],
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict
    from markdown_vault_mcp.utils.fs import path_exists
    from tests.fixtures.conflict_recovery import recovery_vault

    later, _ = case
    root, upstream, latest = _prepare_abort(git_repo_pair, tmp_path, case)
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


def _hex_filter(tmp_path: Path, *repos: Path) -> None:
    import shlex
    import sys

    from tests.fixtures.git import _run_git

    script = tmp_path / "hex_filter.py"
    script.write_text(
        "import sys\n"
        "for line in sys.stdin.buffer:\n"
        "    if sys.argv[1] == 'clean':\n"
        "        sys.stdout.buffer.write(b'E:' + line.hex().encode() + b'\\n')\n"
        "    else:\n"
        "        sys.stdout.buffer.write(bytes.fromhex(line[2:].decode()) if line.startswith(b'E:') else line)\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    for repo in repos:
        for side in ("clean", "smudge"):
            _run_git(repo, "config", f"filter.note.{side}", f"{command} {side}")


@pytest.mark.parametrize(
    "mode", ["union", "fallback", "local_failure", "upstream_failure"]
)
def test_filtered_log_resolution(
    git_repo_pair: GitRepoPair,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    _hex_filter(tmp_path, root)
    (root / ".gitattributes").write_text("log.md filter=note\n")
    _run_git(root, "add", ".gitattributes")
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _hex_filter(tmp_path, other)
    upstream = base + "- remote\n"
    _git_commit(other, "log.md", upstream, "remote")
    _run_git(other, "push", "origin", "main")
    local = base + ("1. ordered\n" if mode == "fallback" else "- local\n")
    _git_commit(root, "log.md", local, "local")
    original = conflict.run_git_capturing

    def capture(repo: Path, *args: Any, **kwargs: Any) -> Any:
        spec = "REBASE_HEAD:log.md" if mode == "local_failure" else ":2:log.md"
        if (
            mode.endswith("failure")
            and args[:2] == ("cat-file", "--filters")
            and spec in args
        ):
            return subprocess.CompletedProcess(args, 1, "", "forced failure")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(conflict, "run_git_capturing", capture)
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        if mode == "union":
            assert result.reason == "rebased"
            assert (root / "log.md").read_text() == base + "- remote\n- local\n"
            blob = _run_git(root, "show", "HEAD:log.md")
            assert blob.startswith("E:") and "- remote" not in blob
            assert not result.conflict_files
            assert _run_git(root, "status", "--porcelain") == ""
        elif mode == "local_failure":
            assert result.reason == "conflict_resolution_failed"
            assert (root / "log.md").read_text() == upstream
            assert not result.conflict_files
            assert "git_conflict_mcp_version_unreadable path=log.md" in caplog.text
        else:
            assert result.reason == "conflicts_resolved_with_siblings"
            assert (root / "log.md").read_text() == upstream
            assert len(result.conflict_files) == 1
            assert (root / result.conflict_files[0]).read_text() == local
    finally:
        vault.close()
        strategy.close()


def test_failed_upstream_read_leaves_conflict_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict

    target = tmp_path / "log.md"
    target.write_text("conflict markers\n")
    commands: list[tuple[Any, ...]] = []

    def capture(_root: Path, *args: Any, **_kwargs: Any) -> Any:
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, "", "failure")

    monkeypatch.setattr(conflict, "run_git_capturing", capture)
    monkeypatch.setattr(
        conflict, "run_git", lambda *_args, **_kwargs: pytest.fail("index changed")
    )
    assert not conflict._merge_log_conflict(tmp_path, "log.md", None, "local")
    assert target.read_text() == "conflict markers\n"
    assert commands == [("cat-file", "--filters", ":2:log.md")]
