"""Pin the FastMCP discovery-route behaviour ``docs/deployment/oidc.md`` states.

Template-owned. The "One document still collides" admonition in that page
tells operators that proxy mode serves authorization-server metadata at the
host root whatever prefix ``BASE_URL`` carries: FastMCP's HTTP app mounts
``get_routes()``, so the RFC 8414 path-aware form that
``get_well_known_routes()`` builds is never reachable.  Operators write
reverse-proxy rules from that sentence, and a FastMCP upgrade could change
it silently, so this file fails when a FastMCP bump does and the page needs
rewording (template#656).

``OAuthProxy`` stands in for the ``OIDCProxy`` pvl-core builds: the subclass
only adds OIDC discovery of the upstream endpoints, which would need the
network here, while the route wiring under test lives in the shared base.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from starlette.applications import Starlette

ROOT_FORM = "/.well-known/oauth-authorization-server"
PATH_AWARE_FORM = "/.well-known/oauth-authorization-server/myservice"
IDP = "https://idp.example"
# A placeholder, never a credential: the proxy only needs a non-empty value.
PLACEHOLDER_CLIENT_KEY = "client-secret"


def _proxy() -> OAuthProxy:
    """A proxy-mode provider whose ``BASE_URL`` carries a ``/myservice`` prefix."""
    return OAuthProxy(
        upstream_authorization_endpoint=f"{IDP}/authorize",
        upstream_token_endpoint=f"{IDP}/token",
        upstream_client_id="client-id",
        upstream_client_secret=PLACEHOLDER_CLIENT_KEY,
        token_verifier=JWTVerifier(jwks_uri=f"{IDP}/jwks", issuer=IDP),
        base_url="https://mcp.example.com/myservice",
        jwt_signing_key="x" * 32,
    )


def _status(app: Starlette, url: str) -> int:
    async def _probe() -> int:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get(url)).status_code

    return asyncio.run(_probe())


def test_proxy_mode_serves_authorization_server_metadata_at_the_host_root() -> None:
    app = FastMCP("discovery-probe", auth=_proxy()).http_app(path="/mcp")

    assert _status(app, ROOT_FORM) == 200
    assert _status(app, PATH_AWARE_FORM) == 404, (
        "FastMCP now serves the RFC 8414 path-aware form: reword the "
        "'One document still collides' admonition in docs/deployment/oidc.md"
    )


def test_fastmcp_still_builds_the_unmounted_path_aware_override() -> None:
    routes = [route.path for route in _proxy().get_well_known_routes("/mcp")]

    assert PATH_AWARE_FORM in routes, (
        "OAuthProvider.get_well_known_routes() no longer builds the "
        "path-aware form docs/deployment/oidc.md names"
    )
