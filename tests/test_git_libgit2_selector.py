"""Operator selection of the optional Git backend."""

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import _resolve_git
from markdown_vault_mcp.config_sections.git import GitConfig
from markdown_vault_mcp.exceptions import ConfigurationError


def test_backend_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", raising=False)
    assert ProjectConfig.from_env().git.backend == "cli"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", "libgit2")
    assert ProjectConfig.from_env().git.backend == "libgit2"
    with pytest.raises(ConfigurationError, match="GIT_BACKEND"):
        ProjectConfig(git_backend="invalid")
    with pytest.raises(ConfigurationError, match="backend"):
        GitConfig(backend="invalid")


def test_selector_builds_native_commit_only(tmp_path) -> None:
    from markdown_vault_mcp.git.libgit2_sync import Libgit2SyncStore

    strategy, interval = _resolve_git(
        ProjectConfig(source_dir=tmp_path, git_backend="libgit2")
    )
    assert isinstance(strategy, Libgit2SyncStore)
    assert interval == 0
    strategy.close()
