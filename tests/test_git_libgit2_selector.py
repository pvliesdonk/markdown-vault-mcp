"""Operator selection of the optional Git backend."""

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import _resolve_git
from markdown_vault_mcp.exceptions import ConfigurationError


def test_backend_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", raising=False)
    assert ProjectConfig.from_env().git_backend == "cli"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", "libgit2")
    assert ProjectConfig.from_env().git_backend == "libgit2"
    with pytest.raises(ConfigurationError, match="GIT_BACKEND"):
        ProjectConfig(git_backend="invalid")


def test_selector_builds_native_commit_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pygit2

    from markdown_vault_mcp.git.libgit2_sync import Libgit2SyncStore

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = pygit2.init_repository(str(tmp_path), initial_head="main")
    repo.config["commit.gpgsign"] = False
    repo.config["core.hooksPath"] = str(tmp_path / "empty-hooks")
    strategy, interval = _resolve_git(
        ProjectConfig(source_dir=tmp_path, git_backend="libgit2")
    )
    assert isinstance(strategy, Libgit2SyncStore)
    assert interval == 0
    strategy.close()
