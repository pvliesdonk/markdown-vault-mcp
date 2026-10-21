"""Exact query library/MCP bridge, wire contracts and read outcomes."""

from __future__ import annotations

import json
import logging
import os
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._tools._common import _staleness_result
from markdown_vault_mcp.domain import get_vault_singleton
from markdown_vault_mcp.exceptions import IndexUnavailableError, InvalidRequestError
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_mcp_writer_drain
from tests.fixtures.query_http import live_mcp
from tests.server_factory import make_server


@pytest.fixture
def query_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> FastMCP:
    """Construct a real corpus larger than one default result page."""
    source = tmp_path / "vault"
    source.mkdir()
    for i in range(25):
        (source / f"{i:02}.md").write_text(
            f"---\ntags: [common, tier{i % 2}]\nstatus: stable\n---\n# Metadata\n\nbodyterm one two three\n",
            encoding="utf-8",
        )
    (source / "meta.md").write_text(
        "---\ntitle: bodyterm\n---\n# Different\n\nOther content\n", encoding="utf-8"
    )
    if getattr(request, "param", False):
        (source / "index.md").write_text(
            '---\nokf_version: "0.2"\n---\n# Index\n', encoding="utf-8"
        )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags,status")
    return make_server(transport="http")


@pytest.fixture
def query_vault(tmp_path: Path) -> Iterator[Vault]:
    """Yield a built library facade using the new concrete query backend."""
    (tmp_path / "a.md").write_text("---\ntags: [one, two]\n---\n# Title\n\nbodyterm\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(indexed_frontmatter_fields=["tags"])
    )
    try:
        vault.index.build_index()
        yield vault
    finally:
        vault.close()


def test_reader_delegates_exact_population(query_vault: Vault) -> None:
    """Reader page/count/group projections share the keyword population."""
    spec = NoteQuery(query="bodyterm", scope="body")
    assert query_vault.reader.query_documents(spec).total == 1
    assert query_vault.reader.count_documents_matching(spec).total == 1
    grouped = query_vault.reader.aggregate_documents(spec, NoteGrouping(by="tag"))
    assert grouped.buckets == [{"key": "one", "count": 1}, {"key": "two", "count": 1}]
    with pytest.raises(InvalidRequestError):
        query_vault.reader.count_documents_matching(NoteQuery(filters={"unknown": "x"}))


async def test_exact_queries_over_real_http(query_server: FastMCP) -> None:
    """HTTP pages, exact totals and capped buckets retain one population."""
    async with live_mcp(query_server) as url, Client(url) as client:
        await wait_for_mcp_writer_drain(client)
        args = {"query": "bodyterm", "scope": "body"}
        reply = await client.call_tool(
            "query_documents", {**args, "limit": 7, "offset": 7, "snippet_words": 1}
        )
        page = reply.structured_content
        assert page["total"] == 25 and len(page["items"]) == 7 and page["truncated"]
        assert page["offset"] == 7 and page["limit"] == 7
        assert all(
            len(s["content"].split()) <= 1 for x in page["items"] for s in x["sections"]
        )
        assert page["index_stale"] == reply.meta["index_stale"] is False
        counted = await client.call_tool("count_documents", args)
        assert counted.structured_content == {"total": 25, "index_stale": False}
        grouped = await client.call_tool(
            "aggregate_documents", {**args, "by": "tag", "limit": 1}
        )
        assert grouped.structured_content["buckets"] == [{"key": "common", "count": 25}]
        assert (
            grouped.structured_content["total"] == 25
            and grouped.structured_content["truncated"]
        )
        metadata = await client.call_tool(
            "query_documents", {"query": "bodyterm", "scope": "metadata"}
        )
        assert metadata.structured_content["total"] == 1
        assert metadata.structured_content["items"][0]["path"] == "meta.md"
        assert "sections" not in metadata.structured_content["items"][0]
        empty = await client.call_tool("count_documents", {"path": "absent.md"})
        assert empty.structured_content["total"] == 0


