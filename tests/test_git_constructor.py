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


def test_settings_match_legacy_and_wire_collaborators() -> None:
    """Settings and legacy options configure the same strategy and collaborators."""
    from dataclasses import asdict

    from markdown_vault_mcp.git import GitWriteSettings

    settings = GitWriteSettings(
        token="secret",
        username="user",
        repo_url="https://example.com/repo",
        managed=True,
        enable_pull=False,
        enable_push=False,
        push_delay_s=12,
        commit_name="name",
        commit_email="email",
        git_lfs=False,
    )
    new = GitWriteStrategy(settings=settings)
    old = GitWriteStrategy(**asdict(settings))
    try:
        for name in asdict(settings):
            assert getattr(new, f"_{name}") == getattr(old, f"_{name}")
        for name in ("token", "username", "_repo_url"):
            assert getattr(new._bootstrap, name) == getattr(old._bootstrap, name)
        for name in ("enable_push", "push_delay_s"):
            assert getattr(new._push_scheduler, f"_{name}") == getattr(
                old._push_scheduler, f"_{name}"
            )
    finally:
        new.close()
        old.close()


def test_settings_conflicts_and_defaults() -> None:
    """Mixed nondefault arguments fail without disclosing their values."""
    from markdown_vault_mcp.git import GitWriteSettings

    with pytest.raises(ValueError) as error:
        GitWriteStrategy(token="secret", enable_push=False, settings=GitWriteSettings())
    assert "token" in str(error.value) and "enable_push" in str(error.value)
    assert "secret" not in str(error.value)
    strategy = GitWriteStrategy(
        enable_push=True, settings=GitWriteSettings(enable_push=False)
    )
    try:
        assert strategy._enable_push is False
    finally:
        strategy.close()


def test_settings_contract() -> None:
    """Settings retain constructor defaults, hide the token, and are immutable."""
    from dataclasses import FrozenInstanceError, fields

    from markdown_vault_mcp.git import GitWriteSettings

    signature = inspect.signature(GitWriteStrategy)
    params = list(signature.parameters.values())
    assert [p.name for p in params] == [
        "token",
        "username",
        "repo_url",
        "managed",
        "enable_pull",
        "enable_push",
        "push_delay_s",
        "commit_name",
        "commit_email",
        "git_lfs",
        "repo_path",
        "settings",
        "subject_writer",
    ]
    assert [p.kind for p in params] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 9 + [
        inspect.Parameter.KEYWORD_ONLY
    ] * 4
    assert signature.parameters["subject_writer"].default is None
    settings_fields = {field.name: field for field in fields(GitWriteSettings)}
    for param in params[:10]:
        assert settings_fields[param.name].default == param.default
    settings = GitWriteSettings(token="secret")
    assert "secret" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.token = "other"  # type: ignore[misc]
