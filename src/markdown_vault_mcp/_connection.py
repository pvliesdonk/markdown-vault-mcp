"""Which protocol revision and client a request arrived over.

Ported from ``fastmcp_pvl_core._connection`` 10.2.0 (#2333) for
``get_server_info``; ``_request_logging`` reads it too. How the SDK populates
the values per protocol era is recorded in fastmcp-pvl-core's
``docs/reference/mcp-protocol-era-identity.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import Context

# The ``_meta`` key a 2026-07-28 client identifies itself under, per
# request. Spelled out rather than imported: it is a wire constant, and
# the SDK exposes it only from a private module.
_CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"


@dataclass(frozen=True)
class ConnectionIdentity:
    """The protocol revision and client identity of one request.

    Each field is ``None`` when the request does not establish it: no
    request context (an in-process call), or a client that did not
    identify itself. A ``None`` is never replaced by a guess.
    """

    protocol_version: str | None = None
    client_name: str | None = None
    client_version: str | None = None


def connection_identity(ctx: Context | None) -> ConnectionIdentity:
    """Read the protocol revision and client identity from *ctx*.

    The revision is the SDK's per-connection value. The client comes from
    the handshake's ``initialize`` params when the SDK holds them (on a
    handshake-era connection once ``initialize`` has been processed, and on
    a modern one that sent both client info and capabilities), and
    otherwise from the request's ``_meta`` client-info entry, which a
    modern client may omit.

    Args:
        ctx: The FastMCP context of the current request, or ``None``.

    Returns:
        The identity; all fields ``None`` when *ctx* carries no request
        context.
    """
    rc = ctx.request_context if ctx is not None else None
    if rc is None:
        return ConnectionIdentity()
    params = rc.session.client_params
    if params is not None:
        info = params.client_info
        return ConnectionIdentity(rc.protocol_version, info.name, info.version)
    raw = (rc.meta or {}).get(_CLIENT_INFO_META_KEY)
    if not isinstance(raw, Mapping):
        return ConnectionIdentity(rc.protocol_version)
    return ConnectionIdentity(
        rc.protocol_version,
        _str_or_none(raw.get("name")),
        _str_or_none(raw.get("version")),
    )


def _str_or_none(value: Any) -> str | None:
    """*value* when it is a string, else ``None``: ``_meta`` is client-supplied."""
    return value if isinstance(value, str) else None
