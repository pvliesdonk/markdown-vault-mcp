"""The strategy accepts resolved identities, not claim configuration (#1236)."""

from __future__ import annotations

import inspect
import os
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.git import GitWriteStrategy

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.git import GitRepoPair


@pytest.mark.parametrize("name", ["commit_name_claim", "commit_email_claim"])
@pytest.mark.parametrize("value", [None, "name"])
def test_removed_claim_keyword_is_rejected(name: str, value: str | None) -> None:
    """Even an explicitly disabled former claim argument fails visibly."""
    kwargs: dict[str, Any] = {name: value}
    with pytest.raises(TypeError, match=f"unexpected keyword argument '{name}'"):
        GitWriteStrategy(**kwargs)


def test_old_positional_claims_cannot_become_lfs_or_repo_path() -> None:
    """Removing middle parameters must not silently rebind their old values."""
    args: list[Any] = [
        None,
        "x-access-token",
        None,
        False,
        True,
        True,
        30.0,
        None,
        None,
    ]
    for legacy_tail in (["name"], [None, None], ["name", "email", False, None]):
        with pytest.raises(TypeError, match="positional"):
            GitWriteStrategy(*args, *legacy_tail)


def test_trailing_options_are_keyword_only(tmp_path: Path) -> None:
    """The retained LFS and repository options remain usable by name."""
    signature = inspect.signature(GitWriteStrategy)
    for name in ("git_lfs", "repo_path"):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    strategy = GitWriteStrategy(git_lfs=False, repo_path=tmp_path)
    try:
        assert strategy._git_lfs is False
        assert strategy._repo_path == tmp_path
    finally:
        strategy.close()


def test_default_committer_without_checkout_identity_needs_no_warning(
    git_repo_pair: GitRepoPair,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real commit uses the supplied default even with no Git identity config."""
    git_repo = git_repo_pair.local_path
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    for key in ("user.name", "user.email"):
        subprocess.run(
            ["git", "-C", str(git_repo), "config", "--unset-all", key], check=True
        )
    missing_email = subprocess.run(
        ["git", "-C", str(git_repo), "config", "user.email"],
        capture_output=True,
        text=True,
    )
    assert missing_email.returncode == 1
    assert missing_email.stdout == ""
    strategy = GitWriteStrategy(enable_pull=False, enable_push=False, git_lfs=False)
    note = git_repo / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    try:
        strategy(note, "# Note\n", "write")
        identity = subprocess.run(
            ["git", "-C", str(git_repo), "log", "-1", "--format=%an <%ae>%n%cn <%ce>"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        assert identity == ["markdown-vault-mcp <noreply@markdown-vault-mcp>"] * 2
        assert not any("no user.email" in record.message for record in caplog.records)
    finally:
        strategy.close()
