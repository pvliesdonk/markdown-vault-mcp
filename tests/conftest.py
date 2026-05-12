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
def populated_collection(tmp_path: Path):
    """A small Collection with one doc that has 'foo' in multiple sections.

    Used by test_search_grouping and other tests that exercise the
    field-collapsed shape with multi-chunk documents.
    """
    from markdown_vault_mcp.collection import Collection

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
    col = Collection(
        source_dir=vault,
        embedding_provider=MockEmbeddingProvider(),
        embeddings_path=tmp_path / "vectors",
    )
    col.build_index()
    col.build_embeddings()
    return col


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


@pytest.fixture
def vault_path(tmp_path: Path, fixtures_path: Path) -> Path:
    """Copy fixtures into a temp directory.

    Excludes ``invalid_utf8.md`` (non-UTF-8 bytes) so that Collection tests
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
