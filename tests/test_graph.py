"""Tests for orphan detection and hub notes (get_orphan_notes, get_most_linked)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.types import (
    Chunk,
    LinkInfo,
    MostLinkedNote,
    NoteInfo,
    ParsedNote,
)
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_mcp_writer_drain

if TYPE_CHECKING:
    from pathlib import Path


def _parse_tool_data(result: Any) -> Any:
    """Extract list/dict from a CallToolResult, handling FastMCP serialization."""
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_note(
    path: str,
    title: str = "Test",
    links: list[LinkInfo] | None = None,
) -> ParsedNote:
    return ParsedNote(
        path=path,
        frontmatter={},
        title=title,
        chunks=[Chunk(heading=None, heading_level=0, content="body", start_line=0)],
        content_hash="abc",
        modified_at=0.0,
        links=links or [],
    )


# ---------------------------------------------------------------------------
# FTSIndex.get_orphan_notes
# ---------------------------------------------------------------------------


class TestFTSGetOrphanNotes:
    def test_note_with_no_links_is_orphan(self) -> None:
        """A note with no outlinks and no backlinks is an orphan."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("orphan.md"))
        rows = idx.get_orphan_notes()
        assert len(rows) == 1
        assert rows[0]["path"] == "orphan.md"

    def test_note_with_only_outlinks_is_not_orphan(self) -> None:
        """A note that has outlinks (even to non-indexed paths) is not an orphan."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "linker.md",
                links=[
                    LinkInfo(
                        target_path="other.md", link_text="T", link_type="markdown"
                    )
                ],
            )
        )
        rows = idx.get_orphan_notes()
        assert all(r["path"] != "linker.md" for r in rows)

    def test_note_with_only_backlinks_is_not_orphan(self) -> None:
        """A note that is linked to by other notes is not an orphan."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("target.md"))
        idx.upsert_note(
            make_note(
                "source.md",
                links=[
                    LinkInfo(
                        target_path="target.md", link_text="T", link_type="markdown"
                    )
                ],
            )
        )
        rows = idx.get_orphan_notes()
        paths = {r["path"] for r in rows}
        assert "target.md" not in paths  # has backlink → not orphan
        assert "source.md" not in paths  # has outlink → not orphan

    def test_multiple_orphans_returned(self) -> None:
        """All orphan notes are returned."""
        idx = FTSIndex(":memory:")
        for i in range(3):
            idx.upsert_note(make_note(f"orphan{i}.md"))
        rows = idx.get_orphan_notes()
        assert len(rows) == 3

    def test_orphan_notes_ordered_by_path(self) -> None:
        """Results are ordered by path."""
        idx = FTSIndex(":memory:")
        for name in ["z.md", "a.md", "m.md"]:
            idx.upsert_note(make_note(name))
        rows = idx.get_orphan_notes()
        paths = [r["path"] for r in rows]
        assert paths == sorted(paths)

    def test_empty_vault_returns_empty(self) -> None:
        """get_orphan_notes returns [] when no documents are indexed."""
        idx = FTSIndex(":memory:")
        assert idx.get_orphan_notes() == []

    def test_orphan_row_includes_title_and_folder(self) -> None:
        """Each row contains path, title, folder, frontmatter_json, modified_at."""
        idx = FTSIndex(":memory:")
        note = ParsedNote(
            path="folder/note.md",
            frontmatter={"key": "val"},
            title="My Note",
            chunks=[Chunk(heading=None, heading_level=0, content="x", start_line=0)],
            content_hash="h",
            modified_at=1234.5,
        )
        idx.upsert_note(note)
        rows = idx.get_orphan_notes()
        assert len(rows) == 1
        assert rows[0]["title"] == "My Note"
        assert rows[0]["folder"] == "folder"


# ---------------------------------------------------------------------------
# FTSIndex.get_most_linked
# ---------------------------------------------------------------------------


