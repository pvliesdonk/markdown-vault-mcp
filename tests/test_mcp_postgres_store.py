"""The MCP server with its embeddings in PostgreSQL, end to end (#1504)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.conftest import (
    MockEmbeddingProvider,
    _parse_tool_data,
    wait_for_mcp_writer_drain,
)
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.postgres import PostgresSchema

_CLEAR = (
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER",
    "MARKDOWN_VAULT_MCP_EMBEDDING_SEARCH",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
)


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=16)
        self.texts = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts += len(texts)
        return super().embed(texts)


def _env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dsn: str, schema: str
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    (vault / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    for var in _CLEAR:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_STORE", "postgres")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_DSN", dsn)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_SCHEMA", schema)


async def _semantic(client: Client, query: str) -> list[str]:
    result = await client.call_tool("search", {"query": query, "mode": "semantic"})
    return [hit["path"] for hit in _parse_tool_data(result)]


async def test_the_server_searches_postgres_across_a_restart(
    tmp_path: Path, postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path, postgres_schema.dsn, postgres_schema.name)
    provider = _Counting()
    with patch(
        "markdown_vault_mcp.providers.get_embedding_provider", return_value=provider
    ):
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            assert "alpha.md" in await _semantic(client, "pears")
            status = _parse_tool_data(await client.call_tool("embeddings_status", {}))
        embedded = provider.texts
        async with Client(make_server()) as client:  # a restart
            await wait_for_mcp_writer_drain(client)
            assert "alpha.md" in await _semantic(client, "pears")
    assert provider.texts == embedded + 1  # only the second query
    assert (status["backend"], status["path"], status["search_mode"]) == (
        "postgres",
        None,
        "streamed",
    )
    assert status["store_details"]["schema"] == postgres_schema.name
    assert postgres_schema.dsn not in json.dumps(status)


async def test_a_search_while_postgres_is_unreachable_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, tmp_path, "host=127.0.0.1 port=1 connect_timeout=2", "vault_a")
    with patch(
        "markdown_vault_mcp.providers.get_embedding_provider",
        return_value=MockEmbeddingProvider(dim=16),
    ):
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="Retry later"):  # a fault, not []
                await client.call_tool("search", {"query": "pears", "mode": "semantic"})


def test_the_docker_image_installs_the_postgres_extra() -> None:
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    extras = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    packages = {
        spec.split("[")[0].split(">")[0].split("=")[0].strip() for spec in extras["all"]
    }
    assert {"psycopg", "psycopg-pool", "pgvector"} <= packages
    dockerfile = (root / "Dockerfile").read_text()
    assert (
        dockerfile.count("--extra all") >= 2
    )  # the dependency layer and the project install
