"""Budgets for model-facing MCP prose and schemas (#1253)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp_pvl_core import utf16_code_units

from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


_WAIT_DESCRIPTION = (
    "Wait for recent index writes. On timeout, answer from the current index "
    "with _meta.index_stale=true. Default false."
)

_SURFACE_LIMITS = {
    "instructions": 1_536,
    "tool_descriptions": 23_500,
    "tool_input_schemas": 27_600,
    "tool_output_schemas": 3_500,
    "prompt_descriptions": 900,
    "prompt_arguments": 150,
    "resource_descriptions": 700,
    "resource_template_descriptions": 800,
}
_TOTAL_SURFACE_LIMIT = 58_000


@pytest.fixture
def maximal_surface_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Build the largest client-visible surface without starting the vault."""
    from markdown_vault_mcp import server as server_module

    @asynccontextmanager
    async def _skip_lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
        yield {}

    monkeypatch.setattr(server_module, "server_lifespan", _skip_lifespan)
    (tmp_path / "index.md").write_text(
        "---\nokf_version: 0.2\n---\n# Index\n", encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://vault.example")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://github.com/example/vault.git"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_KV_STORE_URL", "memory://")
    return make_server(transport="http")


def _compact_schema(schema: dict[str, Any]) -> str:
    return json.dumps(
        schema,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _units(values: list[str | None]) -> int:
    return sum(utf16_code_units(value or "") for value in values)


@pytest.mark.asyncio
async def test_maximal_client_surface_stays_within_reviewed_budgets(
    maximal_surface_server: FastMCP,
) -> None:
    """Measure post-parse protocol objects, not source docstrings."""
    async with Client(maximal_surface_server) as client:
        tools = await client.list_tools()
        prompts = await client.list_prompts()
        resources = await client.list_resources()
        resource_templates = await client.list_resource_templates()

    actual = {
        "instructions": utf16_code_units(maximal_surface_server.instructions or ""),
        "tool_descriptions": _units([tool.description for tool in tools]),
        "tool_input_schemas": _units(
            [_compact_schema(tool.inputSchema) for tool in tools]
        ),
        "tool_output_schemas": _units(
            [
                _compact_schema(tool.outputSchema)
                for tool in tools
                if tool.outputSchema is not None
            ]
        ),
        "prompt_descriptions": _units([prompt.description for prompt in prompts]),
        "prompt_arguments": _units(
            [
                argument.description
                for prompt in prompts
                for argument in (prompt.arguments or [])
            ]
        ),
        "resource_descriptions": _units(
            [resource.description for resource in resources]
        ),
        "resource_template_descriptions": _units(
            [resource.description for resource in resource_templates]
        ),
    }
    exceeded = {
        name: (actual[name], limit)
        for name, limit in _SURFACE_LIMITS.items()
        if actual[name] > limit
    }
    assert not exceeded, f"client-surface category budgets exceeded: {exceeded}"
    assert sum(actual.values()) <= _TOTAL_SURFACE_LIMIT, actual

    wait_descriptions = [
        tool.inputSchema["properties"]["wait_for_pending_writes"]["description"]
        for tool in tools
        if "wait_for_pending_writes" in tool.inputSchema.get("properties", {})
    ]
    assert wait_descriptions
    assert set(wait_descriptions) == {_WAIT_DESCRIPTION}

    expected_descriptions = {
        "embeddings_status": "Check embedding provider and vector-index status.",
        "get_index_status": (
            "Report FTS index readiness, progress, and last build error."
        ),
        "okf_validate": "Audit the vault's Open Knowledge Format conformance.",
    }
    descriptions = {tool.name: tool.description for tool in tools}
    for name, expected in expected_descriptions.items():
        assert descriptions[name] == expected
        assert "Returns:" not in (descriptions[name] or "")
