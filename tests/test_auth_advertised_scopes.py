"""Contract tests for the scopes advertised to MCP clients (issue #280).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_advertised_scopes.py`` (#2277).

Protected-resource metadata (RFC 9728) tells a client which scopes to put in
its authorization request. pvl-core keeps that set separate from the token
verifier's ``required_scopes`` — the latter answers "what must a token carry
for this server to accept it", which in ``multi`` mode must be empty so scope-
less bearer tokens are not rejected (#249).

Letting the second answer the first is the bug this file pins: the metadata
advertised ``[]``, the client requested no scopes, the grant carried no
``offline_access``, no refresh token was issued, and every session died at
access-token expiry needing a human at a browser.

The advertised set is asserted where a client actually reads it — over the
real ``/.well-known/oauth-protected-resource/mcp`` route, served through ASGI.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from markdown_vault_mcp._auth import build_auth, build_remote_auth
from markdown_vault_mcp._pvl_core import ServerConfig

_CONFIG_URL = "https://idp.example/.well-known/openid-configuration"
_ISSUER = "https://idp.example/"


class _StubDiscoveryResponse:
    """A canned OIDC discovery document."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._document


@pytest.fixture
def discovery(monkeypatch: pytest.MonkeyPatch):
    """Return a callable that installs a canned discovery document."""
    import httpx

    def _install(**extra: Any) -> None:
        document: dict[str, Any] = {
            "jwks_uri": "https://idp.example/jwks.json",
            "issuer": _ISSUER,
        }
        document.update(extra)
        monkeypatch.setattr(
            httpx, "get", lambda *_a, **_kw: _StubDiscoveryResponse(document)
        )

    return _install


def _remote_config(**overrides: Any) -> ServerConfig:
    base: dict[str, Any] = {
        "base_url": "https://mcp.example.com",
        "oidc_config_url": _CONFIG_URL,
    }
    base.update(overrides)
    return ServerConfig(**base)


async def _published_metadata(auth: Any) -> dict[str, Any]:
    """Fetch the protected-resource metadata the way an MCP client would."""
    app = Starlette(routes=auth.get_routes("/mcp"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://mcp.example.com"
    ) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    return response.json()


class TestRemoteModeAdvertisedScopes:
    async def test_advertises_openid_and_offline_access_by_default(self, discovery):
        """The regression: an empty list here means no refresh token, ever."""
        discovery()
        auth = build_remote_auth(_remote_config())
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]

    async def test_required_scopes_are_always_advertised(self, discovery):
        """A scope we require but never advertise makes every token fail."""
        discovery()
        auth = build_remote_auth(
            _remote_config(oidc_required_scopes=("openid", "groups"))
        )
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access", "groups"]

    async def test_required_scopes_still_gate_token_acceptance(self, discovery):
        """Widening what is advertised must not widen what is accepted."""
        discovery()
        auth = build_remote_auth(_remote_config(oidc_required_scopes=("groups",)))
        assert auth is not None
        assert auth.token_verifier.required_scopes == ["groups"]

    async def test_operator_override_replaces_the_default(self, discovery):
        """A client that is not permitted ``offline_access`` needs an opt-out."""
        discovery()
        auth = build_remote_auth(
            _remote_config(oidc_advertised_scopes=("openid", "profile"))
        )
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "profile"]

    async def test_unsupported_default_scope_is_dropped(self, discovery):
        """Some providers reject the whole request with ``invalid_scope``."""
        discovery(scopes_supported=["openid", "profile", "email"])
        auth = build_remote_auth(_remote_config())
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid"]

    async def test_supported_default_scopes_survive(self, discovery):
        discovery(scopes_supported=["openid", "offline_access", "groups"])
        auth = build_remote_auth(_remote_config())
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]

    def test_dropping_a_default_scope_warns(
        self, discovery, caplog: pytest.LogCaptureFixture
    ):
        """Silently losing refresh capability is what made #280 hard to see."""
        discovery(scopes_supported=["openid"])
        with caplog.at_level("WARNING"):
            build_remote_auth(_remote_config())
        assert any(
            "oidc_advertised_scope_dropped" in record.message
            and "offline_access" in record.message
            for record in caplog.records
        )

    async def test_operator_override_is_not_filtered_by_discovery(self, discovery):
        """Discovery does not know what one client is permitted; the operator does."""
        discovery(scopes_supported=["openid"])
        auth = build_remote_auth(
            _remote_config(oidc_advertised_scopes=("openid", "offline_access"))
        )
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]

    async def test_malformed_discovery_scopes_are_ignored(self, discovery):
        """A non-list ``scopes_supported`` must not silently empty the set."""
        discovery(scopes_supported="openid offline_access")
        auth = build_remote_auth(_remote_config())
        assert auth is not None

        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]


