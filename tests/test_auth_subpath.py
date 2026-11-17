"""Check FastMCP's outer mount pattern, not the standalone CLI deployment.

OAuthProxy stands in for OIDCProxy: route assembly is shared, and no real
identity provider or upstream discovery request is needed for this check.
"""

import asyncio

from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Mount


def test_mounted_proxies_isolate_discovery_on_a_shared_host() -> None:
    """Each service advertises its own issuer and protected resource."""
    routes = []
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
        )
        mcp_app = FastMCP(prefix, auth=auth).http_app(path="/mcp")
        routes.extend(auth.get_well_known_routes(mcp_path="/mcp"))
        routes.append(Mount(f"/{prefix}", app=mcp_app))
    app = Starlette(routes=routes)

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

    asyncio.run(probe())
