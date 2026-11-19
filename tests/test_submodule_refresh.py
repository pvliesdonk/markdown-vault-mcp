"""Opted-in submodule refresh on pull (#1565), against real repositories.

Every test builds two local bare remotes, one for the superproject and one
for the submodule, from synthetic notes; nothing touches the network.
``protocol.file.allow=always`` reaches git through ``GIT_CONFIG_*`` in the
test's environment only, because git refuses file-transport submodules by
default; the product passes nothing of the kind.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.git import submodule_refresh
from markdown_vault_mcp.git.health import (
    SUBMODULE_REASON_NOT_CURRENT,
    SyncHealthTracker,
)
from markdown_vault_mcp.git.strategy import GitWriteSettings, GitWriteStrategy
from markdown_vault_mcp.git.submodule_refresh import (
    SUBMODULE_REASON_DIRTY,
    SUBMODULE_REASON_REMOTE_NOT_ALLOWED,
    SUBMODULE_REASON_UPDATE_FAILED,
    SubmoduleRefresher,
)
from markdown_vault_mcp.git.types import PUSH_REASON_PUSH_FAILED

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-qm", f"write {name}")


@dataclass
class Remotes:
    """The two bare remotes and a writer clone that moves them."""

    root: Path
    super_remote: Path
    sub_remote: Path
    writer: Path

    def bump(self, text: str) -> str:
        """Commit *text* to the submodule remote and record it upstream."""
        sub = self.writer / "notes"
        _git(sub, "checkout", "-q", "-B", "main", "origin/main")
        _commit(sub, "note.md", text)
        _git(sub, "push", "-q", "origin", "main")
        _git(self.writer, "add", "--", "notes")
        _git(self.writer, "commit", "-qm", "bump notes")
        _git(self.writer, "push", "-q", "origin", "main")
        return _git(sub, "rev-parse", "HEAD")


@pytest.fixture
def remotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Remotes:
    """Superproject remote recording a submodule at ``notes/``."""
    for key, value in {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "protocol.file.allow",
        "GIT_CONFIG_VALUE_0": "always",
        "GIT_CONFIG_KEY_1": "init.defaultBranch",
        "GIT_CONFIG_VALUE_1": "main",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    super_remote, sub_remote = tmp_path / "super.git", tmp_path / "sub.git"
    _git(tmp_path, "init", "-q", "--bare", str(super_remote))
    _git(tmp_path, "init", "-q", "--bare", str(sub_remote))
    seed = tmp_path / "seed-sub"
    _git(tmp_path, "init", "-q", str(seed))
    _commit(seed, "note.md", "# v1\n")
    _git(seed, "push", "-q", str(sub_remote), "main")
    writer = tmp_path / "writer"
    _git(tmp_path, "init", "-q", str(writer))
    _commit(writer, "top.md", "# top\n")
    _git(writer, "submodule", "add", "-q", str(sub_remote), "notes")
    _git(writer, "commit", "-qm", "add notes")
    _git(writer, "remote", "add", "origin", str(super_remote))
    _git(writer, "push", "-q", "-u", "origin", "main")
    return Remotes(tmp_path, super_remote, sub_remote, writer)


@pytest.fixture
def make_strategy(
    remotes: Remotes,
) -> Iterator[Any]:
    """Build a managed strategy that clones the superproject like bootstrap."""
    built: list[GitWriteStrategy] = []

    def make(*allowed: str, clone: Path | None = None) -> tuple[GitWriteStrategy, Path]:
        vault = clone or remotes.root / "vault"
        strategy = GitWriteStrategy(
            repo_url=str(remotes.super_remote),
            managed=True,
            enable_push=False,
            git_lfs=False,
            repo_path=vault,
            submodule_remotes=allowed,
        )
        built.append(strategy)
        return strategy, vault

    yield make
    for strategy in built:
        strategy.close()


def _sub_head(vault: Path) -> str | None:
    if not (vault / "notes" / ".git").exists():
        return None
    return _git(vault / "notes", "rev-parse", "HEAD")


def _recorded(vault: Path) -> str:
    return _git(vault, "rev-parse", "HEAD:notes")


# ---------------------------------------------------------------------------
# Default off: today's behaviour, unchanged
# ---------------------------------------------------------------------------


def test_default_leaves_a_bootstrapped_submodule_empty(make_strategy: Any) -> None:
    strategy, vault = make_strategy()
    strategy.sync_once(vault)
    assert list((vault / "notes").iterdir()) == []
    assert not (vault / ".git" / "modules").exists()
    assert strategy.sync_health() is None


def test_default_leaves_a_pulled_submodule_stale(
    remotes: Remotes, make_strategy: Any
) -> None:
    strategy, vault = make_strategy()
    _git(vault, "submodule", "update", "--init", "-q")
    before = _sub_head(vault)
    remotes.bump("# v2\n")

    assert strategy.sync_once(vault) is True

    assert _sub_head(vault) == before != _recorded(vault)
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"
    assert strategy.sync_health() is None


# ---------------------------------------------------------------------------
# Opted in, remote allowed
# ---------------------------------------------------------------------------


def test_opt_in_populates_the_submodule_after_bootstrap(
    remotes: Remotes, make_strategy: Any
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    assert _sub_head(vault) == _recorded(vault)
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"
    assert strategy.sync_health() is None


def test_opt_in_brings_the_submodule_to_a_pulled_gitlink(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # Normalised like GIT_REPO_URL: trailing slash and .git ignored.
    strategy, vault = make_strategy(str(remotes.sub_remote).removesuffix(".git") + "/")
    strategy.sync_once(vault)
    new = remotes.bump("# v2\n")

    with caplog.at_level(logging.INFO, logger=submodule_refresh.__name__):
        assert strategy.sync_once(vault) is True

    assert _sub_head(vault) == new == _recorded(vault)
    assert (vault / "notes" / "note.md").read_text() == "# v2\n"
    assert "git_submodule_updated path=notes" in caplog.text
    assert strategy.sync_health() is None


def test_dry_run_does_not_refresh(remotes: Remotes, make_strategy: Any) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.force_pull(dry_run=True)
    assert _sub_head(vault) is None


def test_a_current_submodule_costs_no_update(
    remotes: Remotes, make_strategy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    calls: list[str] = []
    monkeypatch.setattr(
        submodule_refresh, "_update", lambda _root, path: calls.append(path)
    )
    strategy.sync_once(vault)
    assert calls == []


# ---------------------------------------------------------------------------
# Opted in, refused or failed: reported, never silent
# ---------------------------------------------------------------------------


def test_an_unlisted_remote_is_never_fetched(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    other = str(remotes.root / "elsewhere.git")
    strategy, vault = make_strategy(other)

    with caplog.at_level(logging.WARNING):
        strategy.sync_once(vault)

    assert list((vault / "notes").iterdir()) == []
    assert not (vault / ".git" / "modules").exists()
    health = strategy.sync_health()
    assert health is not None
    assert health.reason == SUBMODULE_REASON_NOT_CURRENT
    assert f"notes:{SUBMODULE_REASON_REMOTE_NOT_ALLOWED}" in caplog.text
    assert str(remotes.sub_remote) in caplog.text


def test_an_unlisted_remote_does_not_fetch_into_a_populated_submodule(
    remotes: Remotes, make_strategy: Any
) -> None:
    strategy, vault = make_strategy("https://example.invalid/other.git")
    _git(vault, "submodule", "update", "--init", "-q")
    known = _git(vault / "notes", "rev-parse", "origin/main")
    remotes.bump("# v2\n")

    strategy.sync_once(vault)

    assert _git(vault / "notes", "rev-parse", "origin/main") == known
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"


def test_a_dirty_submodule_is_left_alone_then_recovers(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    (vault / "notes" / "note.md").write_text("local edit\n")
    remotes.bump("# v2\n")

    with caplog.at_level(logging.WARNING):
        assert strategy.sync_once(vault) is True

    assert (vault / "notes" / "note.md").read_text() == "local edit\n"
    health = strategy.sync_health()
    assert health is not None
    assert health.reason == SUBMODULE_REASON_NOT_CURRENT
    assert f"notes:{SUBMODULE_REASON_DIRTY}" in caplog.text

    _git(vault / "notes", "checkout", "-q", "--", "note.md")
    strategy.sync_once(vault)
    assert (vault / "notes" / "note.md").read_text() == "# v2\n"
    assert strategy.sync_health() is None


def test_an_unreachable_allowed_remote_is_reported(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    remotes.bump("# v2\n")
    remotes.sub_remote.rename(remotes.root / "gone.git")

    with caplog.at_level(logging.WARNING):
        assert strategy.sync_once(vault) is True

    assert (vault / "notes" / "note.md").read_text() == "# v1\n"
    health = strategy.sync_health()
    assert health is not None
    assert health.reason == SUBMODULE_REASON_NOT_CURRENT
    assert f"notes:{SUBMODULE_REASON_UPDATE_FAILED}" in caplog.text


def test_a_missing_git_is_reported_not_raised(
    remotes: Remotes, make_strategy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))

    def boom(_self: SubmoduleRefresher, _root: Path) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(SubmoduleRefresher, "refresh", boom)
    strategy.sync_once(vault)
    health = strategy.sync_health()
    assert health is not None
    assert health.reason == SUBMODULE_REASON_NOT_CURRENT


# ---------------------------------------------------------------------------
# Credentials and redaction
# ---------------------------------------------------------------------------


def test_the_update_never_carries_askpass_or_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_ASKPASS", "/tmp/askpass-with-token.sh")
    seen: dict[str, str] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.update(kwargs["env"])
        seen["timeout"] = str(kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(submodule_refresh.subprocess, "run", fake_run)
    assert submodule_refresh._update(tmp_path, "notes") is None
    assert "GIT_ASKPASS" not in seen
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert seen["timeout"] == "300"


def test_credentials_in_a_remote_url_are_not_logged() -> None:
    assert (
        submodule_refresh._redact_url("https://user:s3cret@host.example/r.git")
        == "https://host.example/r.git"
    )


# ---------------------------------------------------------------------------
# Configuration and health
# ---------------------------------------------------------------------------


def test_the_refresher_requires_an_allowed_remote() -> None:
    with pytest.raises(ValueError, match="allowed remote"):
        SubmoduleRefresher(())


def test_config_defaults_off_and_parses_the_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_SUBMODULE_REMOTES", raising=False)
    assert ProjectConfig.from_env().git.submodule_remotes == ()
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_SUBMODULE_REMOTES",
        "https://a.example/x.git, https://b.example/y",
    )
    assert ProjectConfig.from_env().git.submodule_remotes == (
        "https://a.example/x.git",
        "https://b.example/y",
    )


def test_submodule_health_ranks_below_remote_failures() -> None:
    tracker = SyncHealthTracker()
    tracker.submodules_observed("notes:submodule_dirty")
    snapshot = tracker.snapshot()
    assert snapshot is not None
    assert snapshot.reason == SUBMODULE_REASON_NOT_CURRENT
    assert "submodule" in snapshot.as_payload()["detail"]

    tracker.push_failed(PUSH_REASON_PUSH_FAILED)
    assert tracker.snapshot() is not None
    assert tracker.snapshot().reason == PUSH_REASON_PUSH_FAILED  # type: ignore[union-attr]

    tracker.push_succeeded()
    assert tracker.snapshot().reason == SUBMODULE_REASON_NOT_CURRENT  # type: ignore[union-attr]
    tracker.submodules_observed(None)
    assert tracker.snapshot() is None


# ---------------------------------------------------------------------------
# Review hardening: pathspecs, nesting, registration, relative URLs, limits
# ---------------------------------------------------------------------------


def _add_gitlink_pushed(remotes: Remotes, remote: Path, path: str) -> None:
    """Record another submodule at *path* from *remote* and push it."""
    _git(remotes.writer, "submodule", "add", "-q", str(remote), path)
    _git(remotes.writer, "commit", "-qm", f"add {path}")
    _git(remotes.writer, "push", "-q", "origin", "main")


def _bare_with_note(root: Path, name: str) -> Path:
    remote = root / f"{name}.git"
    _git(root, "init", "-q", "--bare", str(remote))
    seed = root / f"seed-{name}"
    _git(root, "init", "-q", str(seed))
    _commit(seed, "note.md", f"# {name}\n")
    _git(seed, "push", "-q", str(remote), "main")
    return remote


def test_a_glob_path_cannot_pull_in_an_unlisted_sibling(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """`s*` is a gitlink name, not a pattern selecting `s1` (review finding)."""
    evil = _bare_with_note(remotes.root, "evil")
    _add_gitlink_pushed(remotes, remotes.sub_remote, "s*")
    _add_gitlink_pushed(remotes, evil, "s1")
    strategy, vault = make_strategy(str(remotes.sub_remote))

    with caplog.at_level(logging.WARNING):
        strategy.sync_once(vault)

    assert (vault / "s*" / "note.md").read_text() == "# v1\n"
    assert list((vault / "s1").iterdir()) == []
    assert f"s1:{SUBMODULE_REASON_REMOTE_NOT_ALLOWED}" in caplog.text


def test_a_nested_submodule_remote_is_not_fetched(
    remotes: Remotes,
    make_strategy: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inner = _bare_with_note(remotes.root, "inner")
    sub = remotes.writer / "notes"
    _git(sub, "checkout", "-q", "-B", "main", "origin/main")
    _git(sub, "submodule", "add", "-q", str(inner), "in")
    _git(sub, "commit", "-qm", "nest")
    _git(sub, "push", "-q", "origin", "main")
    _git(remotes.writer, "add", "--", "notes")
    _git(remotes.writer, "commit", "-qm", "bump")
    _git(remotes.writer, "push", "-q", "origin", "main")
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    # The operator populated the nested one by hand.
    _git(vault / "notes", "submodule", "update", "--init", "-q")
    inner_before = _git(vault / "notes" / "in", "rev-parse", "HEAD")
    # Move the nested gitlink too, so on-demand recursion has work to do.
    seed = remotes.root / "seed-inner"
    _commit(seed, "note.md", "# inner v2\n")
    _git(seed, "push", "-q", str(inner), "main")
    _git(sub / "in", "fetch", "-q", "origin")
    _git(sub / "in", "checkout", "-q", "origin/main")
    _git(sub, "add", "--", "in")
    _git(sub, "commit", "-qm", "bump inner")
    _git(sub, "push", "-q", "origin", "main")
    remotes.bump("# v2\n")
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))

    strategy.sync_once(vault)

    assert (vault / "notes" / "note.md").read_text() == "# v2\n"
    assert str(inner) not in trace.read_text()
    assert _git(vault / "notes" / "in", "rev-parse", "HEAD") == inner_before


def test_an_unregistered_gitlink_is_reported(
    remotes: Remotes, make_strategy: Any, caplog: pytest.LogCaptureFixture
) -> None:
    sha = _git(remotes.writer, "rev-parse", "HEAD:notes")
    _git(remotes.writer, "update-index", "--add", "--cacheinfo", f"160000,{sha},ghost")
    _git(remotes.writer, "commit", "-qm", "ghost gitlink")
    _git(remotes.writer, "push", "-q", "origin", "main")
    strategy, vault = make_strategy(str(remotes.sub_remote))

    with caplog.at_level(logging.WARNING):
        strategy.sync_once(vault)

    assert "ghost:submodule_not_registered" in caplog.text
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"


def test_a_relative_url_is_resolved_before_it_is_checked(
    remotes: Remotes, make_strategy: Any
) -> None:
    _git(
        remotes.writer,
        "config",
        "-f",
        ".gitmodules",
        "submodule.notes.url",
        "../sub.git",
    )
    _git(remotes.writer, "commit", "-qam", "relative url")
    _git(remotes.writer, "push", "-q", "origin", "main")
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"


def test_a_refresh_without_a_head_move_tells_the_observer(
    remotes: Remotes, make_strategy: Any
) -> None:
    """Retry after a failure: HEAD is unchanged, so the owner must reindex."""
    strategy, vault = make_strategy(str(remotes.sub_remote))
    calls: list[None] = []
    strategy.set_submodule_observer(lambda: calls.append(None))
    strategy.sync_once(vault)
    (vault / "notes" / "note.md").write_text("local edit\n")
    remotes.bump("# v2\n")
    strategy.sync_once(vault)
    calls.clear()
    _git(vault / "notes", "checkout", "-q", "--", "note.md")
    head = _git(vault, "rev-parse", "HEAD")

    assert strategy.sync_once(vault) is False  # HEAD did not move...
    assert _git(vault, "rev-parse", "HEAD") == head
    assert calls == [None]  # ...but the observer heard about the refresh


def test_a_hung_update_times_out_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hang(cmd: list[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(submodule_refresh.subprocess, "run", hang)
    assert submodule_refresh._update(tmp_path, "notes") == "timed out after 300s"
    assert submodule_refresh._git(tmp_path, "status").returncode == -1


def test_a_password_with_an_at_sign_is_removed_whole() -> None:
    assert (
        submodule_refresh._redact_url("https://u:p@ss@host.example/r.git")
        == "https://host.example/r.git"
    )


def test_an_empty_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """examples/*.env ship the variable empty; that must mean off."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    for value in ("", "  ", " , "):
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_SUBMODULE_REMOTES", value)
        assert ProjectConfig.from_env().git.submodule_remotes == ()


@pytest.mark.parametrize("grouped", [False, True])
def test_commit_only_mode_says_the_refresh_is_inactive(
    caplog: pytest.LogCaptureFixture,
    grouped: bool,
) -> None:
    with caplog.at_level(logging.WARNING):
        settings = GitWriteSettings(enable_pull=False, enable_push=False, git_lfs=False)
        strategy = GitWriteStrategy(
            submodule_remotes=("https://example.invalid/x.git",),
            **(
                {"settings": settings}
                if grouped
                else {
                    "enable_pull": False,
                    "enable_push": False,
                    "git_lfs": False,
                }
            ),
        )
    strategy.close()
    assert "git_submodule_refresh_inactive reason=pull_disabled" in caplog.text


# ---------------------------------------------------------------------------
# Second review: insteadOf, token end to end, default argv, budget, logging
# ---------------------------------------------------------------------------


def test_insteadof_is_compared_the_same_before_and_after_population(
    remotes: Remotes, make_strategy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allow-list sees the URL git contacts, populated or not."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_2", f"url.{remotes.root}/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_2", "alias:")
    _git(
        remotes.writer,
        "config",
        "-f",
        ".gitmodules",
        "submodule.notes.url",
        "alias:sub.git",
    )
    _git(remotes.writer, "commit", "-qam", "aliased url")
    _git(remotes.writer, "push", "-q", "origin", "main")
    strategy, vault = make_strategy(str(remotes.sub_remote))
    strategy.sync_once(vault)  # unpopulated path
    assert (vault / "notes" / "note.md").read_text() == "# v1\n"
    remotes.bump("# v2\n")
    strategy.sync_once(vault)  # populated path
    assert (vault / "notes" / "note.md").read_text() == "# v2\n"
    assert strategy.sync_health() is None


def test_a_configured_token_never_reaches_the_update(
    remotes: Remotes, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = subprocess.run
    update_envs: list[dict[str, str]] = []

    def spy(cmd: list[str], **kwargs: Any) -> Any:
        if "update" in cmd and "submodule" in cmd:
            update_envs.append(dict(kwargs["env"]))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(submodule_refresh.subprocess, "run", spy)
    vault = remotes.root / "vault"
    strategy = GitWriteStrategy(
        token="tok-not-a-real-secret",
        repo_url=str(remotes.super_remote),
        managed=True,
        enable_push=False,
        git_lfs=False,
        repo_path=vault,
        submodule_remotes=(str(remotes.sub_remote),),
    )
    try:
        strategy.sync_once(vault)
    finally:
        strategy.close()
    assert update_envs, "the update ran"
    for env in update_envs:
        assert "GIT_ASKPASS" not in env
        assert "tok-not-a-real-secret" not in " ".join(env.values())


def test_default_off_fetch_argv_is_unchanged(
    make_strategy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy, vault = make_strategy()
    calls: list[tuple[str, ...]] = []
    real = GitWriteStrategy._git

    def spy(self: GitWriteStrategy, root: Path, *args: str, **kwargs: Any) -> Any:
        calls.append(args)
        return real(self, root, *args, **kwargs)

    monkeypatch.setattr(GitWriteStrategy, "_git", spy)
    strategy.sync_once(vault)
    assert ("fetch", "origin") in calls
    assert not any("--no-recurse-submodules" in c for c in calls)


def test_a_spent_budget_reports_instead_of_updating(
    remotes: Remotes, make_strategy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    strategy, vault = make_strategy(str(remotes.sub_remote))
    monkeypatch.setattr(submodule_refresh, "_PASS_BUDGET_S", -1)
    strategy.sync_once(vault)
    assert list((vault / "notes").iterdir()) == []
    health = strategy.sync_health()
    assert health is not None
    assert health.reason == SUBMODULE_REASON_NOT_CURRENT


def test_a_changed_failure_set_is_logged_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = SyncHealthTracker()
    with caplog.at_level(logging.WARNING):
        tracker.submodules_observed("a:submodule_dirty")
        tracker.submodules_observed("a:submodule_dirty")
        tracker.submodules_observed("a:submodule_dirty; b:submodule_dirty")
    lines = [r for r in caplog.records if "git_submodule_not_current" in r.getMessage()]
    assert len(lines) == 2
    assert "b:submodule_dirty" in lines[1].getMessage()
