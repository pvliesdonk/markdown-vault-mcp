"""Wire the ``/transfer`` capability links onto the server.

Ported from ``fastmcp_pvl_core._transfer.register`` 10.2.0 (#2318).

Two entry points share one route mount and one token store:

- :func:`register_transfer_routes` builds the token store, mounts the
  ``/transfer/{token}`` route and registers the two link tools,
  ``create_download_link`` and ``create_upload_link``.
- :func:`build_transfer_links` mounts the route and returns a
  :class:`TransferLinks` minter without registering tools, for a tool the
  generic pair cannot express (the commented example in ``server.py``'s
  DOMAIN-WIRING block). ``register_transfer_routes`` is
  ``build_transfer_links`` plus the two tools.

The instructions builder still comes from pvl-core through the shim, until
#2305 moves it.

How FastMCP builds the tool descriptions, parameter descriptions and
instructions snippets registered here, and how clients read them, is in
``docs/design/reference/mcp-model-facing-text.md``.
"""

from __future__ import annotations

import base64
import inspect
import logging
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from mcp.types import Icon, ToolAnnotations

from markdown_vault_mcp._pvl_core import (
    InstructionRole,
    ServerConfig,
    instructions_for,
)
from markdown_vault_mcp._tool_boundary import tool_boundary
from markdown_vault_mcp._transfer_routes import make_transfer_handler
from markdown_vault_mcp._transfer_store import TransferStore
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from markdown_vault_mcp._transfer_contract import (
        TransferKind,
        TransferSink,
        TransferValidator,
    )
    from markdown_vault_mcp.config_sections import TransferConfig

# Lucide icons (MIT): raw SVG markup, base64-encoded once at import time into
# data URIs, so the tools carry icons with no file-system or network
# dependency. The raw markup stays reviewable in source.
_DOWNLOAD_SVG = (
    '<svg width="24" height="24" xmlns="http://www.w3.org/2000/svg"'
    ' viewBox="0 0 24 24">'
    '<path fill="none" stroke="currentColor" stroke-linecap="round"'
    ' stroke-linejoin="round" stroke-width="2"'
    ' d="M12 15V3m9 12v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
    '<path d="m7 10l5 5l5-5"/>'
    "</svg>"
)
_UPLOAD_SVG = (
    '<svg width="24" height="24" xmlns="http://www.w3.org/2000/svg"'
    ' viewBox="0 0 24 24">'
    '<path fill="none" stroke="currentColor" stroke-linecap="round"'
    ' stroke-linejoin="round" stroke-width="2"'
    ' d="M12 3v12m5-7l-5-5l-5 5m14 7v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
    "</svg>"
)


def _icon(svg: str) -> Icon:
    """Return an :class:`Icon` with a base64 data URI for *svg*."""
    payload = base64.b64encode(svg.encode()).decode("ascii")
    return Icon(src=f"data:image/svg+xml;base64,{payload}", mime_type="image/svg+xml")


_DOWNLOAD_ICON = _icon(_DOWNLOAD_SVG)
_UPLOAD_ICON = _icon(_UPLOAD_SVG)


def _describe(fn: Any, note: str | None) -> str:
    """Compose a tool description from *fn*'s docstring plus an optional *note*.

    The generic description (the docstring text above its ``Args:`` section)
    always comes first and is never altered; a *note* is appended after a
    blank line. An absent note (``None`` or blank) yields the
    generic description unchanged. The ``Args:`` entries stay out of the
    description because FastMCP reads them into each parameter's own
    description, where the model reads them when it fills that argument (see
    ``docs/design/reference/mcp-model-facing-text.md``).
    """
    base = inspect.cleandoc(fn.__doc__ or "").split("\n\nArgs:", 1)[0].rstrip()
    if note and note.strip():
        return f"{base}\n\n{note.strip()}"
    return base


_ROUTE_PATH = "/transfer/{token}"

