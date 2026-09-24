"""Contract tests for the liveness and readiness routes ``make_server`` wires.

Template-owned. The routes themselves are ``fastmcp_pvl_core``'s and are
tested there; what this file pins is the *wiring* — that the scaffold
registers them under http alone, at the prefix the mount path implies —
because that is what ``compose.yml``'s probe and an operator's
load balancer depend on, and a scaffold that quietly stopped registering
them would still pass every MCP-level test.

Every test is synchronous and builds its server before probing it:
``make_server`` finalises the instructions synchronously and refuses to run
inside an event loop, so the probe, not the construction, is what runs under
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP
    from httpx import Response


def _get(server: FastMCP, url: str, *, http_path: str = "/mcp") -> Response:
    """``GET`` *url* against *server* served as the ASGI app the CLI builds.

    ``ASGITransport`` runs no lifespan, which is fine here: the routes are
    plain ``custom_route`` handlers and the KV probe opens its store lazily.
    """

    async def _probe() -> Response:
        transport = ASGITransport(app=server.http_app(path=http_path))
        async with AsyncClient(transport=transport, base_url="http://probe") as client:
            return await client.get(url)

    return asyncio.run(_probe())


@pytest.fixture(autouse=True)
def _kv_store_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pin the readiness probe's backend to a temp directory.

    Unset, pvl-core defaults to ``file:///data/state`` where that directory
    is usable and ``memory://`` elsewhere — which would make the readiness
    verdict depend on the host running the tests.
    """
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_KV_STORE_URL",
        f"file://{tmp_path / 'kv'}",
    )


def test_liveness_answers_outside_the_mcp_mount() -> None:
    """``/health`` is a static 200 carrying the server's identity."""
    response = _get(make_server(transport="http"), "/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["server"]["name"] == "markdown-vault-mcp"
    assert "version" in body["server"]


def test_readiness_runs_the_kv_store_check() -> None:
    """``/health/ready`` reports the built-in ``kv_store`` verdict.

    A 200 with ``kv_store: true`` means the write probe reached the temp
    backend the fixture configured — the route is wired *and* the server's
    own KV configuration is the one it probes.
    """
    response = _get(make_server(transport="http"), "/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["kv_store"] is True


def test_health_prefix_follows_the_mount_path() -> None:
    """A namespaced mount publishes namespaced routes, and nothing at the root.

    ``make_server`` and ``http_app`` receive the same path, as ``serve`` passes
    it; the routes land under the namespace so two servers sharing a hostname
    never collide on ``/health``.
    """
    mount = "/markdown-vault-mcp/mcp"
    # Hoisted so the assert's length does not grow with ``project_name``: an
    # inline literal pushed the line past ruff's 88 for names over 17
    # characters, and ``ruff format`` rewrapped every such render (#649).
    health = "/markdown-vault-mcp/health"
    server = make_server(transport="http", http_path=mount)
    assert _get(server, health, http_path=mount).status_code == 200
    assert _get(server, "/health", http_path=mount).status_code == 404


def test_mount_path_env_var_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit ``http_path``, ``MARKDOWN_VAULT_MCP_HTTP_PATH`` decides.

    This is the same fallback ``serve`` applies, so a server built directly
    and one built by the CLI publish the routes at the same place.
    """
    mount = "/markdown-vault-mcp/mcp"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_HTTP_PATH", mount)
    health = "/markdown-vault-mcp/health"
    server = make_server(transport="http")
    assert _get(server, health, http_path=mount).status_code == 200


@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_other_transports_register_no_health_routes(transport: str) -> None:
    """Only http registers them: under stdio there is no HTTP app to serve
    them, and under sse the CLI never hands the server a mount path, so the
    derived prefix would be a guess."""
    assert _get(make_server(transport=transport), "/health").status_code == 404
