"""Tests for VaultTransferSink — the domain hook behind pvl-core's transfer (#979).

The sink is the only piece markdown-vault-mcp owns now that the capability-link
route, token store, and generic tools live in ``fastmcp_pvl_core``. These tests
exercise ``validate`` / ``read`` / ``write`` directly against a writable vault,
covering the note/attachment split, path validation, and the failure paths the
old in-memory subsystem's tests covered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp_pvl_core import TransferResourceGoneError, TransferUnavailableError

from markdown_vault_mcp._transfer_sink import VaultTransferSink
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_PNG = b"\x89PNG\r\n\x1a\nDATA"


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "vault"
    src.mkdir()
    (src / "note.md").write_text("# Hello\n\nbody text\n", encoding="utf-8")
    (src / "pic.png").write_bytes(_PNG)
    return src


@pytest.fixture
def config(source_dir: Path) -> ProjectConfig:
    return ProjectConfig(
        source_dir=source_dir, read_only=False, attachment_extensions=("png",)
    )


@pytest.fixture
def vault(source_dir: Path) -> Iterator[Vault]:
    col = Vault(source_dir=source_dir, read_only=False, attachment_extensions=["png"])
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


@pytest.fixture
def sink(config: ProjectConfig, vault: Vault) -> VaultTransferSink:
    return VaultTransferSink(config, vault_provider=lambda: vault)


# --- validate -------------------------------------------------------------


async def test_validate_download_note(sink: VaultTransferSink) -> None:
    assert await sink.validate("note.md", "download") == "note.md"


async def test_validate_download_attachment(sink: VaultTransferSink) -> None:
    assert await sink.validate("pic.png", "download") == "pic.png"


async def test_validate_download_missing_note_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match="not found"):
        await sink.validate("ghost.md", "download")


async def test_validate_download_traversal_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match=r"traversal|escape|outside|Invalid"):
        await sink.validate("../secret.png", "download")


async def test_validate_download_missing_attachment_raises(
    sink: VaultTransferSink,
) -> None:
    # A non-.md path that does not exist is rejected as not found.
    with pytest.raises(ValueError, match=r"not found"):
        await sink.validate("evil.exe", "download")


async def test_validate_download_existing_bad_extension_raises(
    sink: VaultTransferSink, source_dir: Path
) -> None:
    # An existing non-.md file whose extension is not allowed is rejected on the
    # extension check (a path distinct from the missing-file rejection).
    (source_dir / "data.bin").write_bytes(b"x")
    with pytest.raises(ValueError, match=r"extension"):
        await sink.validate("data.bin", "download")


async def test_validate_upload_note(sink: VaultTransferSink) -> None:
    assert await sink.validate("new/dest.md", "upload") == "new/dest.md"


async def test_validate_upload_attachment(sink: VaultTransferSink) -> None:
    assert await sink.validate("img/new.png", "upload") == "img/new.png"


async def test_validate_upload_traversal_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match=r"traversal|escape|outside|Invalid"):
        await sink.validate("../evil.png", "upload")


async def test_validate_upload_bad_extension_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match="extension"):
        await sink.validate("report.pdf", "upload")


# --- read -----------------------------------------------------------------


async def test_read_note_serves_raw_content(sink: VaultTransferSink) -> None:
    result = await sink.read("note.md")
    assert result.body == b"# Hello\n\nbody text\n"
    assert "text/markdown" in result.media_type
    assert result.filename == "note.md"


async def test_read_attachment_serves_bytes(sink: VaultTransferSink) -> None:
    result = await sink.read("pic.png")
    assert result.body == _PNG
    assert "png" in result.media_type
    assert result.filename == "pic.png"


async def test_read_missing_note_raises_gone(sink: VaultTransferSink) -> None:
    # validate checks existence at mint time; a note removed before download
    # surfaces as TransferResourceGoneError → 410 (not a generic 500).
    with pytest.raises(TransferResourceGoneError) as exc:
        await sink.read("ghost.md")
    assert exc.value.status_code == 410


async def test_read_missing_attachment_raises_gone(sink: VaultTransferSink) -> None:
    with pytest.raises(TransferResourceGoneError) as exc:
        await sink.read("ghost.png")
    assert exc.value.status_code == 410


async def test_read_vault_unavailable_raises_503(config: ProjectConfig) -> None:
    # The vault is torn down (ref-counted lifespan) while the route stays
    # mounted: a link followed then gets a retryable 503, not a 500.
    def _torn_down() -> Vault:
        raise RuntimeError("Vault not initialised — Service.start was never called.")

    sink = VaultTransferSink(config, vault_provider=_torn_down)
    with pytest.raises(TransferUnavailableError) as exc:
        await sink.read("note.md")
    assert exc.value.status_code == 503


# --- write ----------------------------------------------------------------


async def test_write_note_commits(sink: VaultTransferSink, vault: Vault) -> None:
    payload = await sink.write("uploaded.md", b"# Uploaded\n\ntext\n")
    wait_for_writer_drain(vault)
    assert payload == {"path": "uploaded.md", "bytes": len(b"# Uploaded\n\ntext\n")}
    note = vault.reader.read("uploaded.md")
    assert note is not None
    assert "# Uploaded" in note.content


async def test_write_attachment_commits(sink: VaultTransferSink, vault: Vault) -> None:
    payload = await sink.write("shot.png", _PNG)
    wait_for_writer_drain(vault)
    assert payload["path"] == "shot.png"
    assert payload["bytes"] == len(_PNG)
    assert vault.reader.read_attachment("shot.png").size_bytes == len(_PNG)


async def test_write_note_strips_bom(sink: VaultTransferSink, vault: Vault) -> None:
    await sink.write("bom.md", b"\xef\xbb\xbf# BOM\n")
    wait_for_writer_drain(vault)
    note = vault.reader.read("bom.md")
    assert note is not None
    assert note.content.startswith("# BOM")


async def test_write_note_invalid_utf8_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(UnicodeDecodeError):
        await sink.write("bad.md", b"\xff\xfe\x00garbage")


async def test_write_vault_unavailable_raises_503(config: ProjectConfig) -> None:
    def _torn_down() -> Vault:
        raise RuntimeError("Vault not initialised — Service.start was never called.")

    sink = VaultTransferSink(config, vault_provider=_torn_down)
    with pytest.raises(TransferUnavailableError) as exc:
        await sink.write("uploaded.md", b"# x\n")
    assert exc.value.status_code == 503


# --- OKF bundle download ref (#963) ---------------------------------------


async def test_validate_bundle_ref_whole_vault(sink: VaultTransferSink) -> None:
    assert await sink.validate("okf-bundle", "download") == "okf-bundle"


async def test_validate_bundle_ref_folder(
    sink: VaultTransferSink, source_dir: Path
) -> None:
    (source_dir / "guides").mkdir()
    assert await sink.validate("okf-bundle:guides", "download") == "okf-bundle:guides"


async def test_validate_bundle_missing_folder_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match=r"not found"):
        await sink.validate("okf-bundle:nope", "download")


async def test_validate_bundle_traversal_raises(sink: VaultTransferSink) -> None:
    with pytest.raises(ValueError, match=r"traversal"):
        await sink.validate("okf-bundle:../secret", "download")


async def test_validate_bundle_rejected_when_okf_off(source_dir: Path) -> None:
    config = ProjectConfig(
        source_dir=source_dir,
        read_only=False,
        attachment_extensions=("png",),
        okf_mode="off",
    )
    sink = VaultTransferSink(config)
    with pytest.raises(ValueError, match=r"disabled"):
        await sink.validate("okf-bundle", "download")


async def test_read_bundle_serves_zip(sink: VaultTransferSink) -> None:
    import io
    import zipfile

    result = await sink.read("okf-bundle")
    assert result.media_type == "application/zip"
    assert result.filename == "okf-bundle.zip"
    names = zipfile.ZipFile(io.BytesIO(result.body)).namelist()
    assert "note.md" in names


async def test_read_bundle_missing_folder_raises_gone(sink: VaultTransferSink) -> None:
    # A folder scope valid at mint time but deleted before the one-time download
    # yields 410 (matching the single-file path), not an empty zip.
    with pytest.raises(TransferResourceGoneError) as exc:
        await sink.read("okf-bundle:vanished")
    assert exc.value.status_code == 410


async def test_read_bundle_folder_scope(sink: VaultTransferSink, vault: Vault) -> None:
    import io
    import zipfile

    vault.writer.write("guides/g.md", "# G\n")
    wait_for_writer_drain(vault)
    result = await sink.read("okf-bundle:guides")
    assert result.filename == "guides.zip"
    names = zipfile.ZipFile(io.BytesIO(result.body)).namelist()
    assert names == ["guides/g.md"]