# Route the methods a client realistically sends a request *body* with, so each
# reaches the handler's own 405 + ``Connection: close`` dispatch (see
# ``make_transfer_handler``'s Note) — an undrained body on a keep-alive socket
# desyncs the next request. GET/POST/PUT are served; DELETE/PATCH fall to the
# handler's closing 405. ``custom_route`` requires ``methods=``, so this is a
# superset rather than "omit it". OPTIONS is deliberately NOT routed here: it must
# stay with Starlette's router so a CORS preflight is answered normally, not
# turned into a 405 (a preflight carries no body). A method not listed (a
# non-preflight OPTIONS with a body, TRACE — which forbids a body per RFC 7231
# §4.3.8 — or an exotic verb) falls to the router's 405 without the close header;
# that residual is accepted. HEAD is auto-added by Starlette for GET.
_ROUTE_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


class TransferLinks:
    """Mints capability links over a mounted ``/transfer`` route and shared store.

    Returned by :func:`build_transfer_links` and
    :func:`register_transfer_routes`. A tool of its own calls
    :meth:`mint_download` / :meth:`mint_upload` with an already-validated
    ``sink_handle``: the opaque routing string the sink interprets, not a
    caller-facing ref. There is no ``validate`` hook here; that tool is the
    validation site.
    """

    def __init__(
        self,
        store: TransferStore,
        *,
        base_url: str,
        transfer_config: TransferConfig,
    ) -> None:
        """Wire the minter.

        Args:
            store: The shared token store.
            base_url: The public base URL, trailing slash stripped.
            transfer_config: The transfer settings (TTL default and maximum).
        """
        self._store = store
        self._base = base_url  # trailing slash already stripped by the factory
        self._transfer_config = transfer_config

    def _clamp_ttl(self, ttl_s: float | None) -> float:
        """Resolve the link TTL: the default when omitted, else clamped to the max."""
        if ttl_s is None:
            return self._transfer_config.ttl_default_s
        return min(ttl_s, self._transfer_config.ttl_max_s)

    async def _mint(
        self, sink_handle: str, kind: TransferKind, ttl_s: float | None
    ) -> dict[str, Any]:
        ttl = self._clamp_ttl(ttl_s)
        token = await self._store.mint(
            kind=kind, sink_handle=sink_handle, caps={}, ttl_seconds=ttl
        )
        return {"url": f"{self._base}/transfer/{token}", "expires_in_s": ttl}

    async def mint_download(
        self, sink_handle: str, ttl_s: float | None = None
    ) -> dict[str, Any]:
        """Mint a download link for an already-validated *sink_handle*.

        *sink_handle* is the opaque routing string the sink interprets (the
        value :func:`register_transfer_routes`'s ``validate`` returns). *ttl_s*
        is the requested lifetime in seconds: omitted uses the configured
        default, over the configured maximum is clamped to it, non-positive is
        rejected. Returns ``{"url", "expires_in_s"}``.
        """
        return await self._mint(sink_handle, "download", ttl_s)

    async def mint_upload(
        self, sink_handle: str, ttl_s: float | None = None
    ) -> dict[str, Any]:
        """Mint an upload link for an already-validated *sink_handle*.

        Same contract as :meth:`mint_download`, for the ``upload`` kind.
        """
        return await self._mint(sink_handle, "upload", ttl_s)


