"""End-to-end tests for OKF read semantics via the MCP server.

Covers the ``okf`` annotation on ``search`` / ``read`` / ``get_context``
results, the ``okf`` section of ``stats``, the effective indexed-field
extension (and the ``document_tags`` filters riding it), the
``config://vault`` fields, the server-instructions sentence, and — the
phase-1 acceptance baseline — that a non-OKF vault's payloads carry no OKF
keys at all.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from fastmcp import Client

from markdown_vault_mcp.server import make_server

from .conftest import _CLEAR_VARS, _parse_tool_data, wait_for_mcp_writer_drain

ROOT_INDEX = """---
okf_version: "0.2"
title: Bundle index
---
# Bundle

- [Playbook](/guides/playbook.md)
"""

PLAYBOOK = """---
type: Playbook
status: deprecated
stale_after: 2020-01-01
verified:
  - by: human:peter
    at: 2026-01-01
sources:
  - resource: https://example.com/a
    id: a
  - resource: https://example.com/b
generated:
  by: process:enrich
---
# Playbook

Zebra migration steps.

## Steps

Zebra checklist body.
"""

PLAIN_NOTE = """# Plain

Zebra background notes.
"""


def _write_notes(vault: Path, *, declared: bool) -> None:
    (vault / "guides").mkdir(parents=True)
    if declared:
        (vault / "index.md").write_text(ROOT_INDEX, encoding="utf-8")
    (vault / "guides" / "playbook.md").write_text(PLAYBOOK, encoding="utf-8")
    (vault / "guides" / "plain.md").write_text(PLAIN_NOTE, encoding="utf-8")


@pytest.fixture
def okf_vault(tmp_path: Path) -> Path:
    """A vault declaring itself an OKF bundle in the root ``index.md``."""
    vault = tmp_path / "vault"
    _write_notes(vault, declared=True)
    return vault


@pytest.fixture
def plain_vault(tmp_path: Path) -> Path:
    """The same notes without any OKF declaration."""
    vault = tmp_path / "vault"
    _write_notes(vault, declared=False)
    return vault


@pytest.fixture
def _okf_env(okf_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(okf_vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def _plain_env(plain_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(plain_vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


def _structured(result: Any) -> dict[str, Any]:
    data = result.structured_content
    assert isinstance(data, dict)
    return data


async def _search(client: Client[Any], **kwargs: Any) -> list[dict[str, Any]]:
    await wait_for_mcp_writer_drain(client)
    result = await client.call_tool("search", {"query": "zebra", **kwargs})
    hits = _parse_tool_data(result)
    assert isinstance(hits, list)
    return hits


@pytest.mark.usefixtures("_plain_env")
class TestNonOkfBaseline:
    """A vault without a declaration gets zero OKF payload changes."""

    async def test_search_read_context_stats_carry_no_okf_key(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client)
            assert hits and all("okf" not in hit for hit in hits)
            read = _structured(
                await client.call_tool("read", {"path": "guides/playbook.md"})
            )
            assert "okf" not in read
            stats = _structured(await client.call_tool("stats", {}))
            assert "okf" not in stats
            context = _structured(
                await client.call_tool(
                    "get_context",
                    {"path": "guides/playbook.md", "similar_limit": 0},
                )
            )
            assert "okf" not in context

    async def test_indexed_fields_not_extended(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            stats = _structured(await client.call_tool("stats", {}))
        assert "type" not in stats["indexed_frontmatter_fields"]

    async def test_config_resource_reports_inactive(self) -> None:
        async with Client(make_server()) as client:
            resource = await client.read_resource("config://vault")
        payload = json.loads(resource[0].text)  # type: ignore[union-attr]
        assert payload["okf_mode"] == "auto"
        assert payload["okf_active"] is False
        assert payload["okf_declared_version"] is None


@pytest.mark.usefixtures("_okf_env")
class TestDeclaredBundle:
    """A declared vault gains annotations on every read surface."""

    async def test_search_hits_carry_annotations(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client)
        by_path = {hit["path"]: hit for hit in hits}
        playbook = by_path["guides/playbook.md"]["okf"]
        assert playbook["type"] == "Playbook"
        assert playbook["status"] == "deprecated"
        assert playbook["stale"] is True
        assert playbook["trust_tier"] == "human-reviewed"
        assert playbook["sources_count"] == 2
        assert "sources" not in playbook
        plain = by_path["guides/plain.md"]["okf"]
        assert plain == {
            "status": "stable",
            "stale": False,
            "trust_tier": "unverified",
        }

    async def test_read_includes_full_sources(self) -> None:
        async with Client(make_server()) as client:
            read = _structured(
                await client.call_tool("read", {"path": "guides/playbook.md"})
            )
        okf = read["okf"]
        assert okf["trust_tier"] == "human-reviewed"
        assert [s["resource"] for s in okf["sources"]] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert "sources_count" not in okf

    async def test_section_read_omits_annotation(self) -> None:
        async with Client(make_server()) as client:
            # Section resolution consults the index, so wait for the build.
            await wait_for_mcp_writer_drain(client)
            read = _structured(
                await client.call_tool(
                    "read", {"path": "guides/playbook.md", "section": "Steps"}
                )
            )
        assert "okf" not in read

    async def test_get_context_carries_annotation(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            context = _structured(
                await client.call_tool(
                    "get_context",
                    {"path": "guides/playbook.md", "similar_limit": 0},
                )
            )
        assert context["okf"]["type"] == "Playbook"

    async def test_stats_okf_section(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            stats = _structured(await client.call_tool("stats", {}))
        okf = stats["okf"]
        assert okf["mode"] == "auto"
        assert okf["declared_version"] == "0.2"
        assert okf["types"] == {"Playbook": 1}
        assert okf["untyped_count"] == 2  # index.md + plain.md
        assert okf["status"] == {"deprecated": 1, "stable": 2}
        assert okf["trust"] == {"human-reviewed": 1, "unverified": 2}
        assert okf["stale_count"] == 1

    async def test_indexed_fields_extended_and_filterable(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            stats = _structured(await client.call_tool("stats", {}))
            assert {"type", "status", "stale_after"} <= set(
                stats["indexed_frontmatter_fields"]
            )
            hits = await _search(client, filters={"type": "Playbook"})
        assert [hit["path"] for hit in hits] == ["guides/playbook.md"]

    async def test_config_resource_reports_active(self) -> None:
        async with Client(make_server()) as client:
            resource = await client.read_resource("config://vault")
        payload = json.loads(resource[0].text)  # type: ignore[union-attr]
        assert payload["okf_active"] is True
        assert payload["okf_declared_version"] == "0.2"

    async def test_instructions_mention_okf(self) -> None:
        server = make_server()
        assert server.instructions is not None
        assert "okf_version" in server.instructions


@pytest.mark.usefixtures("_okf_env")
class TestModeOff:
    """``OKF_MODE=off`` restores the non-OKF baseline on a declared vault."""

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")

    async def test_no_okf_surfaces(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client)
            assert hits and all("okf" not in hit for hit in hits)
            stats = _structured(await client.call_tool("stats", {}))
            assert "okf" not in stats
            assert "type" not in stats["indexed_frontmatter_fields"]
        server = make_server()
        assert server.instructions is not None
        assert "okf_version" not in server.instructions

    async def test_config_resource_reports_off(self) -> None:
        async with Client(make_server()) as client:
            resource = await client.read_resource("config://vault")
        payload = json.loads(resource[0].text)  # type: ignore[union-attr]
        assert payload["okf_mode"] == "off"
        assert payload["okf_active"] is False


@pytest.mark.usefixtures("_plain_env")
class TestModeOn:
    """``OKF_MODE=on`` forces read semantics without a declaration."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")

    async def test_annotations_and_stats_without_declaration(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client)
            assert all("okf" in hit for hit in hits)
            stats = _structured(await client.call_tool("stats", {}))
        assert stats["okf"]["mode"] == "on"
        assert stats["okf"]["declared_version"] is None