class TestMultiModeAdvertisedScopes:
    """``multi`` mode is where #280 was observed: ``required_scopes=[]``."""

    async def test_advertises_scopes_despite_empty_required_scopes(self, discovery):
        discovery()
        auth = build_auth(
            _remote_config(bearer_token="break-glass"),
        )
        # The load-bearing invariant of #249 is untouched...
        assert auth.required_scopes == []
        # ...and the metadata no longer inherits it.
        metadata = await _published_metadata(auth)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]


class TestOIDCProxyAdvertisedScopes:
    """``oidc-proxy`` mode has the same failure one hop further upstream.

    The proxy mints its own refresh token only when the upstream response
    carried one, and upstream issues one only when ``offline_access`` was
    requested — and what the proxy requests upstream is the scope set it
    advertises. Left at ``required_scopes`` (``["openid"]``) the proxy's own
    sessions expire exactly like the ``remote`` ones in #280.
    """

    @staticmethod
    def _proxy(config: ServerConfig, **discovery_extra: Any) -> Any:
        from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy

        from markdown_vault_mcp._auth import build_oidc_proxy_auth

        document: dict[str, Any] = {
            "issuer": _ISSUER,
            "authorization_endpoint": "https://idp.example/authorize",
            "token_endpoint": "https://idp.example/token",
            "jwks_uri": "https://idp.example/jwks.json",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        document.update(discovery_extra)
        oidc_config = OIDCConfiguration(**document)
        with patch.object(
            OIDCProxy,
            "get_oidc_configuration",
            classmethod(lambda _cls, *_a, **_kw: oidc_config),
        ):
            return build_oidc_proxy_auth(config)

    @staticmethod
    def _proxy_config(**overrides: Any) -> ServerConfig:
        base: dict[str, Any] = {
            "base_url": "https://mcp.example.com",
            "oidc_config_url": _CONFIG_URL,
            "oidc_client_id": "cid",
            "oidc_client_secret": "sec",
        }
        base.update(overrides)
        return ServerConfig(**base)

    async def test_advertises_offline_access(self):
        proxy = self._proxy(self._proxy_config())
        assert proxy is not None

        metadata = await _published_metadata(proxy)
        assert metadata["scopes_supported"] == ["openid", "offline_access"]

    async def test_requests_offline_access_upstream(self):
        """The DCR / authorization default the proxy sends to the provider."""
        proxy = self._proxy(self._proxy_config())
        assert proxy is not None
        assert proxy.client_registration_options is not None
        assert proxy.client_registration_options.default_scopes == [
            "openid",
            "offline_access",
        ]

    async def test_token_acceptance_is_not_widened(self):
        """Advertising ``offline_access`` must not start requiring it in a token.

        The id_token carries no ``scope`` claim, so requiring it here would
        reject every otherwise-valid token.
        """
        proxy = self._proxy(self._proxy_config())
        assert proxy is not None
        assert proxy.required_scopes == ["openid"]

    async def test_unsupported_default_scope_is_dropped(self):
        proxy = self._proxy(
            self._proxy_config(), scopes_supported=["openid", "profile"]
        )
        assert proxy is not None

        metadata = await _published_metadata(proxy)
        assert metadata["scopes_supported"] == ["openid"]

    async def test_operator_override_replaces_the_default(self):
        proxy = self._proxy(
            self._proxy_config(oidc_advertised_scopes=("openid", "email"))
        )
        assert proxy is not None

        metadata = await _published_metadata(proxy)
        assert metadata["scopes_supported"] == ["openid", "email"]