def add_transfer_workflow(
    mcp: FastMCP,
    *,
    upload_tool: str | None = None,
    download_tool: str | None = None,
) -> None:
    """Contribute the capability-link workflow to *mcp*'s server instructions.

    :func:`register_transfer_routes` calls this with the generic tool pair it
    registers. A tool built on :class:`TransferLinks` calls it with its own
    name, so the link semantics are not hand-written twice. The snippet
    declares the names it mentions, so it is pruned whole if an operator hides
    either one.

    Args:
        mcp: The server whose instructions builder receives the snippet.
        upload_tool: The name of the tool that mints upload links, or ``None``
            when this server mints none.
        download_tool: The name of the tool that mints download links, or
            ``None`` when this server mints none.

    Raises:
        ConfigurationError: Neither name is a non-blank string — the snippet
            would then reference no tool and could never be true.
    """
    upload = (upload_tool or "").strip()
    download = (download_tool or "").strip()
    if not upload and not download:
        raise ConfigurationError(
            "add_transfer_workflow needs at least one of upload_tool / "
            "download_tool: a workflow snippet naming no tool cannot be true"
        )

    if upload and download:
        body = (
            f"To upload a file, call {upload} and then PUT the bytes to the "
            f"returned URL; to download, call {download} and GET the returned "
            "URL. Each link is a single-use capability URL that expires: do "
            "not reuse it, share it, or call the link tools speculatively."
        )
    elif upload:
        body = (
            f"To upload a file, call {upload} and then PUT the bytes to the "
            "returned URL. The link is a single-use capability URL that "
            "expires: do not reuse it, share it, or call the tool "
            "speculatively."
        )
    else:
        body = (
            f"To download a file, call {download} and then GET the returned "
            "URL. The link is a single-use capability URL that expires: do "
            "not reuse it, share it, or call the tool speculatively."
        )

    instructions_for(mcp).add(
        body,
        role=InstructionRole.WORKFLOWS,
        requires_tools={name for name in (upload, download) if name},
    )


def build_transfer_links(
    mcp: FastMCP,
    config: ServerConfig,
    transfer_config: TransferConfig,
    *,
    sink: TransferSink,
) -> TransferLinks:
    """Mount the ``/transfer`` route and return a link minter, registering no tools.

    For a transfer tool the generic pair cannot express (a different name, a
    domain-accurate description, domain-specific parameters), built on the
    returned :class:`TransferLinks`. :func:`register_transfer_routes` is this
    function plus the two generic tools, so a server calls one of them, not
    both.

    Args:
        mcp: The FastMCP server to mount the ``/transfer/{token}`` route on.
        config: The server's :class:`ServerConfig` — supplies ``base_url``
            (required, to build link URLs) and ``kv_store_url`` (the token store
            backend).
        transfer_config: The transfer env section (TTL default/max, grace, lease,
            upload cap).
        sink: Where bytes are read from and written to.

    Returns:
        A :class:`TransferLinks` minter over the mounted route and shared store.

    Raises:
        ConfigurationError: If ``config.base_url`` is unset or blank — a transfer
            link cannot be minted without a public base URL, so this fails at
            build time rather than deferring to the first mint.
    """
    if not config.base_url:
        raise ConfigurationError(
            "base_url is required to mint transfer links; set <PREFIX>_BASE_URL"
        )
    base = config.base_url.rstrip("/")
    store = TransferStore.from_config(
        config,
        lease_seconds=transfer_config.lease_s,
        grace_seconds=transfer_config.grace_ttl_s,
    )
    handler = make_transfer_handler(
        store, sink, max_upload_bytes=transfer_config.max_upload_bytes
    )
    mcp.custom_route(_ROUTE_PATH, methods=list(_ROUTE_METHODS))(handler)
    return TransferLinks(store, base_url=base, transfer_config=transfer_config)


def _check_ttl(ttl_s: float | None) -> None:
    """Reject a non-positive ``ttl_s`` as a request the model must change.

    The token store rejects it too, but with a ``ValueError`` that would reach
    the tool boundary as a server fault. The tool descriptions say zero or less
    is rejected, so the model is told which argument to fix.
    """
    if ttl_s is not None and not ttl_s > 0:
        raise ToolError(
            "ttl_s must be greater than 0; omit it to use the server's default "
            "link lifetime.",
            log_level=logging.INFO,
        )