@pytest.mark.usefixtures("_okf_env")
class TestOkfFilterDimensions:
    """Phase 2 (#961): OKF semantics for status/stale/trust_tier filters."""

    async def test_status_stable_matches_absent_status(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client, filters={"status": "stable"})
        assert sorted(h["path"] for h in hits) == ["guides/plain.md"]

    async def test_status_deprecated(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client, filters={"status": "deprecated"})
        assert [h["path"] for h in hits] == ["guides/playbook.md"]

    async def test_stale_filter(self) -> None:
        async with Client(make_server()) as client:
            stale = await _search(client, filters={"stale": "true"})
            fresh = await _search(client, filters={"stale": "false"})
        assert [h["path"] for h in stale] == ["guides/playbook.md"]
        assert "guides/playbook.md" not in [h["path"] for h in fresh]

    async def test_trust_tier_filter_composes_with_type(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(
                client,
                filters={"trust_tier": "human-reviewed", "type": "Playbook"},
            )
        assert [h["path"] for h in hits] == ["guides/playbook.md"]

    async def test_invalid_stale_value_is_a_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="stale filter"):
                await client.call_tool(
                    "search", {"query": "zebra", "filters": {"stale": "banana"}}
                )

    async def test_list_documents_triage_listing(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "list_documents", {"filters": {"stale": "true"}}
            )
        listing = _parse_tool_data(result)
        assert [n["path"] for n in listing] == ["guides/playbook.md"]


