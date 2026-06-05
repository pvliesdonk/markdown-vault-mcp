"""Shared test fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from markdown_vault_mcp.providers import EmbeddingProvider

# Re-export reusable fixtures so they are auto-discovered by pytest in any
# test module without requiring a per-file import (which would trip ruff's
# F811 redefinition check on the parameter shadowing).
from tests.fixtures.git import git_repo_pair  # noqa: F401


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic provider for testing. Returns hash-based vectors."""

    def __init__(self, dim: int = 32) -> None:
        """Initialise with a fixed vector dimension.

        Args:
            dim: Embedding dimension. Defaults to 32.
        """
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic hash-based vectors for each text.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        vectors = []
        for text in texts:
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % 2**31
            rng = np.random.RandomState(seed)
            vec = rng.randn(self._dim).tolist()
            vectors.append(vec)
        return vectors

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Returns:
            Integer dimension of each embedding vector.
        """
        return self._dim

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return f"mock-dim-{self._dim}"


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_provider() -> MockEmbeddingProvider:
    """Mock embedding provider for testing."""
    return MockEmbeddingProvider()


@pytest.fixture
def populated_vault(tmp_path: Path):
    """A small Vault with one doc that has 'foo' in multiple sections.

    Used by test_search_grouping and other tests that exercise the
    field-collapsed shape with multi-chunk documents.
    """
    from markdown_vault_mcp.vault import Vault

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "multi.md").write_text(
        "# A\n\n"
        + ("foo something foo\n" * 12)
        + "\n## B\n\n"
        + ("foo elsewhere foo\n" * 12)
        + "\n## C\n\nfoo third section foo\n"
    )
    (vault / "other.md").write_text("# Other\n\n" + ("baz baz baz\n" * 12))
    col = Vault(
        source_dir=vault,
        embedding_provider=MockEmbeddingProvider(),
        embeddings_path=tmp_path / "vectors",
    )
    try:
        col.index.build_index()
        col.index.build_embeddings()
        yield col
    finally:
        col.close()


@pytest.fixture
def built(vault_path: Path):
    """A built Vault over the clean vault fixture (shared by facet tests)."""
    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=vault_path)
    col.index.build_index()
    try:
        yield col
    finally:
        col.close()


async def get_app_html() -> str:
    """Spin up a fresh server and fetch the SPA HTML resource."""
    from fastmcp import Client

    from markdown_vault_mcp.server import make_server

    server = make_server()
    async with Client(server) as client:
        resource = await client.read_resource("ui://vault/app.html")
        return resource[0].text if hasattr(resource[0], "text") else str(resource[0])


# Shared env-var hygiene for MCP Apps tests — keep make_server() reads
# deterministic by stripping host-leaked configuration.
_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    "MARKDOWN_VAULT_MCP_APP_DOMAIN",
)


@pytest.fixture
def _mcp_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal env vars for make_server()."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


def wait_for_writer_drain(col: object, timeout: float = 5.0) -> None:
    """Wait for IndexWriter queue + dirty-sets to drain (#559).

    Used by Vault-level tests that need to assert FTS / vector
    state after a write / edit / delete / rename — which now go through
    the single-owner :class:`IndexWriter` and complete asynchronously.

    Args:
        col: The :class:`Vault` instance under test.
        timeout: Maximum wait in seconds.

    Raises:
        AssertionError: If the writer did not drain within *timeout*.
    """
    import time

    deadline = time.monotonic() + timeout
    status: dict[str, object] = {}
    while time.monotonic() < deadline:
        status = col._coordinator.writer.get_status()  # type: ignore[attr-defined]
        if (
            status["queue_depth"] == 0
            and status["in_flight"] is None
            and status["dirty_paths"] == 0
            and status["dirty_embeddings"] == 0
        ):
            return
        time.sleep(0.01)
    msg = f"Writer did not drain in {timeout}s: {status}"
    raise AssertionError(msg)


async def wait_for_mcp_writer_drain(client: object, timeout: float = 5.0) -> None:
    """Poll a FastMCP Client until the writer has drained.

    Used by tests that drive the server via an in-process Client and
    need bucket-2 tools (search/list/stats) to return populated state
    after a cold-start lifespan (#559).

    Args:
        client: The FastMCP Client instance.
        timeout: Maximum wait in seconds.

    Raises:
        AssertionError: If drain did not complete within the timeout.
    """
    import asyncio as _asyncio

    deadline_iters = int(timeout / 0.05)
    last_status: dict = {}
    for _ in range(deadline_iters):
        status_res = await client.call_tool("get_index_status", {})  # type: ignore[attr-defined]
        status = status_res.structured_content or {}
        last_status = status
        if (
            status.get("status") == "queryable"
            and status.get("queue_depth", 0) == 0
            and status.get("in_flight") is None
            and status.get("dirty_paths", 0) == 0
            and status.get("dirty_embeddings", 0) == 0
        ):
            return
        await _asyncio.sleep(0.05)
    msg = f"Writer did not drain via MCP client in {timeout}s: {last_status}"
    raise AssertionError(msg)


@pytest.fixture
def vault_path(tmp_path: Path, fixtures_path: Path) -> Path:
    """Copy fixtures into a temp directory.

    Excludes ``invalid_utf8.md`` (non-UTF-8 bytes) so that Vault tests
    can scan the vault without errors.  ``malformed_yaml.md`` is included
    because ``scan_directory`` handles YAML parse errors gracefully.

    Returns:
        Path to the vault root inside ``tmp_path``.
    """
    import shutil

    vault = tmp_path / "vault"
    vault.mkdir()

    _EXCLUDED = {"invalid_utf8.md"}

    for src in fixtures_path.rglob("*.md"):
        if src.name in _EXCLUDED:
            continue
        rel = src.relative_to(fixtures_path)
        dest = vault / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    return vault
