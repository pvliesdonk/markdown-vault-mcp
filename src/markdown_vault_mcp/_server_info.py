"""The ``get_server_info`` tool.

Ported from ``fastmcp_pvl_core._server_info`` 10.2.0 (#2333). The tool reports
the server's own version and, optionally, an upstream service's version: the
one-call check an operator wants when verifying that a fresh image is serving
traffic (a ``docker pull`` without a restart leaves the old one up).

The payload no longer carries ``core_version``: it reported the installed
fastmcp-pvl-core version, which the fold removed (#2283). How FastMCP builds
the tool description and how clients read it is in
``docs/design/reference/mcp-model-facing-text.md``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp._connection import connection_identity
from markdown_vault_mcp._tool_boundary import tool_boundary
from markdown_vault_mcp._url import redact_urls_in_text

if TYPE_CHECKING:
    from fastmcp import Context, FastMCP

logger = logging.getLogger(__name__)

UpstreamResult = Any
"""What an ``upstream_version`` callable may return.

- ``dict`` — full upstream block; used as-is.
- ``None`` — wrapped as ``{"version": None}``.
- Any other value — coerced via ``str()`` and wrapped as
  ``{"version": "<str>"}``.  This covers bare version strings as well as
  ``Path``-like or numeric values.
"""

UpstreamProvider = Callable[[], Any]
"""Sync or async zero-arg callable; the return value (or its awaited
result, if a coroutine is returned) is interpreted per
:data:`UpstreamResult`."""


_RESERVED_KEYS = frozenset({"server_name", "server_version", "protocol"})
"""Keys that ``upstream_label`` must not collide with."""


def _protocol_block(ctx: Context) -> dict[str, Any]:
    """The ``protocol`` block of the payload, for the connection *ctx* is on."""
    # Imported lazily, like the helper's own mcp imports below.
    from mcp.types.version import (
        HANDSHAKE_PROTOCOL_VERSIONS,
        MODERN_PROTOCOL_VERSIONS,
    )

    identity = connection_identity(ctx)
    client = None
    if identity.client_name is not None or identity.client_version is not None:
        client = {"name": identity.client_name, "version": identity.client_version}
    return {
        # The SDK negotiates a handshake-era revision through ``initialize``
        # and a modern one per request; the server accepts the union of both.
        "supported_versions": [*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS],
        "version": identity.protocol_version,
        "client": client,
    }


def register_server_info_tool(  # noqa: PLR0913, RUF100 -- ported keyword-only signature
    mcp: FastMCP,
    *,
    server_version: str,
    server_name: str,
    upstream_version: UpstreamProvider | None = None,
    upstream_label: str = "upstream",
    tool_name: str = "get_server_info",
    description: str | None = None,
    title: str | None = None,
) -> None:
    """Register a ``get_server_info`` tool on a FastMCP instance.

    The registered tool is read-only and returns a structured payload::

        {
          "server_name": "<server_name>",
          "server_version": "<server_version>",
          "protocol": {
            "supported_versions": ["2024-11-05", ..., "2026-07-28"],
            "version": "<revision of this connection>" | None,
            "client": {"name": "...", "version": "..."} | None
          },
          "<upstream_label>": {"version": "..."}   # only when upstream_version set
        }

    ``protocol`` describes the connection the call arrived over.
    ``supported_versions`` lists every revision the installed MCP SDK
    negotiates, oldest first. ``version`` is the revision this connection
    speaks, ``None`` when the call has no request context (an in-process
    ``call_tool``). ``client`` is the client's self-reported name and
    version — from ``initialize`` on a handshake-era connection, from the
    request's ``_meta`` on a 2026-07-28 one — and ``None`` when the client
    did not identify itself; each of its two fields is ``None`` when that
    one was missing.

    If the upstream lookup raises, the upstream block becomes
    ``{"error": "<message>"}`` so the tool still returns the wrapper info
    instead of failing the whole call. The message is the exception's, with
    every URL's userinfo, query and fragment removed: an HTTP client's
    error quotes the request URL, and an operator's upstream URL may carry
    credentials.

    Args:
        mcp: The :class:`FastMCP` instance to register on.
        server_version: The wrapper's own version (typically the domain
            project's ``__version__``).
        server_name: The wrapper's package/distribution name (e.g.
            ``"paperless-mcp"``).
        upstream_version: Optional zero-arg callable (sync or async)
            returning a dict (full upstream block), ``None``, or any other
            value (coerced via ``str()`` and wrapped as
            ``{"version": "<str>"}``).
        upstream_label: Key under which the upstream block appears in the
            response.  Defaults to ``"upstream"``.  Must not collide with
            the reserved keys ``server_name``, ``server_version``,
            or ``protocol``; this is validated eagerly at registration
            time, even when ``upstream_version`` is ``None``.  When no
            provider is configured the label is otherwise unused — no
            block keyed under it appears in the payload.
        tool_name: Tool name to register.  Defaults to ``"get_server_info"``.
        description: Override for the tool description.  Defaults to a
            built-in description that mentions the wrapper name.  Pass
            ``""`` to register an empty description; only ``None`` triggers
            the default.
        title: Human-readable ``annotations.title`` for the tool, used as
            the label by title-aware MCP clients (e.g. VS Code) instead of
            the raw tool name.  Defaults to ``"Server Info"``; pass ``""``
            to register an empty title; only ``None`` triggers the default.

    Raises:
        ValueError: If ``upstream_label`` collides with a reserved payload
            key (``server_name``, ``server_version``,
            ``protocol``).
    """
    if upstream_label in _RESERVED_KEYS:
        raise ValueError(
            f"upstream_label {upstream_label!r} conflicts with reserved "
            f"payload keys {sorted(_RESERVED_KEYS)}"
        )

    # Imported lazily so callers that never use this helper don't pay
    # for the mcp.types import.
    from fastmcp.server.dependencies import get_context
    from mcp.types import ToolAnnotations

    # Model-facing text: what the call returns and when to choose it, in
    # terms the model sees in the result (``writing-model-facing-text``).
    default_description = (
        f"Report the version information of {server_name}; returns "
        "server_name, server_version, the MCP protocol revisions "
        "supported and the revision and client of this connection, and, when "
        "configured, the upstream service's version. Use it when asked which "
        "version or build is running or which protocol revision is in use."
    )

    async def get_server_info() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "server_name": server_name,
            "server_version": server_version,
            "protocol": _protocol_block(get_context()),
        }
        if upstream_version is None:
            return payload

        try:
            result: Any = upstream_version()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # broad: surface as a structured error
            # Redacted on both emit paths, and logged without exc_info: the
            # rendered traceback would print the unredacted message again.
            reason = redact_urls_in_text(str(exc))
            logger.warning(
                "server_info_upstream_lookup_failed error_type=%s error=%s",
                type(exc).__name__,
                reason,
            )
            payload[upstream_label] = {"error": reason}
            return payload

        if isinstance(result, dict):
            payload[upstream_label] = result
        elif result is None:
            payload[upstream_label] = {"version": None}
        else:
            payload[upstream_label] = {"version": str(result)}
        return payload

    # Make tracebacks and repr() reflect the registered tool name rather
    # than always showing ``get_server_info``.
    get_server_info.__name__ = tool_name
    get_server_info.__qualname__ = tool_name

    mcp.tool(
        name=tool_name,
        description=default_description if description is None else description,
        annotations=ToolAnnotations(
            read_only_hint=True,
            title="Server Info" if title is None else title,
        ),
    )(tool_boundary(get_server_info))