class TestFTSGetMostLinked:
    def test_most_linked_returns_correct_counts(self) -> None:
        """get_most_linked returns notes sorted by backlink_count descending."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("hub.md"))
        idx.upsert_note(make_note("less.md"))
        for i in range(3):
            idx.upsert_note(
                make_note(
                    f"src{i}.md",
                    links=[
                        LinkInfo(
                            target_path="hub.md", link_text="H", link_type="markdown"
                        )
                    ],
                )
            )
        idx.upsert_note(
            make_note(
                "src_less.md",
                links=[
                    LinkInfo(target_path="less.md", link_text="L", link_type="markdown")
                ],
            )
        )
        rows = idx.get_most_linked()
        assert rows[0]["path"] == "hub.md"
        assert rows[0]["backlink_count"] == 3
        assert rows[1]["path"] == "less.md"
        assert rows[1]["backlink_count"] == 1

    def test_get_most_linked_limit(self) -> None:
        """limit caps the number of results."""
        idx = FTSIndex(":memory:")
        for i in range(5):
            idx.upsert_note(make_note(f"target{i}.md"))
            idx.upsert_note(
                make_note(
                    f"src{i}.md",
                    links=[
                        LinkInfo(
                            target_path=f"target{i}.md",
                            link_text="T",
                            link_type="markdown",
                        )
                    ],
                )
            )
        rows = idx.get_most_linked(limit=3)
        assert len(rows) == 3

    def test_get_most_linked_empty_vault(self) -> None:
        """get_most_linked returns [] when no links exist."""
        idx = FTSIndex(":memory:")
        assert idx.get_most_linked() == []

    def test_most_linked_row_keys(self) -> None:
        """Each row contains path, title, folder, backlink_count."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("notes/target.md", title="Target Note"))
        idx.upsert_note(
            make_note(
                "src.md",
                links=[
                    LinkInfo(
                        target_path="notes/target.md",
                        link_text="T",
                        link_type="markdown",
                    )
                ],
            )
        )
        rows = idx.get_most_linked()
        assert rows[0]["path"] == "notes/target.md"
        assert rows[0]["title"] == "Target Note"
        assert rows[0]["folder"] == "notes"
        assert "backlink_count" in rows[0]


# ---------------------------------------------------------------------------
# GraphFacet.get_orphan_notes / GraphFacet.get_most_linked
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_vault(tmp_path: Path) -> Path:
    """Vault with a hub note, a linked note, and an orphan note.

    hub.md        linked to by source.md and source2.md
    source.md     links to hub.md
    source2.md    links to hub.md
    orphan.md     no links in or out
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "hub.md").write_text("# Hub\n\nThe central note.\n")
    (vault / "source.md").write_text("# Source\n\nSee [Hub](hub.md).\n")
    (vault / "source2.md").write_text("# Source 2\n\nAlso see [Hub](hub.md).\n")
    (vault / "orphan.md").write_text("# Orphan\n\nIsolated note.\n")
    return vault


class TestVaultOrphanAndHub:
    def test_get_orphan_notes_returns_note_info(self, graph_vault: Path) -> None:
        """GraphFacet.get_orphan_notes() returns NoteInfo objects."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        orphans = col.graph.get_orphan_notes()
        assert all(isinstance(n, NoteInfo) for n in orphans)

    def test_get_orphan_notes_finds_orphan(self, graph_vault: Path) -> None:
        """The orphan note appears in results."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        paths = {n.path for n in col.graph.get_orphan_notes()}
        assert "orphan.md" in paths

    def test_get_orphan_notes_excludes_linked(self, graph_vault: Path) -> None:
        """Notes with links (in or out) are not orphans."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        paths = {n.path for n in col.graph.get_orphan_notes()}
        assert "hub.md" not in paths
        assert "source.md" not in paths
        assert "source2.md" not in paths

    def test_get_most_linked_returns_dataclasses(self, graph_vault: Path) -> None:
        """GraphFacet.get_most_linked() returns list of MostLinkedNote dataclasses."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        results = col.graph.get_most_linked()
        assert all(isinstance(r, MostLinkedNote) for r in results)

    def test_get_most_linked_hub_is_first(self, graph_vault: Path) -> None:
        """The hub note (most backlinks) appears first."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        results = col.graph.get_most_linked()
        assert results[0].path == "hub.md"
        assert results[0].backlink_count == 2

    def test_get_most_linked_limit(self, graph_vault: Path) -> None:
        """limit caps the result count."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        results = col.graph.get_most_linked(limit=1)
        assert len(results) == 1

    def test_get_most_linked_includes_folder(self, graph_vault: Path) -> None:
        """MostLinkedNote.folder is populated from the documents table."""
        col = Vault(source_dir=graph_vault)
        col.index.build_index()
        results = col.graph.get_most_linked()
        hub = next(r for r in results if r.path == "hub.md")
        assert hub.folder == ""


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_graph_vault(tmp_path: Path) -> Path:
    """Minimal vault for MCP graph tool tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "hub.md").write_text("# Hub\n\nCentral.\n")
    (vault / "src.md").write_text("# Src\n\nSee [Hub](hub.md).\n")
    (vault / "orphan.md").write_text("# Orphan\n\nAlone.\n")
    return vault


