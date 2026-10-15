"""A vault on EMBEDDING_STORE=sqlite (#1497)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=16)
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed(texts)


def test_a_vault_on_the_sqlite_store_keeps_its_vectors_across_a_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    provider = _Counting()

    def vault() -> Vault:
        return Vault(
            source_dir=root,
            settings=VaultSettings(
                index_path=tmp_path / "index.db",
                read_only=False,
                embedding_store="sqlite",
            ),
            embedding_provider=provider,
        )

    first = vault()
    try:
        first.index.build_index()
        first.index.build_embeddings()
        (root / "alpha.md").write_text("# Alpha\n\nQuinces and medlars.\n")
        first.index.reindex()
    finally:
        first.close()
    assert (tmp_path / "index-db-embeddings.sqlite3").is_file()
    assert not (tmp_path / "index-db-embeddings.json").exists()

    calls = provider.calls
    second = vault()
    try:
        second.index.build_index()
        assert second.index.build_embeddings() == 0
        assert provider.calls == calls
        store = second._vectors
        assert store is not None
        contents = [m["content"] for m in metadata_by_path(store)["alpha.md"]]
        assert any("Quinces" in c for c in contents)
        assert second.reader.search("medlars", mode="semantic")
    finally:
        second.close()


def test_importing_the_vault_registers_every_backend() -> None:
    """A fresh interpreter, so no other test module's imports register them."""
    import subprocess
    import sys

    script = (
        "import markdown_vault_mcp.vault\n"
        "from markdown_vault_mcp.stores.registry import backend_names\n"
        "print(backend_names('vector'), backend_names('keyword'))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "('numpy', 'sqlite') ('sqlite',)"
