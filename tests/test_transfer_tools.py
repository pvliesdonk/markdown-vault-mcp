"""Unit tests for the transfer-link tool logic (#622)."""

import pytest
from fastmcp_pvl_core import ServerConfig

from markdown_vault_mcp import _server_transfer as T
from markdown_vault_mcp.config import VaultConfig
from markdown_vault_mcp.config_sections import ContentConfig
from markdown_vault_mcp.transfer.store import TransferStore
from markdown_vault_mcp.vault import Vault


@pytest.fixture
def env(tmp_path):
    """A writable vault + config + store with BASE_URL set."""
    src = tmp_path / "vault"
    src.mkdir()
    (src / "note.md").write_text("# N\n\nx\n")
    (src / "pic.png").write_bytes(b"DATA")
    col = Vault(source_dir=src, read_only=False, attachment_extensions=["png"])
    col.index.build_index()
    config = VaultConfig(
        source_dir=src,
        content=ContentConfig(attachment_extensions=["png"]),
        server=ServerConfig(base_url="https://host"),
    )
    store = TransferStore()
    try:
        yield store, config, col
    finally:
        col.close()


async def test_download_link_url_shape(env):
    """create_download_link returns a transfer URL and honest expiry."""
    store, config, vault = env
    out = await T._create_download_link(store, config, vault, "note.md", None)
    assert out["url"].startswith("https://host/transfer/")
    assert out["path"] == "note.md"
    assert out["expires_in_seconds"] == 3600


async def test_ttl_is_clamped_to_max(env):
    """A requested TTL above the ceiling is clamped and reported honestly."""
    store, config, vault = env
    out = await T._create_download_link(store, config, vault, "note.md", 999999)
    assert out["expires_in_seconds"] == 86400


async def test_base_url_unset_raises(env):
    """Minting fails clearly when BASE_URL is unset."""
    store, config, vault = env
    config_no_url = VaultConfig(
        source_dir=config.source_dir,
        content=ContentConfig(attachment_extensions=["png"]),
    )
    with pytest.raises(ValueError, match="BASE_URL"):
        await T._create_download_link(store, config_no_url, vault, "note.md", None)


async def test_download_missing_note_raises(env):
    """A download link for a non-existent note fails fast."""
    store, config, vault = env
    with pytest.raises(ValueError, match="not found"):
        await T._create_download_link(store, config, vault, "nope.md", None)


async def test_upload_link_attachment_ok(env):
    """create_upload_link mints an attachment token for an allowed extension."""
    store, config, vault = env
    out = await T._create_upload_link(store, config, vault, "new.png", None)
    assert out["url"].startswith("https://host/transfer/")


async def test_upload_traversal_raises(env):
    """An upload destination escaping the vault is rejected."""
    store, config, vault = env
    with pytest.raises(ValueError):
        await T._create_upload_link(store, config, vault, "../evil.md", None)


async def test_upload_bad_extension_raises(env):
    """An upload to a disallowed attachment extension is rejected at creation."""
    store, config, vault = env
    with pytest.raises(ValueError, match="extension"):
        await T._create_upload_link(store, config, vault, "bad.exe", None)


async def test_upload_attachment_traversal_raises(env):
    """An attachment destination escaping the vault is rejected."""
    store, config, vault = env
    with pytest.raises(ValueError, match="traversal"):
        await T._create_upload_link(store, config, vault, "../evil.png", None)


async def test_download_missing_attachment_raises(env):
    """A download link for a non-existent attachment fails fast."""
    store, config, vault = env
    with pytest.raises(ValueError):
        await T._create_download_link(store, config, vault, "ghost.png", None)


async def test_ttl_clamped_to_floor(env):
    """A zero or negative requested TTL is clamped up to 1 second."""
    store, config, vault = env
    out = await T._create_download_link(store, config, vault, "note.md", 0)
    assert out["expires_in_seconds"] == 1
