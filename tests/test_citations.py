"""One-note literal attribution, parser exclusions and snapshot consistency."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
import yaml
from fastmcp import Client

from markdown_vault_mcp.citations import inspect_citation_text
from markdown_vault_mcp.exceptions import (
    DocumentNotFoundError,
    DocumentUnreadableError,
    InvalidRequestError,
    NoteTooLargeError,
)
from markdown_vault_mcp.facets.reader import ReaderFacet
from markdown_vault_mcp.types import NoteContent
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


def _note(sources: object, body: str) -> str:
    return f"---\n{yaml.safe_dump({'sources': sources})}---\n{body}"


def test_literal_join_and_stable_ids() -> None:
    sources = [
        {"id": "a", "resource": "all queries in project X", "unknown": {"keep": 1}},
        {"id": "B", "resource": "../local.md"},
        {"id": " a ", "resource": "urn:scope"},
        {"resource": "https://example.invalid"},
    ]
    report = inspect_citation_text(
        "one.md", _note(sources, "`events_` claim[^a][^B] again[^a] missing[^b].")
    )
    assert report.sources == sources
    assert [
        (c.id, c.status, c.occurrences, c.source_indices) for c in report.citations
    ] == [("a", "matched", 2, [0]), ("B", "matched", 1, [1]), ("b", "unmatched", 1, [])]
    assert report.unkeyed_source_indices == [3]
    assert report.source_issues == []
    reordered = inspect_citation_text(
        "one.md", _note(list(reversed(sources)), "[^a][^B]")
    )
    assert [c.id for c in reordered.citations] == ["a", "B"]
    assert reordered.citations[0].source_indices == [3]
    assert "not support or truth" in report.scope


def test_duplicate_ids_are_ambiguous_even_when_resources_equal() -> None:
    sources = [{"id": "a", "resource": "same"}] * 2
    report = inspect_citation_text("one.md", _note(sources, "claim[^a]"))
    assert report.citations[0].status == "ambiguous"
    assert report.citations[0].source_indices == [0, 1]
    assert report.duplicate_ids == ["a"]
    unused = inspect_citation_text("one.md", _note(sources, "No markers"))
    assert unused.citations == []
    assert unused.duplicate_ids == ["a"]


@pytest.mark.parametrize("sources", [None, "wrong", {"id": "a"}, 42])
def test_malformed_source_collection_is_visible(sources: object) -> None:
    report = inspect_citation_text("one.md", _note(sources, "[^a]"))
    assert report.sources == sources
    assert report.source_issues == ["sources is not a list; no source IDs joined"]
    assert report.citations[0].status == "unmatched"


def test_malformed_records_do_not_hide_literal_matches() -> None:
    sources = [
        False,
        {"id": 12, "resource": "ok"},
        {"id": "a"},
        {"resource": "scope"},
        {"id": "", "resource": 42},
    ]
    report = inspect_citation_text("one.md", _note(sources, "[^a][^12]"))
    assert report.sources == sources
    assert len(report.source_issues) == 5
    assert report.unkeyed_source_indices == [3]
    assert report.citations[0].status == "matched"
    assert report.citations[1].status == "unmatched"


@pytest.mark.parametrize(
    "body",
    [
        "`[^a]`",
        "``[^a]``",
        "`line\n[^a]`",
        "~~~\n[^a]\n~~~",
        "```python\n[^a]",
        "    [^a]",
        "\\[^a]",
        "[^a]: prose",
        "[^x]: prose\n\n    continuation[^a]",
        "[link](note.md)",
        "[^a](note.md)",
        "[label][^a]",
        "[text [^a]](note.md)",
        "![alt [^a]](image.png)",
        "[[note|[^a]]]",
        "[[[^a]]]",
        "[[unclosed [^a]",
        "[text [^a]][nav]\n\n[nav]: note.md",
        "[^a][nav]\n\n[nav]: note.md",
        "<!--\n\n[^a]\n\n-->",
        "<script>\n\n[^a]\n\n</script>",
        '<span title="[^a]">text</span>',
        "[^two words]",
        "[^escaped\\]label]",
    ],
)
def test_non_citation_contexts(body: str) -> None:
    assert inspect_citation_text("one.md", body).citations == []


@pytest.mark.parametrize(
    "body",
    [
        "Claim `events_`[^a].",
        "[navigation](note.md) claim[^a].",
        "[navigation](note.md)[^a]",
        "See [[Other note]][^a]",
        "See [[Other note|alias]][^a].",
        "- List claim[^a]",
        "> Quote claim[^a]",
        "# Heading[^a]",
        "\\\\[^a]",
        "`unclosed text[^a]",
        "````\n[^hidden]\n```\n[^hidden]\n````\n\n[^a]",
        "<!--[^hidden]-->\n\n[^a]",
        "[^definition]: prose[^hidden]\n\nClaim[^a]",
    ],
)
def test_prose_around_excluded_contexts(body: str) -> None:
    assert [c.id for c in inspect_citation_text("one.md", body).citations] == ["a"]


def test_frontmatter_is_not_scanned_and_digest_covers_returned_text() -> None:
    content = _note([{"id": "a", "resource": "[^hidden]"}], "Claim[^a]\n").replace(
        "\n", "\r\n"
    )
    report = inspect_citation_text("one.md", content)
    assert [c.id for c in report.citations] == ["a"]
    assert (
        report.returned_text_sha256
        == hashlib.sha256(content.replace("\r\n", "\n").encode()).hexdigest()
    )
    assert inspect_citation_text("one.md", "plain").source_issues == []


def test_long_delimiters_and_repeated_markers() -> None:
    body = "`" * 16000 + "\n[^hidden]\n" + "`" * 16000 + "\n\n" + "[^a]" * 4000
    report = inspect_citation_text("one.md", body)
    assert len(report.citations) == 1
    assert report.citations[0].occurrences == 4000


def test_facet_uses_only_returned_content_snapshot() -> None:
    content = _note([{"id": "new", "resource": "new-scope"}], "Claim[^new]")
    manager = Mock()
    manager.read.return_value = NoteContent(
        path="one.md",
        title="old",
        folder="",
        content=content,
        frontmatter={"sources": [{"id": "old", "resource": "old"}]},
        modified_at=0,
        etag="old-file-etag",
    )
    reader = ReaderFacet(
        search_mgr=Mock(), doc_mgr=manager, git_query_mgr=Mock(), require_built=Mock()
    )
    report = reader.inspect_citations("one.md")
    manager.read.assert_called_once_with("one.md", section=None)
    assert report.citations[0].id == "new"
    assert report.citations[0].status == "matched"
    assert report.sources == [{"id": "new", "resource": "new-scope"}]
    assert report.returned_text_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert "etag" not in asdict(report)


def test_facet_keeps_existing_access_and_size_limits(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("Claim[^a]", encoding="utf-8")
    (tmp_path / "other.txt").write_text("Claim[^a]", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(max_note_read_bytes=5))
    try:
        with pytest.raises(NoteTooLargeError):
            vault.reader.inspect_citations("one.md")
        for path in ["other.txt", "ONE.MD", "folder"]:
            with pytest.raises(InvalidRequestError, match=r"not a \.md note"):
                vault.reader.inspect_citations(path)
        for path in ["missing.md", "../outside.md"]:
            with pytest.raises(DocumentNotFoundError):
                vault.reader.inspect_citations(path)
    finally:
        vault.close()


async def test_tool_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = _note([{"id": "a", "resource": "never fetched"}], "Claim[^a]")
    (tmp_path / "one.md").write_text(content, encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        tool = tools["inspect_citations"]
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint
        assert tool.annotations.title == "Inspect Note Citations"
        assert tool.icons
        reply = await client.call_tool("inspect_citations", {"path": "one.md"})
        assert reply.structured_content == asdict(
            inspect_citation_text("one.md", content)
        )
        with pytest.raises(Exception, match=r"Document not found: 'missing\.md'"):
            await client.call_tool("inspect_citations", {"path": "missing.md"})


def test_newer_unparseable_snapshot_is_unreadable_not_empty() -> None:
    manager = Mock()
    manager.read.return_value = NoteContent(
        path="one.md",
        title="old",
        folder="",
        content="---\nsources: [\n---\n[^new]",
        frontmatter={"sources": [{"id": "old", "resource": "old"}]},
        modified_at=0,
        etag="old-etag",
    )
    reader = ReaderFacet(
        search_mgr=Mock(), doc_mgr=manager, git_query_mgr=Mock(), require_built=Mock()
    )
    with pytest.raises(DocumentUnreadableError) as exc:
        reader.inspect_citations("one.md")
    assert isinstance(exc.value.__cause__, yaml.YAMLError)


@pytest.mark.parametrize(
    "body", ["[^a](", "[^a][]", "[^a][undefined]", "[text][^a]", "[t][ref][^a]"]
)
def test_incomplete_navigation_shapes_are_not_citations(body: str) -> None:
    assert inspect_citation_text("one.md", body).citations == []


def test_definition_interrupts_paragraph_without_becoming_a_marker() -> None:
    report = inspect_citation_text(
        "one.md", "Claim[^a]\n[^definition]: prose[^hidden]\n"
    )
    assert [citation.id for citation in report.citations] == ["a"]
