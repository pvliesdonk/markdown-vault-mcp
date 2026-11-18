"""Log union preserves entries without duplicating date headings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair

from markdown_vault_mcp._okf_log_merge import merge_log_texts


def test_same_date_keeps_both_entries() -> None:
    upstream = "# Log\n\n## 2026-11-18\n\n- upstream\n"
    local = "# Log\n\n## 2026-11-18\n\n- local\n"
    result = merge_log_texts(upstream, local)
    assert result.text == "# Log\n\n## 2026-11-18\n\n- upstream\n- local\n"
    assert result.added == 1


@pytest.mark.parametrize(
    "entry",
    [
        "+ plus",
        "* star",
        "-\ttab",
        "- first\n  continuation",
        "- parent\n  - nested",
        "- code\n\n  ```\n  text\n  ```",
    ],
)
def test_complete_local_entry_is_preserved(entry: str) -> None:
    base = "# Log\n\n## 2026-11-18\n\n- upstream\n"
    result = merge_log_texts(base, "# Log\n\n## 2026-11-18\n\n" + entry + "\n")
    assert result.text is not None
    assert entry in result.text


@pytest.mark.parametrize(
    "content",
    [
        "prose",
        "1. ordered",
        "> quote",
        "```\ncode\n```",
        "## Not a date",
        "[ref]: https://example.com",
    ],
)
def test_unaccounted_content_falls_back(content: str) -> None:
    base = "# Log\n\n## 2026-11-18\n\n- upstream\n"
    result = merge_log_texts(base, base + "\n" + content + "\n")
    assert result.text is None
    assert result.reason


def test_local_subset_is_byte_identical() -> None:
    base = "# Log\r\n\r\n## 2026-11-18\r\n\r\n- upstream\r\n"
    assert merge_log_texts(base, base).text == base


def test_local_new_date_precedes_older_date() -> None:
    base = "# Log\n\n## 2026-11-17\n\n- old\n"
    local = "# Log\n\n## 2026-11-18\n\n- new\n\n## 2026-11-17\n\n- old\n"
    assert merge_log_texts(base, local).text == local


@pytest.mark.parametrize("subdir", [False, True])
@pytest.mark.parametrize("tick", [False, True])
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


@pytest.mark.parametrize(
    "bad",
    [
        "\udcff",
        "## 2026-11-18\n\n- one\n\n## 2026-11-18\n\n- two\n",
        "## 2026-11-18\n\n- item\n\n```\nunclosed\n",
    ],
)
def test_unsafe_log_refuses_union(bad: str) -> None:
    result = merge_log_texts("## 2026-11-18\n\n- valid\n", bad)
    assert result.text is None
    assert result.reason


def test_differing_frontmatter_refuses_union() -> None:
    upstream = "---\ntitle: one\n---\n\n## 2026-11-18\n\n- one\n"
    local = "---\ntitle: two\n---\n\n## 2026-11-18\n\n- two\n"
    assert merge_log_texts(upstream, local).reason == "frontmatter_differs"


def test_crlf_and_missing_final_newline() -> None:
    upstream = "# Log\r\n\r\n## 2026-11-18\r\n\r\n- upstream"
    local = "# Log\n\n## 2026-11-18\n\n- local\n  continuation\n"
    result = merge_log_texts(upstream, local)
    assert (
        result.text
        == "# Log\r\n\r\n## 2026-11-18\r\n\r\n- upstream\r\n- local\r\n  continuation\r\n"
    )


def test_fenced_preamble_is_not_a_section() -> None:
    preamble = "# Log\n\n```md\n## 2026-01-01\n- fake\n```\n\n"
    upstream = preamble + "## 2026-11-18\n\n- upstream\n"
    local = preamble + "## 2026-11-18\n\n- local\n"
    result = merge_log_texts(upstream, local)
    assert result.text == upstream + "- local\n"


def test_later_local_entry_survives_multiple_replays(
    git_repo_pair: GitRepoPair, tmp_path: Path
) -> None:
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    _git_commit(other, "log.md", base + "- remote\n", "remote")
    _run_git(other, "push", "origin", "main")
    _git_commit(root, "log.md", base + "- local one\n", "one")
    _git_commit(root, "log.md", base + "- local one\n- local two\n", "two")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "rebased"
        assert not result.conflict_files
        text = (root / "log.md").read_text()
        for entry in ("base", "remote", "local one", "local two"):
            assert text.count("- " + entry + "\n") == 1
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


def test_malformed_local_log_is_saved_verbatim(
    git_repo_pair: GitRepoPair, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    upstream = base + "- remote\n"
    _git_commit(other, "log.md", upstream, "remote")
    _run_git(other, "push", "origin", "main")
    local = base + "\nindependent prose\n"
    _git_commit(root, "log.md", local, "local")
    strategy, vault = recovery_vault(root)
    try:
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert (root / result.conflict_files[0]).read_text() == local
        assert (root / "log.md").read_text() == upstream
        assert "git_conflict_log_merge_fallback" in caplog.text
        assert "unaccounted_content" in caplog.text
    finally:
        vault.close()
        strategy.close()


def test_abort_after_union_restores_both_versions(
    git_repo_pair: GitRepoPair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from markdown_vault_mcp.git import conflict
    from tests.fixtures.conflict_recovery import recovery_vault
    from tests.fixtures.git import _git_clone, _git_commit, _run_git

    root = git_repo_pair.local_path
    base = "# Log\n\n## 2026-11-18\n\n- base\n"
    _git_commit(root, "log.md", base, "seed")
    _run_git(root, "push", "origin", "main")
    other = _git_clone(git_repo_pair, tmp_path)
    upstream = base + "- remote\n"
    _git_commit(other, "log.md", upstream, "remote")
    _run_git(other, "push", "origin", "main")
    local = base + "- local\n"
    _git_commit(root, "log.md", local, "local")
    original_run = subprocess.run

    def fail_continue(*args: Any, **kwargs: Any) -> Any:
        command = args[0]
        if "rebase" in command and "--continue" in command:
            return subprocess.CompletedProcess(command, 1, "", "forced failure")
        return original_run(*args, **kwargs)

    strategy, vault = recovery_vault(root)
    try:
        monkeypatch.setattr(conflict.subprocess, "run", fail_continue)
        result = strategy.force_pull()
        assert result.reason == "conflicts_resolved_with_siblings"
        assert len(result.conflict_files) == 1
        assert (root / result.conflict_files[0]).read_text() == local
        assert (root / "log.md").read_text() == upstream
        assert _run_git(root, "status", "--porcelain") == ""
    finally:
        vault.close()
        strategy.close()


def test_existing_date_and_concept_conflict_only_saves_concept(
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


def test_blank_preamble_separators_do_not_block_union() -> None:
    upstream = "# Log\n\n  \n## 2026-11-18\n\n- upstream\n"
    local = "# Log\n\n## 2026-11-18\n\n- local\n"
    result = merge_log_texts(upstream, local)
    assert result.text is not None
    assert "- upstream\n- local\n" in result.text


@pytest.mark.parametrize("extra", ["", "- local\n"])
def test_upstream_repeated_entry_is_deduplicated(extra: str) -> None:
    upstream = "## 2026-11-18\n\n- repeated\n- repeated\n"
    local = "## 2026-11-18\n\n- repeated\n" + extra
    result = merge_log_texts(upstream, local)
    assert result.text is not None
    assert result.text.count("- repeated\n") == 1
    assert ("- local\n" in result.text) == bool(extra)
