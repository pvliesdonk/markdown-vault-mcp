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
    (tmp_path / "other.md").write_text("# Other\n")
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
        assert "if_match" in tools["okf_author"].input_schema["required"]
        note = await client.call_tool("read", {"path": "note.md"})
        from tests.conftest import _parse_tool_data

        etag = _parse_tool_data(note)["etag"]
        other = _parse_tool_data(await client.call_tool("read", {"path": "other.md"}))
        await client.call_tool(
            "okf_author",
            {
                "path": "other.md",
                "if_match": other["etag"],
                "fields": {"title": "Other"},
            },
        )
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
    other_text = (tmp_path / "other.md").read_text()
    assert "secret-subject" not in other_text
    assert "generated" not in frontmatter.loads(other_text).metadata
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]


def test_author_keeps_comments_inside_replaced_block(tmp_path: Path) -> None:
    original = (
        "---\nsources:\n  # context\n  - resource: old # old source\n"
        "# next field comment\ncustom: yes\n---\nBody\n"
    )
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"sources": [{"resource": "new"}]}
        )
        text = (tmp_path / "note.md").read_text()
        for comment in ("# context", "# old source", "# next field comment"):
            assert comment in text
        assert frontmatter.loads(text).metadata["sources"] == [{"resource": "new"}]
    finally:
        vault.close()


@pytest.mark.parametrize(
    "producer", ["", "human:", "process:", "/1", "agent/", "bad", " a/1"]
)
def test_invalid_actor_refused_without_write(tmp_path: Path, producer: str) -> None:
    (tmp_path / "note.md").write_text("# Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={}, producer=producer
            )
        assert (tmp_path / "note.md").read_text() == "# Body\n"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "text",
    [
        "---\ntype: a\ntype: b\n---\nBody\n",
        "---\nshared: &shared value\ntitle: *shared\n---\nBody\n",
        "---\ngenerated: invalid\n---\nBody\n",
    ],
)
def test_ambiguous_frontmatter_is_not_rewritten(tmp_path: Path, text: str) -> None:
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md",
                if_match=note.etag,
                fields={"title": "new"},
                producer="agent/1",
            )
        assert (tmp_path / "note.md").read_text() == text
    finally:
        vault.close()


def test_flow_generation_extensions_preserved() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\ngenerated: {by: old/1, custom: keep}\n---\nBody\n"
    result = author_text(text, {}, "process:job")
    assert frontmatter.loads(result).metadata["generated"]["custom"] == "keep"
    assert result.endswith("Body\n")


@pytest.mark.anyio
async def test_full_registry_author_metadata() -> None:
    from fastmcp import FastMCP

    from markdown_vault_mcp.tools import register_tools

    mcp = FastMCP("registry")
    register_tools(mcp)
    tool = next(t for t in await mcp._list_tools() if t.name == "okf_author")
    assert {"write", "okf", "okf-author"} <= tool.tags
    assert tool.annotations and tool.annotations.title
    assert tool.icons
    assert "if_match" in tool.parameters["required"]


@pytest.mark.anyio
async def test_inactive_vault_refuses_authoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "note.md").write_text("# Body\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:
        etag = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))[
            "etag"
        ]
        with pytest.raises(ToolError):
            await client.call_tool(
                "okf_author",
                {"path": "note.md", "if_match": etag, "fields": {"type": "Concept"}},
            )
    assert (tmp_path / "note.md").read_text() == "# Body\n"


@pytest.mark.parametrize(
    "text",
    [
        "---\nbad: [\n---\nBody\n",
        "---\n[one, two]\n---\nBody\n",
        "---\n{type: old}\n---\nBody\n",
        "---\nunterminated\n",
    ],
)
def test_unsupported_frontmatter_refused(text: str) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError):
        author_text(text, {"type": "New"}, None)


def test_empty_frontmatter_and_unsupported_field() -> None:
    from markdown_vault_mcp._okf_author import author_text

    assert (
        frontmatter.loads(
            author_text("---\n---\nBody\n", {"type": "Guide"}, None)
        ).metadata["type"]
        == "Guide"
    )
    with pytest.raises(InvalidRequestError):
        author_text("# Body\n", {"verified": []}, None)


def test_reserved_and_missing_notes_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import DocumentNotFoundError

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author("index.md", if_match="etag", fields={})
        with pytest.raises(DocumentNotFoundError):
            vault.writer.okf_author("missing.md", if_match="etag", fields={})
    finally:
        vault.close()


def test_generated_block_value_keeps_comment_and_neighbor() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\ngenerated:\n  by:\n    - old/1 # earlier\n  custom: keep\n---\nBody\n"
    result = author_text(text, {}, "new/1")
    assert "# earlier" in result
    assert frontmatter.loads(result).metadata["generated"]["custom"] == "keep"
