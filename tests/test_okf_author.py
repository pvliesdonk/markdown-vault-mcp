"""Explicit OKF authoring preserves unrelated note data."""

from pathlib import Path

import frontmatter
import pytest

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    InvalidRequestError,
)
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_author_preserves_evidence_and_records_explicit_producer(
    tmp_path: Path,
) -> None:
    original = (
        "---\n# keep comment\ntype: Old\nsources:\n  - resource: /source.md\n"
        "verified:\n  - by: human:reviewer\n    at: 2026-01-01\n"
        "x-custom: {keep: true}\n---\n# Body\n"
    )
    (tmp_path / "note.md").write_text(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"type": "New"}, producer="codex/6.1"
        )
        result = (tmp_path / "note.md").read_text()
        meta = frontmatter.loads(result).metadata
        assert "# keep comment" in result
        assert "sources:\n  - resource: /source.md\n" in result
        assert "verified:\n  - by: human:reviewer\n    at: 2026-01-01\n" in result
        assert "x-custom: {keep: true}" in result
        assert result.endswith("# Body\n")
        assert meta["generated"]["by"] == "codex/6.1"
        assert meta["generated"]["at"].endswith("Z")
    finally:
        vault.close()


def test_author_requires_matching_etag_and_valid_producer(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("# Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={}, producer="invalid"
            )
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author("note.md", if_match="", fields={})
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_author("note.md", if_match="stale", fields={})
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"title": "Title"}
        )
        assert (
            "generated"
            not in frontmatter.loads((tmp_path / "note.md").read_text()).metadata
        )
    finally:
        vault.close()


def test_existing_generated_extensions_and_multiline_sources_survive(
    tmp_path: Path,
) -> None:
    (tmp_path / "note.md").write_text(
        "---\ngenerated:\n  by: old/1 # attribution\n  custom: retained\n"
        "sources:\n  - resource: old\nnext: untouched\n---\nBody\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md",
            if_match=note.etag,
            fields={"sources": [{"resource": "new"}]},
            producer="new/1",
        )
        text = (tmp_path / "note.md").read_text()
        meta = frontmatter.loads(text).metadata
        assert meta["generated"]["custom"] == "retained"
        assert meta["generated"]["by"] == "new/1"
        assert "# attribution" in text
        assert meta["next"] == "untouched"
        assert meta["sources"] == [{"resource": "new"}]
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_author_visibility_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client

    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text("# Body\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "secret-subject")
    monkeypatch.setattr(
        "fastmcp_pvl_core.get_claims", lambda: {"sub": "secret-subject"}
    )
    async with Client(make_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "okf_author" in tools
        assert "if_match" in tools["okf_author"].inputSchema["required"]
        note = await client.call_tool("read", {"path": "note.md"})
        from tests.conftest import _parse_tool_data

        etag = _parse_tool_data(note)["etag"]
        await client.call_tool(
            "okf_author",
            {
                "path": "note.md",
                "if_match": etag,
                "fields": {"type": "Concept"},
                "producer": "codex/6.1",
            },
        )
    text = (tmp_path / "note.md").read_text()
    assert "secret-subject" not in text
    assert frontmatter.loads(text).metadata["generated"]["by"] == "codex/6.1"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]