def register_transfer_routes(  # noqa: PLR0913, RUF100 -- ported signature; callers and the server.py example use it
    mcp: FastMCP,
    config: ServerConfig,
    transfer_config: TransferConfig,
    *,
    sink: TransferSink,
    validate: TransferValidator,
    download_note: str | None = None,
    upload_note: str | None = None,
) -> TransferLinks:
    """Register the ``/transfer`` route and the two link tools on *mcp*.

    Args:
        mcp: The FastMCP server to register the route and tools on.
        config: The server's :class:`ServerConfig` — supplies ``base_url``
            (required, to build link URLs) and ``kv_store_url`` (the token
            store backend).
        transfer_config: The transfer env section (TTL default/max, grace,
            lease, upload cap).
        sink: Where bytes are read from and written to.
        validate: Maps a caller ref + kind to a validated opaque
            ``sink_handle``; invoked at link creation. It rejects a ref by
            raising ``ToolError(msg, log_level=logging.INFO)``, whose message
            the model reads; any other exception is a server fault (see
            :data:`TransferValidator`).
        download_note: Optional sentence appended to
            ``create_download_link``'s description. The generic description
            always comes first and is never replaced; this only adds context
            (what a ``ref`` is for this server). Omitted or blank leaves the
            generic description unchanged.
        upload_note: The same for ``create_upload_link``. Usually the more valuable of the two: an
            upload ``ref`` is *authored* by the caller, so stating the
            destination rules here is what a calling model most lacks.

    Returns:
        The :class:`TransferLinks` minter backing the two generic tools; keep it
        to register further tools on the same route and store.

    Raises:
        ConfigurationError: If ``config.base_url`` is unset or blank — a
            transfer link cannot be minted without a public base URL, so this
            fails at registration rather than deferring to the first tool call.
    """
    links = build_transfer_links(mcp, config, transfer_config, sink=sink)

    # The tools are registered by an explicit ``mcp.tool(...)(fn)`` call rather
    # than ``@mcp.tool`` decoration so ``description=`` can be composed from each
    # function's own docstring: a nested closure cannot reference its own
    # ``__doc__`` in its decorator expression. The docstring therefore stays the
    # single source of the generic text: its summary becomes the description (a
    # note is appended) and its ``Args:`` entries the parameter
    # descriptions. Both ship verbatim to the model, so they follow the
    # ``writing-model-facing-text`` skill: no hook names, no reST markup.
    # Each closure validates the caller ``ref`` to an opaque handle, then defers
    # to the shared minter, so every link mints through one code path.
    async def create_download_link(
        ref: str, ttl_s: float | None = None
    ) -> dict[str, Any]:
        """Create a download URL for a file; returns url and expires_in_s.

        Use it to hand a file to the user or another program instead of
        returning its bytes in a tool result.

        Args:
            ref: The file to serve, in this server's reference format.
            ttl_s: Link lifetime in seconds; omit for the server's default. A
                value above the server's maximum is capped, and zero or less
                is rejected.
        """
        _check_ttl(ttl_s)
        handle = await validate(ref, "download")
        return await links.mint_download(handle, ttl_s)

    mcp.tool(
        name="create_download_link",
        description=_describe(create_download_link, download_note),
        annotations=ToolAnnotations(
            title="Create Download Link",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=False,
        ),
        icons=[_DOWNLOAD_ICON],
        # group:transfer keeps the generated reference page at tools/transfer.md.
        tags={"group:transfer"},
    )(tool_boundary(create_download_link))

    async def create_upload_link(
        ref: str, ttl_s: float | None = None
    ) -> dict[str, Any]:
        """Create an upload URL for a file; returns url and expires_in_s.

        Use it to receive a file's bytes instead of passing them as a tool
        argument.

        Args:
            ref: Where to store the uploaded file, in this server's reference
                format.
            ttl_s: Link lifetime in seconds; omit for the server's default. A
                value above the server's maximum is capped, and zero or less
                is rejected.
        """
        _check_ttl(ttl_s)
        handle = await validate(ref, "upload")
        return await links.mint_upload(handle, ttl_s)

    mcp.tool(
        name="create_upload_link",
        description=_describe(create_upload_link, upload_note),
        annotations=ToolAnnotations(
            title="Create Upload Link",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
        ),
        icons=[_UPLOAD_ICON],
        tags={"write", "group:transfer"},
    )(tool_boundary(create_upload_link))

    add_transfer_workflow(
        mcp, upload_tool="create_upload_link", download_tool="create_download_link"
    )

    return links
