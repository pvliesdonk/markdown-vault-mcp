"""A file's etag without its body: ``ReaderFacet.read_etag`` and ``read(include_content=False)`` (#2124)."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.exceptions import (
    DocumentNotFoundError,
    DocumentUnreadableError,
    InvalidRequestError,
    NoteTooLargeError,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# A byte-order mark and CRLF line endings: bytes ``content`` does not carry.
_NOTE = b"\xef\xbb\xbf---\r\ntitle: One\r\n---\r\n\r\nBody text.\r\n"
_PDF = b"%PDF-1.4 " + bytes(range(256)) * 8


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "assets").mkdir(parents=True)
    (root / "one.md").write_bytes(_NOTE)
    (root / "assets" / "report.pdf").write_bytes(_PDF)
    (root / "assets" / "script.exe").write_bytes(b"MZ")
    (root / "folder.md").mkdir()
    (root / "assets" / "dir.pdf").mkdir()
    (tmp_path / "outside.md").write_bytes(b"# Outside\n")
    (root / "escape.md").symlink_to(tmp_path / "outside.md")
    return root


@pytest.fixture
def vault(vault_dir: Path) -> Iterator[Vault]:
    capped = VaultSettings(max_note_read_bytes=8, max_attachment_size_mb=1e-6)
    v = Vault(source_dir=vault_dir, settings=capped)
    try:
        yield v
    finally:
        v.close()


@pytest.mark.parametrize(
    ("path", "raw"), [("one.md", _NOTE), ("assets/report.pdf", _PDF)]
)
def test_etag_is_raw_byte_sha256_at_any_size(
    vault: Vault, vault_dir: Path, path: str, raw: bytes
) -> None:
    """Over both caps, the etag is the raw bytes' SHA-256 and matches a full read."""
    with pytest.raises(NoteTooLargeError):
        vault.reader.read("one.md")
    result = vault.reader.read_etag(path)
    assert result.path == path
    assert result.etag == hashlib.sha256(raw).hexdigest()
    assert result.size_bytes == len(raw)
    assert result.modified_at == (vault_dir / path).stat().st_mtime


def test_etag_equals_whole_file_read_etag(vault_dir: Path) -> None:
    """Uncapped, read_etag gives exactly what read and read_attachment give."""
    v = Vault(source_dir=vault_dir)
    try:
        note = v.reader.read("one.md")
        assert note is not None
        assert v.reader.read_etag("one.md").etag == note.etag
        attachment = v.reader.read_attachment("assets/report.pdf")
        assert v.reader.read_etag("assets/report.pdf").etag == attachment.etag
    finally:
        v.close()


def test_empty_file(vault: Vault, vault_dir: Path) -> None:
    (vault_dir / "empty.md").write_bytes(b"")
    result = vault.reader.read_etag("empty.md")
    assert (result.size_bytes, result.etag) == (0, hashlib.sha256(b"").hexdigest())


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("missing.md", DocumentNotFoundError),
        ("folder.md", DocumentNotFoundError),
        ("escape.md", DocumentNotFoundError),
        ("../outside.md", DocumentNotFoundError),
        ("assets/missing.pdf", DocumentNotFoundError),
        ("assets/dir.pdf", DocumentNotFoundError),
        ("assets", InvalidRequestError),
        ("assets/script.exe", InvalidRequestError),
        ("../outside.pdf", InvalidRequestError),
        ("one\x00.md", InvalidRequestError),
    ],
)
def test_refuses_what_read_refuses(
    vault: Vault, path: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        vault.reader.read_etag(path)


def test_fifo_is_absent_not_a_hang(vault: Vault, vault_dir: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs on this platform")
    os.mkfifo(vault_dir / "pipe.md")
    with pytest.raises(DocumentNotFoundError):
        vault.reader.read_etag("pipe.md")


def test_unreadable_file(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    import pathlib

    real_open = pathlib.Path.open

    def refuse(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "one.md":
            raise PermissionError("denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", refuse)
    with pytest.raises(DocumentUnreadableError):
        vault.reader.read_etag("one.md")


@pytest.fixture
def mcp_env(vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0.000001")
    return vault_dir


@pytest.mark.usefixtures("mcp_env")
@pytest.mark.parametrize(
    ("path", "raw"), [("one.md", _NOTE), ("assets/report.pdf", _PDF)]
)
async def test_read_tool_returns_etag_without_body_over_caps(
    path: str, raw: bytes
) -> None:
    async with Client(make_server()) as client:
        with pytest.raises(ToolError):
            await client.call_tool("read", {"path": path})
        reply = await client.call_tool("read", {"path": path, "include_content": False})
    assert set(reply.data) == {"path", "size_bytes", "modified_at", "etag"}
    assert reply.data["etag"] == hashlib.sha256(raw).hexdigest()
    assert reply.data["size_bytes"] == len(raw)


@pytest.mark.usefixtures("mcp_env")
@pytest.mark.parametrize(
    ("extra", "named"),
    [({"section": "Body"}, "section 'Body'"), ({"revision": "abc123"}, "'abc123'")],
)
async def test_read_tool_refuses_partial_etag_reads(
    extra: dict[str, str], named: str
) -> None:
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match=named) as caught:
            await client.call_tool(
                "read", {"path": "one.md", "include_content": False, **extra}
            )
    assert "include_content=False but no" in str(caught.value)
