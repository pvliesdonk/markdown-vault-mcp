"""Operator selection of the optional Git backend."""

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.exceptions import ConfigurationError


def test_backend_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", raising=False)
    assert ProjectConfig.from_env().git_backend == "cli"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_BACKEND", "libgit2")
    assert ProjectConfig.from_env().git_backend == "libgit2"
    with pytest.raises(ConfigurationError, match="GIT_BACKEND"):
        ProjectConfig(git_backend="invalid")
