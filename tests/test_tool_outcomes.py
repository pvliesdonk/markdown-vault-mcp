"""Every registered tool ends through pvl-core's tool boundary (template-owned).

A tool call that raises anything but a ``ToolError`` reaches FastMCP's own
handler: an ERROR traceback there, and under ``mask_error_details`` a model
that learns nothing about what to do next.  ``fastmcp_pvl_core.tool_boundary``
turns such an exception into one logged server fault and a fixed message
telling the model the request was fine; the ``designing-tool-outcomes`` skill
says how to raise every other outcome.  This test fails a tool registered
without the boundary, so the rule is a build gate rather than a convention.

The registry is enumerated through ``local_provider``, not a client listing,
so a tool hidden by ``MARKDOWN_VAULT_MCP_TOOLS_DENY``, disabled in code, or
visible to an MCP App only is checked too.  The server is built once per
transport, because a ``DOMAIN-WIRING`` block may register tools for HTTP
alone.  A tool that is not a ``FunctionTool`` has no function to wrap and
fails too: it cannot carry the boundary.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
from fastmcp.tools import FunctionTool
from fastmcp_pvl_core import is_tool_boundary

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from fastmcp import FastMCP

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


def _preset_contract_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set every variable the project's ``config_contract_env`` returns.

    The fixture in the project's ``tests/conftest.py`` supplies values for
    the variables its ``from_env`` reads with ``env(..., required=True)``.
    Resolved via ``getfixturevalue`` so a ``conftest.py`` that predates the
    fixture keeps passing with nothing preset.  Repeated verbatim in every
    template-owned test that builds from the environment rather than
    imported: a sibling import only resolves when ``tests/`` is not a package.
    """
    try:
        env = request.getfixturevalue("config_contract_env")
    except pytest.FixtureLookupError:
        return
    for key, value in dict(env).items():
        monkeypatch.setenv(key, value)


@pytest.fixture(params=["stdio", "http"])
def server(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> FastMCP:
    """A default deployment's server for one transport, built outside any loop.

    The HTTP build sets a base URL: HTTP-only wiring such as
    ``register_transfer_routes`` refuses to build without one, and the test
    must reach those tools rather than stop at a configuration error.
    """
    for key in list(os.environ):
        if key.startswith(f"{_ENV_PREFIX}_"):
            monkeypatch.delenv(key, raising=False)
    _preset_contract_env(request, monkeypatch)
    if request.param == "http":
        monkeypatch.setenv(f"{_ENV_PREFIX}_BASE_URL", "http://127.0.0.1:8000")
    return make_server(transport=request.param)


def test_every_tool_is_wrapped_in_tool_boundary(server: FastMCP) -> None:
    """No registered tool lets an unclassified exception reach FastMCP."""
    tools = asyncio.run(server.local_provider.list_tools())
    unwrapped = sorted(
        tool.name
        for tool in tools
        if not (isinstance(tool, FunctionTool) and is_tool_boundary(tool.fn))
    )
    assert not unwrapped, (
        "tools registered without fastmcp_pvl_core.tool_boundary; put "
        "@tool_boundary directly under @mcp.tool and raise "
        "ToolError(msg, log_level=logging.INFO) for outcomes the model can act "
        f"on (the designing-tool-outcomes skill): {unwrapped}"
    )
