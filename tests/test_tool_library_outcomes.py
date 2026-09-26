"""Library signals reach the model as the outcome the design maps them to (#1608)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp._tools._outcomes import (
    library_outcomes,
    maps_library_outcomes,
    outcome_error,
)
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    DocumentUnreadableError,
    EditConflictError,
    EmbeddingsNotConfiguredError,
    IndexUnavailableError,
    InvalidRequestError,
    ReadOnlyError,
)

if TYPE_CHECKING:
    from fastmcp import Client


@pytest.mark.parametrize(
    "exc",
    [
        InvalidRequestError("bad path"),
        DocumentNotFoundError("no note"),
        EmbeddingsNotConfiguredError("off"),
        EditConflictError("old_text not found"),
        DocumentExistsError("taken"),
        ReadOnlyError("read-only"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_change_the_request_keeps_the_message_at_info(exc: Exception) -> None:
    err = outcome_error(exc)
    assert err is not None
    assert err.log_level == logging.INFO
    assert str(err) == str(exc)


def test_stale_etag_says_refresh_then_retry() -> None:
    err = outcome_error(ConcurrentModificationError("a.md", "e1", "e2"))
    assert err is not None
    assert err.log_level == logging.INFO
    assert "fails again" in str(err)
    assert "if_match" in str(err)


@pytest.mark.parametrize("reason", ["busy", "timeout"])
def test_transient_index_says_retry_at_warning(reason: Any) -> None:
    err = outcome_error(IndexUnavailableError("x", reason=reason))
    assert err is not None
    assert err.log_level == logging.WARNING
    assert "request was fine" in str(err)


@pytest.mark.parametrize(
    "exc",
    [
        IndexUnavailableError("x", reason="broken"),
        IndexUnavailableError("x", reason="never_built"),
        DocumentUnreadableError("a.md", "bad bytes"),
        ValueError("internal"),
        RuntimeError("bug"),
    ],
    ids=lambda e: type(e).__name__ + getattr(e, "reason", ""),
)
def test_server_failures_are_left_to_the_boundary(exc: Exception) -> None:
    assert outcome_error(exc) is None


async def test_unmapped_exception_propagates_unchanged() -> None:
    @library_outcomes
    async def tool() -> None:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        await tool()


def test_sync_tool_is_mapped() -> None:
    from fastmcp.exceptions import ToolError

    @library_outcomes
    def tool() -> None:
        raise InvalidRequestError("bad")

    with pytest.raises(ToolError, match="bad"):
        tool()


async def test_refusal_through_a_tool_logs_no_error(
    client: Client[Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result = await client.call_tool_mcp(
            "read", {"path": "no_frontmatter.md", "section": "No Such Heading"}
        )
    assert result.is_error
    assert "No Such Heading" in result.content[0].text  # type: ignore[union-attr]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_missing_note_is_a_refusal_with_a_next_step(
    client: Client[Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result = await client.call_tool_mcp("read", {"path": "ghost.md"})
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "ghost.md" in text
    assert "search" in text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_stale_if_match_through_a_tool(client: Client[Any]) -> None:
    result = await client.call_tool_mcp(
        "write",
        {"path": "no_frontmatter.md", "content": "# x\n", "if_match": "stale"},
    )
    assert result.is_error
    assert "if_match" in result.content[0].text  # type: ignore[union-attr]


async def test_a_fault_reaches_the_model_without_its_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A git failure's stderr can name server paths; the model never sees it."""
    from fastmcp.exceptions import ToolError
    from fastmcp_pvl_core import tool_boundary

    @tool_boundary
    @library_outcomes
    async def get_diff() -> None:
        raise ValueError("fatal: not a git repository: /srv/secret/vault")

    with caplog.at_level(logging.ERROR), pytest.raises(ToolError) as exc:
        await get_diff()
    assert "/srv/secret" not in str(exc.value)
    assert "request itself was fine" in str(exc.value)
    assert any(r.exc_info for r in caplog.records if r.levelno == logging.ERROR)


# Tools pvl-core registers itself, around no library call of this package.
_PVL_CORE_TOOLS = frozenset(
    {"get_server_info", "create_download_link", "create_upload_link", "get_job_result"}
)


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_every_tool_maps_library_outcomes_inside_its_boundary(
    transport: str, monkeypatch: pytest.MonkeyPatch, vault_path: Any
) -> None:
    """A tool without the mapping would report every refusal as a fault."""
    import asyncio
    import os

    from fastmcp.tools import FunctionTool

    from markdown_vault_mcp.server import make_server

    for key in list(os.environ):
        if key.startswith("MARKDOWN_VAULT_MCP_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    if transport == "http":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "http://127.0.0.1:8000")
    server = make_server(transport=transport)
    tools = asyncio.run(server.local_provider.list_tools())
    missing = sorted(
        tool.name
        for tool in tools
        if tool.name not in _PVL_CORE_TOOLS
        and not (isinstance(tool, FunctionTool) and maps_library_outcomes(tool.fn))
    )
    assert not missing, (
        "tools without @library_outcomes directly under @tool_boundary "
        f"(or under register_long_running_tool): {missing}"
    )


def test_mapping_above_the_boundary_does_not_count() -> None:
    from fastmcp_pvl_core import tool_boundary

    @library_outcomes
    @tool_boundary
    async def upside_down() -> None: ...

    @tool_boundary
    @library_outcomes
    async def right_way_up() -> None: ...

    assert not maps_library_outcomes(upside_down)
    assert maps_library_outcomes(right_way_up)