async def test_schema_hides_internal_path_seams(query_server: FastMCP) -> None:
    """Typed public schemas expose bounded exact projections without internal orders."""
    async with Client(query_server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        for name in ["query_documents", "count_documents", "aggregate_documents"]:
            tool = tools[name]
            assert (
                not {"order", "pattern_mode"} & tool.input_schema["properties"].keys()
            )
            assert "total" in tool.output_schema["properties"]
            assert "index_stale" in tool.output_schema["properties"]
            assert tool.annotations.read_only_hint and tool.annotations.idempotent_hint
            assert tool.annotations.title and tool.icons


async def test_invalid_indexed_fields_are_request_outcomes(
    query_server: FastMCP, caplog: pytest.LogCaptureFixture
) -> None:
    """Unindexed and computed fields guide callers without operator-error tracebacks."""
    async with Client(query_server) as client:
        await wait_for_mcp_writer_drain(client)
        caplog.clear()
        for field in ["unknown", "status", "stale", "trust_tier"]:
            with pytest.raises(ToolError, match=field):
                await client.call_tool(
                    "count_documents", {"filters": {field: "stable"}}
                )
        assert not [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ]


@pytest.mark.parametrize("query_server", [True], indirect=True)
async def test_query_items_retain_okf_annotations(query_server: FastMCP) -> None:
    """Exact queries annotate returned notes while keeping their population unchanged."""
    async with Client(query_server) as client:
        await wait_for_mcp_writer_drain(client)
        reply = await client.call_tool(
            "query_documents", {"query": "bodyterm", "scope": "body"}
        )
        assert reply.structured_content["total"] == 25
        assert all(
            item["okf"]["status"] == "stable"
            for item in reply.structured_content["items"]
        )


@pytest.mark.parametrize(
    "drained,generation,idle,expected",
    [
        (True, 1, True, False),
        (False, 1, True, True),
        (True, 2, True, True),
        (True, 1, False, True),
    ],
)
def test_freshness_is_one_shared_observation(
    drained: bool, generation: int, idle: bool, expected: bool
) -> None:
    """Payload, text and MCP metadata agree after one freshness computation."""
    index = SimpleNamespace(
        write_generation=Mock(return_value=generation),
        is_drained=Mock(return_value=idle),
    )
    vault = SimpleNamespace(index=index)
    payload = {"total": 3, "index_stale": False}
    result = _staleness_result(vault, payload, drained_on_request=drained, gen_before=1)
    assert (
        result.structured_content["index_stale"]
        == result.meta["index_stale"]
        == expected
    )
    assert json.loads(result.content[0].text)["index_stale"] == expected
    assert index.write_generation.call_count <= 1 and index.is_drained.call_count <= 1
    legacy = {"total": 3}
    wrapped = _staleness_result(
        vault, legacy, drained_on_request=True, gen_before=generation
    )
    assert wrapped.structured_content == {"total": 3}


def test_reader_refuses_an_unbuilt_population(tmp_path: Path) -> None:
    """An unavailable index is never reported as an exact zero."""
    vault = Vault(source_dir=tmp_path)
    try:
        for operation in (
            lambda: vault.reader.query_documents(NoteQuery()),
            lambda: vault.reader.count_documents_matching(NoteQuery()),
            lambda: vault.reader.aggregate_documents(
                NoteQuery(), NoteGrouping(by="folder")
            ),
        ):
            with pytest.raises(IndexUnavailableError):
                operation()
    finally:
        vault.close()


def test_corrupt_frontmatter_remains_a_server_fault(query_vault: Vault) -> None:
    """Malformed stored JSON is not disguised as caller validation."""
    query_vault._fts._conn().execute("UPDATE documents SET frontmatter_json = '{'")
    query_vault._fts._conn().commit()
    with pytest.raises(json.JSONDecodeError):
        query_vault.reader.query_documents(NoteQuery())


async def test_query_freshness_over_http(
    query_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pending writes and a bounded drain timeout agree on every wire channel."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S", "0")
    async with live_mcp(query_server) as url, Client(url) as client:
        await wait_for_mcp_writer_drain(client)
        vault = get_vault_singleton()
        vault._coordinator.writer.mark_dirty(["sentinel.md"])
        try:
            for name, args in (
                ("query_documents", {}),
                ("count_documents", {}),
                ("aggregate_documents", {"by": "folder"}),
            ):
                reply = await client.call_tool(
                    name, {**args, "wait_for_pending_writes": True}
                )
                assert (
                    reply.structured_content["index_stale"]
                    is reply.meta["index_stale"]
                    is True
                )
                assert json.loads(reply.content[0].text)["index_stale"] is True
        finally:
            vault._coordinator.writer.drain_dirty_paths()
        assert any(
            record.levelno == logging.WARNING and "drain" in record.message
            for record in caplog.records
        )
        assert not [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ]


async def test_invalid_query_instants_are_quiet_outcomes(
    query_server: FastMCP, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid time bounds ask for a changed request without an ERROR traceback."""
    async with Client(query_server) as client:
        await wait_for_mcp_writer_drain(client)
        caplog.clear()
        with pytest.raises(ToolError, match="RFC3339"):
            await client.call_tool("count_documents", {"modified_after": "yesterday"})
        assert not [
            record for record in caplog.records if record.levelno >= logging.ERROR
        ]


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    case=st.tuples(
        st.sampled_from(["", "bodyterm"]),
        st.sampled_from(["all", "metadata", "body"]),
        st.sampled_from([None, "common", "tier0", "tier1", "absent"]),
        st.integers(min_value=0),
        st.integers(min_value=1, max_value=1000),
    )
)
async def test_wire_projections_agree_with_corpus(
    query_server: FastMCP, case: tuple[str, str, str | None, int, int]
) -> None:
    """MCP page/count/groups describe the independently known corpus population.

    Each example opens and closes its own server lifespan; the function-scoped
    fixture only supplies immutable source files and configuration.
    """
    query, scope, tag, offset, limit = case
    expected = {
        f"{i:02}.md"
        for i in range(25)
        if (tag is None or tag in {"common", f"tier{i % 2}"})
        and not (query and scope == "metadata")
    }
    if tag is None and not (query and scope == "body"):
        expected.add("meta.md")
    args = {
        "query": query,
        "scope": scope,
        "filters": {} if tag is None else {"tags": tag},
    }
    async with Client(query_server) as client:
        await wait_for_mcp_writer_drain(client)
        page = (
            await client.call_tool(
                "query_documents", {**args, "offset": offset, "limit": limit}
            )
        ).structured_content
        count = (await client.call_tool("count_documents", args)).structured_content
        groups = (
            await client.call_tool("aggregate_documents", {**args, "by": "folder"})
        ).structured_content
    assert page["total"] == count["total"] == groups["total"] == len(expected)
    assert {item["path"] for item in page["items"]} <= expected
    assert len(page["items"]) == min(limit, max(0, len(expected) - offset))
    assert page["offset"] == offset
    assert groups["buckets"] == (
        [{"key": "", "count": len(expected)}] if expected else []
    )


def test_stored_integer_decode_failures_are_not_request_errors(
    query_vault: Vault,
) -> None:
    """Valid JSON can fail decoding without being a query validation refusal."""
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        payload = '{"number":' + "1" * 641 + "}"
        query_vault._fts._conn().execute(
            "UPDATE documents SET frontmatter_json = ?", (payload,)
        )
        query_vault._fts._conn().commit()
        with pytest.raises(ValueError) as failure:
            query_vault.reader.query_documents(NoteQuery())
        assert not isinstance(failure.value, InvalidRequestError)
    finally:
        sys.set_int_max_str_digits(previous)


async def test_all_predicates_reach_each_projection(
    query_server: FastMCP, tmp_path: Path
) -> None:
    """Every flat predicate can exclude the same exact HTTP population."""
    for note in (tmp_path / "vault").glob("*.md"):
        os.utime(note, (1700000000, 1700000000))
    base = {
        "query": "bodyterm",
        "scope": "body",
        "path": "00.md",
        "folder": "",
        "pattern": "0?.md",
        "filters": {"tags": "common"},
        "modified_after": "2020-01-01T00:00:00Z",
        "modified_before": "2024-01-01T00:00:00Z",
    }
    exclusions = [
        {"path": "absent.md"},
        {"folder": "absent"},
        {"pattern": "*.txt"},
        {"filters": {"tags": "absent"}},
        {"modified_after": "2023-12-01T00:00:00Z"},
        {"modified_before": "2023-01-01T00:00:00Z"},
    ]
    async with live_mcp(query_server) as url, Client(url) as client:
        await wait_for_mcp_writer_drain(client)
        for extra, expected in [({}, 1), *[(x, 0) for x in exclusions]]:
            args = {**base, **extra}
            for name in ["query_documents", "count_documents", "aggregate_documents"]:
                request = (
                    {**args, "by": "folder"} if name == "aggregate_documents" else args
                )
                result = (await client.call_tool(name, request)).structured_content
                assert result["total"] == expected, (name, extra)


async def test_schema_rejects_unsupported_query_controls(query_server: FastMCP) -> None:
    """Unknown/internal controls and wrong wire types cannot become valid queries."""
    async with Client(query_server) as client:
        await wait_for_mcp_writer_drain(client)
        for args in [
            {"order": "path"},
            {"pattern_mode": "prefix"},
            {"mode": "semantic"},
            {"scope": "semantic"},
            {"limit": True},
            {"offset": 1.5},
            {"filters": {"tags": 42}},
            {"limit": 1001},
            {"snippet_words": -1},
        ]:
            with pytest.raises(ToolError):
                await client.call_tool("query_documents", args)
