"""Synthetic vault shared by local and real HTTP reader-command tests."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.server import make_server
from tests.fixtures.cli_http import serve_http

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def discovery_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create two linked notes, indexed tags and one allowed binary attachment."""
    source = tmp_path / "vault"
    (source / "notes").mkdir(parents=True)
    (source / "alpha.md").write_text(
        "---\ntitle: Alpha\ntags: [shared]\n---\n# Alpha\n\nOpening.\n\n## Detail\n\nDetails here.\n",
        encoding="utf-8",
    )
    (source / "notes/beta.md").write_text(
        "---\ntitle: Beta\ntags: [other]\n---\n# Beta\n\n[Alpha](../alpha.md)\n",
        encoding="utf-8",
    )
    (source / "sample.png").write_bytes(b"\x89PNG\x00\xff\x1b[BINARY")
    os.utime(source / "alpha.md", (1700000000, 1700000000))
    os.utime(source / "notes/beta.md", (1700000001, 1700000001))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags")
    return source


@contextmanager
def reader_backend(remote: bool) -> Iterator[list[str]]:
    """Select the CLI's local adapter or a real native reader MCP server.

    Args:
        remote: Whether to start the loopback server.

    Yields:
        Connection arguments for one CLI invocation.
    """
    if not remote:
        yield ["--local"]
        return
    config = ProjectConfig.from_env()
    server = make_server(transport="http", config=config, configure_logging=False)
    with serve_http(server.http_app()) as origin:
        yield ["--server", origin + "/mcp"]


@pytest.fixture
def stored_reader_index(
    discovery_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    """Seed a completed synthetic index using the same operator configuration."""
    from markdown_vault_mcp.vault import Vault, VaultSettings

    if getattr(request, "param", False):
        for path in discovery_source.rglob("*"):
            if path.is_file():
                path.unlink()
    index = tmp_path / "shared.sqlite"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    config = ProjectConfig.from_env()
    vault = Vault(
        source_dir=discovery_source, settings=VaultSettings.from_project_config(config)
    )
    try:
        vault.index.build_index()
    finally:
        vault.close()
    return index
