"""Real server, real visibility, real initialize: the instructions a client
receives are the pruned, finalized text (#2305).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_instructions_integration.py``.
It also shows the job and transfer snippets reach this project's builder.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastmcp import Client, FastMCP

from markdown_vault_mcp._instructions_builder import (
    CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16,
    InstructionRole,
    finalize_instructions,
    instructions_for,
    utf16_code_units,
)
from markdown_vault_mcp._job_store import JOB_POLL_TOOL_NAME
from markdown_vault_mcp._job_tools import register_job_tools, register_long_running_tool
from markdown_vault_mcp._jobs import build_jobs
from markdown_vault_mcp._pvl_core import ServerConfig, apply_tool_visibility
from markdown_vault_mcp._tasks import configure_task_backend
from markdown_vault_mcp._transfer_contract import TransferReadResult
from markdown_vault_mcp._transfer_links import register_transfer_routes
from markdown_vault_mcp.config_sections import JobsConfig, TransferConfig

if TYPE_CHECKING:
    from collections.abc import Mapping


class _Sink:
    async def read(self, _handle: str) -> TransferReadResult:
        return TransferReadResult(b"x", "text/plain", "x.txt")

    async def write(self, handle: str, _body: bytes) -> Mapping[str, Any]:
        return {"stored": handle}


async def _validate(ref: str, kind: str) -> str:
    return f"{kind}:{ref}"


async def _client_view(mcp: FastMCP) -> tuple[str | None, set[str]]:
    """Return negotiated instructions and the real client tool listing.

    ``Client.instructions`` is the era-stable accessor: it is populated
    from the ``InitializeResult`` on the legacy handshake era and from the
    ``DiscoverResult`` on the sessionless era (where ``initialize_result``
    is ``None``).
    """
    async with Client(mcp) as client:
        return (
            client.instructions,
            {tool.name for tool in await client.list_tools()},
        )


def test_client_receives_pruned_finalized_instructions(monkeypatch):
    monkeypatch.setenv("APP_INSTANCE_DESCRIPTION", "Contains demo data.")
    monkeypatch.setenv("APP_INSTRUCTIONS_EXTRA", "Use demo-safe behavior.")
    config = ServerConfig(
        base_url="https://x.example.com",
        kv_store_url="memory://",
        tools_deny=("create_upload_link",),
    )
    mcp = FastMCP("app")
    # Task-enabled tools (register_long_running_tool below) need the tasks
    # extension registered before a client can connect.
    configure_task_backend(mcp, "APP", config)
    builder = instructions_for(mcp)
    builder.identity("app", "A demo server.")
    builder.add("This instance is READ-WRITE.", role=InstructionRole.INSTANCE)
    builder.add("Provides demo operations.", role=InstructionRole.CAPABILITIES)
    builder.documentation("https://example.test/llms.txt")
    register_transfer_routes(
        mcp,
        config,
        TransferConfig(
            ttl_default_s=10,
            ttl_max_s=20,
            grace_ttl_s=5,
            lease_s=5,
            max_upload_bytes=1024,
        ),
        sink=_Sink(),
        validate=_validate,
    )
    jobs = build_jobs(config, JobsConfig(soft_deadline_s=0.1, result_ttl_s=60.0))

    @register_long_running_tool(mcp, jobs, name="slow")
    async def slow() -> str:
        return "done"

    register_job_tools(mcp, jobs)
    apply_tool_visibility(mcp, config)
    text = finalize_instructions(mcp, config, env_prefix="APP")

    received, listed = asyncio.run(_client_view(mcp))

    assert received == text == mcp.instructions
    assert text.startswith("app: A demo server.\n\nContains demo data.")
    assert text.index("Contains demo data.") < text.index(
        "This instance is READ-WRITE."
    )
    assert text.index("This instance is READ-WRITE.") < text.index(
        "Use demo-safe behavior."
    )
    assert text.index("Use demo-safe behavior.") < text.index(
        "Provides demo operations."
    )
    assert text.index("Provides demo operations.") < text.index(JOB_POLL_TOOL_NAME)
    assert JOB_POLL_TOOL_NAME in text  # job tool exposed → snippet kept
    # transfer snippet dropped: upload hidden
    assert "create_download_link" not in text
    assert "create_upload_link" not in listed and JOB_POLL_TOOL_NAME in listed
    assert text.endswith(
        "Full documentation for this server: https://example.test/llms.txt"
    )
    assert utf16_code_units(text) <= CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16
