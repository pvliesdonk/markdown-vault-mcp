"""Tests for the protocol-revision and client-identity reader (#419).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_connection.py`` (#2333).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import mcp.types as mt
import pytest

from markdown_vault_mcp._connection import ConnectionIdentity, connection_identity

_META_KEY = "io.modelcontextprotocol/clientInfo"


def _ctx(
    *,
    protocol_version: str = "2026-07-28",
    client_params: mt.InitializeRequestParams | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """A stand-in for a FastMCP Context carrying one request context."""
    rc = SimpleNamespace(
        protocol_version=protocol_version,
        session=SimpleNamespace(client_params=client_params),
        meta=meta,
    )
    return SimpleNamespace(request_context=rc)


def _params(name: str, version: str) -> mt.InitializeRequestParams:
    return mt.InitializeRequestParams(
        protocol_version="2025-11-25",
        capabilities=mt.ClientCapabilities(),
        client_info=mt.Implementation(name=name, version=version),
    )


def test_no_context_is_all_none():
    assert connection_identity(None) == ConnectionIdentity()


def test_no_request_context_is_all_none():
    ctx: Any = SimpleNamespace(request_context=None)
    assert connection_identity(ctx) == ConnectionIdentity()


def test_client_params_win_over_meta():
    ctx = _ctx(
        protocol_version="2025-11-25",
        client_params=_params("from-init", "1.0"),
        meta={_META_KEY: {"name": "from-meta", "version": "2.0"}},
    )
    assert connection_identity(ctx) == ConnectionIdentity(
        "2025-11-25", "from-init", "1.0"
    )


def test_meta_client_info_used_without_client_params():
    """A modern client that sent client info but no capabilities leaves the
    SDK's ``client_params`` unset; ``_meta`` still names it."""
    ctx = _ctx(meta={_META_KEY: {"name": "modern", "version": "3.1"}})
    assert connection_identity(ctx) == ConnectionIdentity("2026-07-28", "modern", "3.1")


@pytest.mark.parametrize("meta", [None, {}, {_META_KEY: "not-a-mapping"}])
def test_absent_or_malformed_client_info_leaves_client_none(meta):
    assert connection_identity(_ctx(meta=meta)) == ConnectionIdentity("2026-07-28")


def test_non_string_client_fields_become_none():
    ctx = _ctx(meta={_META_KEY: {"name": 42, "version": "1.0"}})
    assert connection_identity(ctx) == ConnectionIdentity("2026-07-28", None, "1.0")
