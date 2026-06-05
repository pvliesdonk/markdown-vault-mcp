"""Unit tests for WriterFacet (facade-decomposition PR3a, issue #604).

Exercises the writer facet through a real Vault (real on-disk effects),
mirroring the flat-method assertions in test_vault.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.facets.writer import WriterFacet
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def writable(vault_path: Path) -> Iterator[Vault]:
    """Writable, indexed vault accepting any attachment extension."""
    col = Vault(source_dir=vault_path, read_only=False, attachment_extensions=["*"])
    col.index.build_index()
    try:
        yield col
    finally:
        col.close()


class TestWriterFacetAccessor:
    def test_accessor_returns_writer_facet(self, writable: Vault) -> None:
        assert isinstance(writable.writer, WriterFacet)

    def test_accessor_is_stable(self, writable: Vault) -> None:
        assert writable.writer is writable.writer


class TestWriterFacetBehaviour:
    def test_write_creates_document(self, writable: Vault, vault_path: Path) -> None:
        result = writable.writer.write("facet_new.md", "# New\n\nbody text.\n")
        assert result.path == "facet_new.md"
        assert "body text" in (vault_path / "facet_new.md").read_text()

    def test_edit_patches_document(self, writable: Vault, vault_path: Path) -> None:
        writable.writer.write("facet_edit.md", "alpha beta\n")
        writable.writer.edit("facet_edit.md", old_text="alpha", new_text="gamma")
        assert "gamma" in (vault_path / "facet_edit.md").read_text()

    def test_delete_removes_document(self, writable: Vault) -> None:
        writable.writer.write("facet_del.md", "x\n")
        writable.writer.delete("facet_del.md")
        assert writable.reader.read("facet_del.md") is None

    def test_rename_moves_document(self, writable: Vault) -> None:
        writable.writer.write("facet_a.md", "x\n")
        writable.writer.rename("facet_a.md", "facet_b.md")
        assert writable.reader.read("facet_a.md") is None
        assert writable.reader.read("facet_b.md") is not None

    def test_write_attachment_creates_file(
        self, writable: Vault, vault_path: Path
    ) -> None:
        result = writable.writer.write_attachment("facet.bin", b"\x00\x01\x02")
        assert result.path == "facet.bin"
        assert (vault_path / "facet.bin").read_bytes() == b"\x00\x01\x02"