@pytest.mark.usefixtures("_okf_env")
class TestFilterDimensionsModeOff:
    """With OKF off, the dimension keys fall back to plain tag filters."""

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")

    async def test_stale_filter_is_inert(self) -> None:
        async with Client(make_server()) as client:
            hits = await _search(client, filters={"stale": "true"})
            listing = _parse_tool_data(
                await client.call_tool("list_documents", {"filters": {"stale": "true"}})
            )
        assert hits == []
        assert listing == []

    async def test_status_is_plain_equality(self) -> None:
        async with Client(make_server()) as client:
            # Plain semantics: no absent-means-stable defaulting, and no
            # 'status' tag is indexed with OKF off, so nothing matches.
            hits = await _search(client, filters={"status": "stable"})
        assert hits == []


@pytest.mark.usefixtures("_okf_env")
class TestOkfValidateTool:
    """Phase 3 (#962): the okf_validate conformance audit tool."""

    async def test_report_on_declared_vault(self) -> None:
        async with Client(make_server()) as client:
            report = _structured(await client.call_tool("okf_validate", {}))
        assert report["active"] is True
        assert report["declared_version"] == "0.2"
        assert report["total_notes"] == 2  # playbook + plain (index.md reserved)
        assert report["conformant_notes"] == 1
        assert report["missing_type"]["examples"] == ["guides/plain.md"]
        assert report["root_index_missing"] is False

    async def test_hidden_when_mode_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")
        async with Client(make_server()) as client:
            tools = {t.name for t in await client.list_tools()}
        assert "okf_validate" not in tools


@pytest.mark.usefixtures("_plain_env")
class TestOkfValidatePreDeclaration:
    """The audit is the "should I declare?" tool: usable before declaring."""

    async def test_report_on_undeclared_vault(self) -> None:
        async with Client(make_server()) as client:
            tools = {t.name for t in await client.list_tools()}
            assert "okf_validate" in tools
            report = _structured(await client.call_tool("okf_validate", {}))
        assert report["active"] is False
        assert report["declared_version"] is None
        assert report["root_index_missing"] is True
        assert report["total_notes"] == 2


@pytest.fixture
def _okf_env_writable(okf_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared OKF bundle, writable (for the migration transforms, #963)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(okf_vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.usefixtures("_okf_env_writable")
class TestOkfMigrationTools:
    """Phase 4 (#963): the migration transform tools end-to-end."""

    async def test_convert_links_runs(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("okf_convert_links", {})
        data = _parse_tool_data(result)
        # The seed bundle's index.md links to the playbook via a wikilink-free
        # markdown link already, so nothing to convert — but the tool runs and
        # reports the scan.
        assert set(data) == {
            "files_changed",
            "links_converted",
            "links_skipped",
            "notes_scanned",
        }

    async def test_generate_index_preserves_declaration(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("okf_generate_index", {})
            data = _parse_tool_data(result)
            assert data["path"] == "index.md"
            assert data["frontmatter_preserved"] is True
            read = _structured(await client.call_tool("read", {"path": "index.md"}))
        assert read["frontmatter"]["okf_version"] == "0.2"

    async def test_seed_log_writes(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("okf_seed_log", {})
        data = _parse_tool_data(result)
        assert data["path"] == "log.md"

    async def test_seed_log_refuses_overwrite_as_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool("okf_seed_log", {})
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match=r"log\.md"):
                await client.call_tool("okf_seed_log", {})

    async def test_tools_hidden_when_mode_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")
        async with Client(make_server()) as client:
            names = {t.name for t in await client.list_tools()}
        assert not ({"okf_convert_links", "okf_generate_index", "okf_seed_log"} & names)

    async def test_tools_hidden_when_read_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
        async with Client(make_server()) as client:
            names = {t.name for t in await client.list_tools()}
        assert not ({"okf_convert_links", "okf_generate_index", "okf_seed_log"} & names)