@pytest.fixture
def _mcp_env_graph(mcp_graph_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _CLEAR_VARS = [
        "MARKDOWN_VAULT_MCP_SOURCE_DIR",
        "MARKDOWN_VAULT_MCP_DB_PATH",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
        "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
        "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
        "MARKDOWN_VAULT_MCP_READ_ONLY",
    ]
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(mcp_graph_vault))


class TestMCPGraphTools:
    async def test_get_orphan_notes_tool(self, _mcp_env_graph: None) -> None:
        """get_orphan_notes MCP tool returns orphan notes."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_orphan_notes", {})
        items = _parse_tool_data(result)
        assert isinstance(items, list)
        paths = {item["path"] for item in items}
        assert "orphan.md" in paths
        assert "hub.md" not in paths

    async def test_get_most_linked_tool(self, _mcp_env_graph: None) -> None:
        """get_most_linked MCP tool returns hub note first."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_most_linked", {"limit": 5})
        items = _parse_tool_data(result)
        assert isinstance(items, list)
        assert len(items) >= 1
        assert items[0]["path"] == "hub.md"
        assert items[0]["backlink_count"] == 1

    async def test_get_most_linked_tool_limit(self, _mcp_env_graph: None) -> None:
        """get_most_linked respects the limit parameter."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("get_most_linked", {"limit": 1})
        items = _parse_tool_data(result)
        assert len(items) == 1


# ---------------------------------------------------------------------------
# FTSIndex.get_connection_path
# ---------------------------------------------------------------------------


class TestFTSGetConnectionPath:
    """Unit tests for FTSIndex.get_connection_path BFS logic."""

    def _make_idx(self) -> FTSIndex:
        return FTSIndex(":memory:")

    def test_direct_link_returns_two_hops(self) -> None:
        """A→B link yields path [A, B] with 1 hop."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("b.md"))
        result = idx.get_connection_path("a.md", "b.md")
        assert result == ["a.md", "b.md"]

    def test_two_hop_path(self) -> None:
        """A→B→C query returns [A, B, C]."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "b.md",
                links=[
                    LinkInfo(target_path="c.md", link_text="C", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("c.md"))
        result = idx.get_connection_path("a.md", "c.md")
        assert result == ["a.md", "b.md", "c.md"]

    def test_reverse_direction_undirected(self) -> None:
        """Only A→B exists; querying B→A still returns [B, A]."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("b.md"))
        result = idx.get_connection_path("b.md", "a.md")
        assert result == ["b.md", "a.md"]

    def test_self_path(self) -> None:
        """Source == target returns a single-element list."""
        idx = self._make_idx()
        idx.upsert_note(make_note("a.md"))
        result = idx.get_connection_path("a.md", "a.md")
        assert result == ["a.md"]

    def test_no_connection_returns_none(self) -> None:
        """Isolated note with no links returns None."""
        idx = self._make_idx()
        idx.upsert_note(make_note("a.md"))
        idx.upsert_note(make_note("b.md"))
        result = idx.get_connection_path("a.md", "b.md")
        assert result is None

    def test_dangling_link_target_excluded_from_bfs(self) -> None:
        """Ghost nodes (links to non-indexed notes) do not appear in paths.

        a.md links to ghost.md (not indexed) and also to b.md.
        BFS must not traverse ghost.md — it is not a document.
        """
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(
                        target_path="ghost.md", link_text="Ghost", link_type="markdown"
                    ),
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown"),
                ],
            )
        )
        idx.upsert_note(make_note("b.md"))
        # ghost.md is intentionally NOT upserted — it is a dangling link target.
        # The path a→b should be found, and ghost.md must not appear anywhere.
        result = idx.get_connection_path("a.md", "b.md")
        assert result == ["a.md", "b.md"]
        # Also verify ghost node does not appear in any path traversal.
        assert result is not None
        assert "ghost.md" not in result

    def test_max_depth_exceeded_returns_none(self) -> None:
        """Path exists at depth 3 but max_depth=2 returns None."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "b.md",
                links=[
                    LinkInfo(target_path="c.md", link_text="C", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "c.md",
                links=[
                    LinkInfo(target_path="d.md", link_text="D", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("d.md"))
        # Path A→B→C→D exists (depth 3); cap at 2 → not found.
        assert idx.get_connection_path("a.md", "d.md", max_depth=2) is None
        # With depth 3 it should be found.
        result = idx.get_connection_path("a.md", "d.md", max_depth=3)
        assert result == ["a.md", "b.md", "c.md", "d.md"]

    def test_max_depth_clamped_low(self) -> None:
        """max_depth=0 is clamped to 1; direct link still found."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("b.md"))
        result = idx.get_connection_path("a.md", "b.md", max_depth=0)
        assert result == ["a.md", "b.md"]

    def test_max_depth_clamped_high(self) -> None:
        """max_depth=99 is clamped to 10; search still runs normally."""
        idx = self._make_idx()
        idx.upsert_note(
            make_note(
                "a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            )
        )
        idx.upsert_note(make_note("b.md"))
        result = idx.get_connection_path("a.md", "b.md", max_depth=99)
        assert result == ["a.md", "b.md"]

    def test_nonexistent_source_raises_value_error(self) -> None:
        """ValueError raised when source_path is not in documents table."""
        idx = self._make_idx()
        idx.upsert_note(make_note("b.md"))
        with pytest.raises(ValueError, match=r"missing\.md"):
            idx.get_connection_path("missing.md", "b.md")

    def test_nonexistent_target_raises_value_error(self) -> None:
        """ValueError raised when target_path is not in documents table."""
        idx = self._make_idx()
        idx.upsert_note(make_note("a.md"))
        with pytest.raises(ValueError, match=r"missing\.md"):
            idx.get_connection_path("a.md", "missing.md")


# ---------------------------------------------------------------------------
# GraphFacet.get_connection_path
# ---------------------------------------------------------------------------


class TestVaultGetConnectionPath:
    """Integration tests for GraphFacet.get_connection_path."""

    @pytest.fixture
    def path_vault(self, tmp_path: Path) -> Path:
        """Vault: a.md → b.md → c.md, plus isolated.md."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("# A\n\nSee [B](b.md).\n", encoding="utf-8")
        (vault / "b.md").write_text("# B\n\nSee [C](c.md).\n", encoding="utf-8")
        (vault / "c.md").write_text("# C\n\nEnd of chain.\n", encoding="utf-8")
        (vault / "isolated.md").write_text(
            "# Isolated\n\nNo links.\n", encoding="utf-8"
        )
        return vault

    def test_direct_connection(self, path_vault: Path) -> None:
        """Direct link a→b returns [a.md, b.md]."""
        col = Vault(source_dir=path_vault)
        col.index.build_index()
        result = col.graph.get_connection_path("a.md", "b.md")
        assert result == ["a.md", "b.md"]

    def test_two_hop_connection(self, path_vault: Path) -> None:
        """Two-hop path a→b→c returns [a.md, b.md, c.md]."""
        col = Vault(source_dir=path_vault)
        col.index.build_index()
        result = col.graph.get_connection_path("a.md", "c.md")
        assert result == ["a.md", "b.md", "c.md"]

    def test_undirected_reverse(self, path_vault: Path) -> None:
        """Reverse direction (c→a) is reachable via undirected graph."""
        col = Vault(source_dir=path_vault)
        col.index.build_index()
        result = col.graph.get_connection_path("c.md", "a.md")
        assert result is not None
        assert result[0] == "c.md"
        assert result[-1] == "a.md"

    def test_unreachable_returns_none(self, path_vault: Path) -> None:
        """Isolated note returns None."""
        col = Vault(source_dir=path_vault)
        col.index.build_index()
        result = col.graph.get_connection_path("a.md", "isolated.md")
        assert result is None

    def test_path_traversal_rejected(self, path_vault: Path) -> None:
        """Path traversal in source/target raises ValueError."""
        col = Vault(source_dir=path_vault)
        col.index.build_index()
        with pytest.raises(ValueError):
            col.graph.get_connection_path("../etc/passwd", "a.md")


# ---------------------------------------------------------------------------
# MCP tool: get_connection_path
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_path_vault(tmp_path: Path) -> Path:
    """Vault for MCP connection-path tests: a→b→c, plus isolated."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nSee [B](b.md).\n", encoding="utf-8")
    (vault / "b.md").write_text("# B\n\nSee [C](c.md).\n", encoding="utf-8")
    (vault / "c.md").write_text("# C\n\nEnd.\n", encoding="utf-8")
    (vault / "isolated.md").write_text("# Isolated\n\nNo links.\n", encoding="utf-8")
    return vault


