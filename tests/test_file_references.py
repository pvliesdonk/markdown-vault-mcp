"""Portable references identify raw bytes independently of Git or mount paths."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.exceptions import (
    ConfigurationError,
    DocumentUnreadableError,
    InvalidRequestError,
)
from markdown_vault_mcp.references import FileReference, ReferenceReader
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO


def test_raw_bytes_roundtrip_and_change(tmp_path: Path) -> None:
    raw = b"---\r\ntitle: caf\xc3\xa9\r\n---\r\n# Evidence\r\n"
    file = tmp_path / "evidence.md"
    file.write_bytes(raw)
    reader = ReferenceReader(tmp_path, "urn:vault:research")
    ref = reader.get_file_reference("evidence.md", include_sha256=True)
    assert ref.sha256 == hashlib.sha256(raw).hexdigest()
    assert ref.size_bytes == len(raw)
    assert ref.provenance is None
    result = reader.resolve_file_reference(ref, include_content=True)
    assert result.status == "verified"
    assert base64.b64decode(result.content_base64 or "") == raw
    # A same-length replacement must not pass the content check.
    file.write_bytes(raw.replace(b"Evidence", b"Changed!"))
    changed = reader.resolve_file_reference(ref, include_content=True)
    assert changed.status == "changed"
    assert changed.content_base64 is None


def test_binary_and_unhashed_references(tmp_path: Path) -> None:
    raw = b"\x00\xff\r\n"
    (tmp_path / "proof.png").write_bytes(raw)
    reader = ReferenceReader(tmp_path, "evidence")
    ref = reader.get_file_reference("proof.png", provenance="task:42")
    assert ref.sha256 is None and ref.provenance == "task:42"
    assert reader.resolve_file_reference(ref).status == "unverified"
    with pytest.raises(InvalidRequestError, match="SHA-256"):
        reader.resolve_file_reference(ref, include_content=True)
    hashed = reader.get_file_reference("proof.png", include_sha256=True)
    result = reader.resolve_file_reference(hashed, include_content=True)
    assert base64.b64decode(result.content_base64 or "") == raw


def test_missing_renamed_wrong_vault_and_relocated_root(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir()
    (original / "note.md").write_bytes(b"original")
    ref = ReferenceReader(original, "a").get_file_reference(
        "note.md", include_sha256=True
    )
    relocated = tmp_path / "relocated"
    original.rename(relocated)
    reader = ReferenceReader(relocated, "a")
    assert reader.resolve_file_reference(ref).status == "verified"
    assert (
        ReferenceReader(relocated, "b").resolve_file_reference(ref).status
        == "wrong_vault"
    )
    (relocated / "note.md").rename(relocated / "renamed.md")
    missing = reader.resolve_file_reference(ref, include_content=True)
    assert missing.status == "missing"
    assert "renamed" in missing.detail and "retained" in missing.detail
    assert missing.content_base64 is None
    with pytest.raises(InvalidRequestError, match="list_documents"):
        reader.get_file_reference("note.md")


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        "/absolute.md",
        "./note.md",
        "a/../note.md",
        "",
        "a\\b.md",
        "nul\x00.md",
    ],
)
def test_nonportable_paths_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(InvalidRequestError):
        ReferenceReader(tmp_path, "a").get_file_reference(path)


def test_allowlist_symlink_and_nonregular_rules(tmp_path: Path) -> None:
    reader = ReferenceReader(tmp_path, "a", attachment_extensions=["png"])
    (tmp_path / "secret.txt").write_bytes(b"secret")
    with pytest.raises(InvalidRequestError, match="configured attachment"):
        reader.get_file_reference("secret.txt")
    with pytest.raises(InvalidRequestError, match="configured attachment"):
        reader.get_file_reference("secret.txt", include_sha256=True)
    (tmp_path / "folder.md").mkdir()
    with pytest.raises(InvalidRequestError, match="regular file"):
        reader.get_file_reference("folder.md")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_bytes(b"outside")
    (tmp_path / "link.md").symlink_to(outside)
    with pytest.raises(InvalidRequestError, match="traversal"):
        reader.get_file_reference("link.md")


def test_optional_content_cap_and_empty_file(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_bytes(b"abcd")
    reader = ReferenceReader(tmp_path, "a")
    ref = reader.get_file_reference("note.md", include_sha256=True)
    assert reader.resolve_file_reference(ref, max_content_bytes=1).status == "verified"
    with pytest.raises(InvalidRequestError, match="limit"):
        reader.resolve_file_reference(ref, include_content=True, max_content_bytes=1)
    assert (
        reader.resolve_file_reference(
            ref, include_content=True, max_content_bytes=0
        ).content_base64
        == "YWJjZA=="
    )
    (tmp_path / "empty.md").write_bytes(b"")
    empty = reader.get_file_reference("empty.md", include_sha256=True)
    assert empty.size_bytes == 0
    assert (
        reader.resolve_file_reference(empty, include_content=True).content_base64 == ""
    )


def test_reference_validation_and_namespace_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for changes in [
        {"version": 2},
        {"sha256": "not-a-sha"},
        {"size_bytes": -1},
        {"namespace": " "},
        {"provenance": "x" * 1025},
    ]:
        with pytest.raises(InvalidRequestError):
            FileReference(
                **({"namespace": "a", "path": "note.md", "size_bytes": 0} | changes)
            )
    with pytest.raises(InvalidRequestError, match="namespace"):
        ReferenceReader(tmp_path, None)
    with pytest.raises(ConfigurationError, match="reference_namespace"):
        ProjectConfig(reference_namespace=" ")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REFERENCE_NAMESPACE", "urn:vault:test")
    assert ProjectConfig.from_env().reference_namespace == "urn:vault:test"


def test_concurrent_replacement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "note.md"
    path.write_bytes(b"before")
    reader = ReferenceReader(tmp_path, "a")
    original = reader._check_stable

    def replace_before_check(*args: object) -> None:
        alternate = tmp_path / "replacement"
        alternate.write_bytes(b"after!")
        alternate.replace(path)
        original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(reader, "_check_stable", replace_before_check)
    with pytest.raises(InvalidRequestError, match="changed during"):
        reader.get_file_reference("note.md", include_sha256=True)


async def test_mcp_reference_roundtrip_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_bytes(b"# Evidence\r\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    # Explicit config must win over the environment.
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REFERENCE_NAMESPACE", "wrong")
    server = make_server(
        config=replace(ProjectConfig.from_env(), reference_namespace="right")
    )
    async with Client(server) as client:
        tools = {t.name: t for t in await client.list_tools()}
        for name in ["get_file_reference", "resolve_file_reference"]:
            assert tools[name].annotations.title
            assert tools[name].annotations.read_only_hint
            assert tools[name].annotations.idempotent_hint
            assert tools[name].icons
        reply = await client.call_tool(
            "get_file_reference", {"path": "note.md", "include_sha256": True}
        )
        ref = reply.structured_content
        assert ref["namespace"] == "right"
        assert "content" not in ref
        result = await client.call_tool(
            "resolve_file_reference", {"reference": ref, "include_content": True}
        )
        assert result.structured_content["status"] == "verified"
        assert (
            base64.b64decode(result.structured_content["content_base64"])
            == b"# Evidence\r\n"
        )
        wrong = await client.call_tool(
            "resolve_file_reference", {"reference": ref | {"namespace": "other"}}
        )
        assert wrong.structured_content["status"] == "wrong_vault"
        with pytest.raises(ToolError, match="list_documents"):
            await client.call_tool("get_file_reference", {"path": "absent.md"})


@pytest.mark.parametrize("mutation", ["inplace", "symlink", "truncated"])
def test_mutation_during_stream_is_refused(tmp_path: Path, mutation: str) -> None:
    file = tmp_path / "note.md"
    file.write_bytes(b"before")
    (tmp_path / "other.md").write_bytes(b"after!")
    reader = ReferenceReader(tmp_path, "a")
    consume = reader._consume

    def mutate(
        stream: BinaryIO, hash_bytes: bool, cap: int | None
    ) -> tuple[int, str | None, bytes | None]:
        result = consume(stream, hash_bytes, cap)
        if mutation == "symlink":
            file.unlink()
            file.symlink_to(tmp_path / "other.md")
        else:
            file.write_bytes(b"after!" if mutation == "inplace" else b"")
        return result

    with (
        patch.object(reader, "_consume", side_effect=mutate),
        pytest.raises(InvalidRequestError, match="changed during"),
    ):
        reader.get_file_reference("note.md", include_sha256=True)


def test_nonregular_io_and_stream_growth_are_safe(tmp_path: Path) -> None:
    reader = ReferenceReader(tmp_path, "a")
    os.mkfifo(tmp_path / "pipe.md")
    with pytest.raises(InvalidRequestError, match="regular file"):
        reader.get_file_reference("pipe.md", include_sha256=True)
    with (
        patch("markdown_vault_mcp.references.os.open", side_effect=PermissionError),
        pytest.raises(DocumentUnreadableError),
    ):
        reader.get_file_reference("note.md", include_sha256=True)
    size, digest, body = reader._consume(io.BytesIO(b"abcd"), True, 1)
    assert size == 4 and digest == hashlib.sha256(b"abcd").hexdigest()
    assert body is None
    with pytest.raises(InvalidRequestError, match="nonnegative"):
        reader.resolve_file_reference(
            FileReference("a", "note.md", 0), max_content_bytes=-1
        )


async def test_unconfigured_namespace_and_content_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "note.md").write_bytes(b"abcdef")
    (tmp_path / "attachment.png").write_bytes(b"abcdef")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    config = ProjectConfig.from_env()
    async with Client(make_server(config=config)) as client:
        with pytest.raises(ToolError, match="namespace"):
            await client.call_tool("get_file_reference", {"path": "note.md"})
    caplog.clear()
    config = replace(
        config,
        reference_namespace="a",
        max_note_read_bytes=1,
        max_attachment_size_mb=0.000001,
    )
    async with Client(make_server(config=config)) as client:
        for path in ["note.md", "attachment.png"]:
            reply = await client.call_tool(
                "get_file_reference", {"path": path, "include_sha256": True}
            )
            ref = reply.structured_content
            with pytest.raises(ToolError, match="limit"):
                await client.call_tool(
                    "resolve_file_reference",
                    {"reference": ref, "include_content": True},
                )
        assert not [r for r in caplog.records if r.levelno >= 40]


def test_short_stream_cannot_publish_a_complete_digest(tmp_path: Path) -> None:
    """Even stable file metadata cannot make a short byte stream complete."""
    (tmp_path / "note.md").write_bytes(b"complete")
    reader = ReferenceReader(tmp_path, "a")
    with (
        patch.object(
            reader,
            "_consume",
            return_value=(2, hashlib.sha256(b"co").hexdigest(), None),
        ),
        pytest.raises(InvalidRequestError, match="changed during"),
    ):
        reader.get_file_reference("note.md", include_sha256=True)


@pytest.mark.parametrize("replacement", [b"new", b"new content"])
def test_changed_reference_wins_over_body_cap(
    tmp_path: Path, replacement: bytes
) -> None:
    """A changed oversized body is an identity outcome, never a read-cap error."""
    file = tmp_path / "note.md"
    file.write_bytes(b"old")
    reader = ReferenceReader(tmp_path, "a")
    saved = reader.get_file_reference("note.md", include_sha256=True)
    file.write_bytes(replacement)
    result = reader.resolve_file_reference(
        saved, include_content=True, max_content_bytes=1
    )
    assert result.status == "changed"
    assert result.content_base64 is None


async def test_mcp_changed_oversized_file_reports_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    config = replace(
        ProjectConfig.from_env(),
        reference_namespace="a",
        max_note_read_bytes=1,
        max_attachment_size_mb=0.000001,
    )
    async with Client(make_server(config=config)) as client:
        for path in ["note.md", "proof.png"]:
            file = tmp_path / path
            file.write_bytes(b"old")
            saved = (
                await client.call_tool(
                    "get_file_reference", {"path": path, "include_sha256": True}
                )
            ).structured_content
            file.write_bytes(b"new content")
            result = (
                await client.call_tool(
                    "resolve_file_reference",
                    {"reference": saved, "include_content": True},
                )
            ).structured_content
            assert result["status"] == "changed"
            assert result["content_base64"] is None
