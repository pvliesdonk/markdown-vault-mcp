"""Local search must not bootstrap a server's managed checkout (#1999)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from markdown_vault_mcp.config_sections import _assembly

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "token-only"])
def test_search_reads_shared_index_without_git_sync(
    stored_reader_index: Path, monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    """A Git discovery refusal cannot prevent a read or enable remote sync."""
    before = stored_reader_index.read_bytes()
    if managed:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://example.com/synthetic.git"
        )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "synthetic-token")
    # Git's dubious-ownership refusal is represented by no discovered root.
    # This reproduces the bootstrap outcome without changing filesystem owners.
    with (
        patch("markdown_vault_mcp.git.bootstrap._find_git_root", return_value=None),
        patch.object(
            _assembly, "_build_git_strategy", wraps=_assembly._build_git_strategy
        ) as build_git,
    ):
        result = CliRunner().invoke(app, ["search", "Alpha", "-n", "3", "--json"])
        assert result.exit_code == 0, repr(result.exception)
        assert json.loads(result.stdout)[0]["path"] == "alpha.md"
        assert build_git.call_args.kwargs["managed"] is False
        assert build_git.call_args.kwargs["enable_sync"] is False
        assert build_git.call_args.kwargs["token"] is None
        if managed:
            admin = CliRunner().invoke(app, ["admin", "index"])
            assert admin.exit_code != 0
            assert "Managed mode requires SOURCE_DIR" in str(admin.exception)
    assert stored_reader_index.read_bytes() == before
