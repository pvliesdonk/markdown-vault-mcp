"""Check FastMCP's outer mount pattern, not the standalone CLI deployment.

OAuthProxy stands in for OIDCProxy: route assembly is shared, and no real
identity provider or upstream discovery request is needed for this check.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from httpx import ASGITransport, AsyncClient
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette
from starlette.routing import Mount


def test_mounted_proxies_serve_isolated_discovery_then_initialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discover each service and initialize it with a synthetic verified token."""
    routes = []
    apps = []
    for prefix in ("vault", "other"):
        auth = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example/authorize",
            upstream_token_endpoint="https://idp.example/token",
            upstream_client_id="test-client",
            upstream_client_secret="test-secret",
            token_verifier=JWTVerifier(
                jwks_uri="https://idp.example/jwks", issuer="https://idp.example"
            ),
            base_url=f"https://mcp.example.com/{prefix}",
            jwt_signing_key="x" * 32,
            client_storage=MemoryStore(),
        )
        monkeypatch.setattr(
            auth,
            "verify_token",
            AsyncMock(
                return_value=AccessToken(token="valid", client_id="client", scopes=[])
            ),
        )
        mcp_app = FastMCP(prefix, auth=auth).http_app(path="/mcp")
        apps.append(mcp_app)
        routes.extend(auth.get_well_known_routes(mcp_path="/mcp"))
        routes.append(Mount(f"/{prefix}", app=mcp_app))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for child in apps:
                await stack.enter_async_context(child.router.lifespan_context(child))
            yield

    app = Starlette(routes=routes, lifespan=lifespan)

    async def probe() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://mcp.example.com"
        ) as client:
            assert (
                await client.get("/.well-known/oauth-authorization-server")
            ).status_code == 404
            for prefix in ("vault", "other"):
                issuer = f"https://mcp.example.com/{prefix}"
                metadata = await client.get(
                    f"/.well-known/oauth-authorization-server/{prefix}"
                )
                assert metadata.status_code == 200
                assert metadata.json()["issuer"] == issuer
                assert (
                    metadata.json()["authorization_endpoint"] == f"{issuer}/authorize"
                )
                response = await client.post(f"/{prefix}/mcp")
                assert response.status_code == 401
                resource_path = f"/.well-known/oauth-protected-resource/{prefix}/mcp"
                assert resource_path in response.headers["www-authenticate"]
                resource = await client.get(resource_path)
                assert resource.status_code == 200
                assert resource.json()["resource"] == f"{issuer}/mcp"
                assert resource.json()["authorization_servers"] == [issuer]
                initialized = await client.post(
                    f"/{prefix}/mcp",
                    headers={
                        "authorization": "Bearer valid",
                        "accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                )
                assert initialized.status_code == 200
                assert f'"serverInfo":{{"name":"{prefix}"' in initialized.text

    async def run() -> None:
        async with app.router.lifespan_context(app):
            await probe()

    asyncio.run(run())