@pytest.fixture
def _mcp_env_path(mcp_path_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _CLEAR_VARS = [
        "MARKDOWN_VAULT_MCP_SOURCE_DIR",
        "MARKDOWN_VAULT_MCP_DB_PATH",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
        "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
        "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
        "MARKDOWN_VAULT_MCP_READ_ONLY",
    ]
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(mcp_path_vault))


class TestMCPGetConnectionPath:
    async def test_found_path(self, _mcp_env_path: None) -> None:
        """get_connection_path tool returns found=True and correct path."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_connection_path", {"source": "a.md", "target": "c.md"}
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data["found"] is True
        assert data["path"] == ["a.md", "b.md", "c.md"]
        assert data["hops"] == 2

    async def test_not_found_path(self, _mcp_env_path: None) -> None:
        """get_connection_path tool returns found=False when no path exists."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_connection_path",
                {"source": "a.md", "target": "isolated.md"},
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data["found"] is False
        assert data["path"] == []
        assert data["hops"] == -1

    async def test_source_equals_target(self, _mcp_env_path: None) -> None:
        """get_connection_path tool returns found=True with 0 hops for source==target."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_connection_path", {"source": "a.md", "target": "a.md"}
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        data = envelope["data"]
        assert data["found"] is True
        assert data["path"] == ["a.md"]
        assert data["hops"] == 0

    async def test_invalid_source_raises_error(self, _mcp_env_path: None) -> None:
        """get_connection_path tool surfaces ValueError for an unindexed source path."""
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "get_connection_path",
                    {"source": "nonexistent.md", "target": "a.md"},
                )

    async def test_get_connection_path_with_wait_for_drain(
        self, _mcp_env_path: None
    ) -> None:
        """wait_for_drain=True blocks until writer drained, then returns envelope."""
        from fastmcp import Client

        from markdown_vault_mcp.server import make_server

        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_connection_path",
                {
                    "source": "a.md",
                    "target": "c.md",
                    "wait_for_drain": True,
                },
            )
        envelope = _parse_tool_data(result)
        assert envelope["stale"] is False
        assert envelope["data"]["found"] is True
