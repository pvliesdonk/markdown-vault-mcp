"""Tests for individual auth builders.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_builders.py`` (#2277).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp._auth import (
    _missing_for_mode,
    build_bearer_auth,
    build_oidc_proxy_auth,
    build_remote_auth,
)
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError


def _oidc_config(**overrides: object) -> ServerConfig:
    """Build a fully-populated OIDC proxy config with optional overrides."""
    base: dict[str, object] = {
        "base_url": "https://mcp.example.com",
        "oidc_config_url": "https://idp.example/.well-known/openid-configuration",
        "oidc_client_id": "test-client",
        "oidc_client_secret": "test-secret",
    }
    base.update(overrides)
    return ServerConfig(**base)  # type: ignore[arg-type]


class TestBuildBearerAuth:
    def test_returns_none_when_no_token(self):
        assert build_bearer_auth(ServerConfig()) is None

    def test_returns_none_when_token_is_whitespace(self):
        assert build_bearer_auth(ServerConfig(bearer_token="   ")) is None

    def test_returns_verifier_when_token_set(self):
        from fastmcp.server.auth import StaticTokenVerifier

        auth = build_bearer_auth(ServerConfig(bearer_token="secret"))
        assert isinstance(auth, StaticTokenVerifier)

    def test_token_mapped_with_read_write_scopes(self):
        auth = build_bearer_auth(ServerConfig(bearer_token="secret"))
        assert auth is not None
        # StaticTokenVerifier exposes a ``tokens`` dict for introspection.
        entry = auth.tokens["secret"]
        assert entry["client_id"] == "bearer-anon"
        assert entry["scopes"] == ["read", "write"]


class TestBuildOIDCProxyAuth:
    def test_returns_none_when_all_vars_missing(self):
        assert build_oidc_proxy_auth(ServerConfig()) is None

    def test_returns_none_when_base_url_only(self):
        assert build_oidc_proxy_auth(ServerConfig(base_url="https://x")) is None

    def test_returns_none_when_secret_missing(self):
        cfg = ServerConfig(
            base_url="https://x.example",
            oidc_config_url="https://idp.example/.well-known/openid-configuration",
            oidc_client_id="cid",
        )
        assert build_oidc_proxy_auth(cfg) is None

    def test_returns_proxy_when_all_vars_set(self):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            auth = build_oidc_proxy_auth(_oidc_config())
        assert auth is mock_cls.return_value
        mock_cls.assert_called_once()

    def test_passes_correct_kwargs(self):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(_oidc_config())
        kw = mock_cls.call_args.kwargs
        assert kw["base_url"] == "https://mcp.example.com"
        assert kw["client_id"] == "test-client"
        assert kw["client_secret"] == "test-secret"
        assert kw["config_url"] == (
            "https://idp.example/.well-known/openid-configuration"
        )
        assert kw["required_scopes"] == ["openid"]
        assert kw["verify_id_token"] is True
        assert kw["require_authorization_consent"] is False

    def test_defaults_required_scopes_to_openid(self):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(_oidc_config())
        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid"]

    def test_custom_scopes_passed_through(self):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(
                _oidc_config(oidc_required_scopes=("openid", "profile"))
            )
        assert mock_cls.call_args.kwargs["required_scopes"] == [
            "openid",
            "profile",
        ]

    def test_verify_id_token_false_when_access_token_opt_in(self):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(_oidc_config(oidc_verify_access_token=True))
        assert mock_cls.call_args.kwargs["verify_id_token"] is False

    def test_scope_warning_when_verify_id_token_without_openid(
        self, caplog: pytest.LogCaptureFixture
    ):
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(_oidc_config(oidc_required_scopes=("profile",)))
        assert any(
            "scope_warning" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )

    def test_no_ephemeral_key_warning_when_signing_key_unset(
        self, caplog: pytest.LogCaptureFixture
    ):
        """The derived signing key survives a restart (fixed-salt HKDF), so
        leaving ``oidc_jwt_signing_key`` unset is not a misconfiguration and
        must never warn, on any platform.
        """
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_proxy_auth(_oidc_config())
        assert not any(
            "ephemeral_signing_key" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )


class TestBuildOIDCProxyAuthDiscoveryFailure:
    """Discovery-time failures inside ``OIDCProxy.__init__`` surface as
    :class:`ConfigurationError`, matching ``build_remote_auth`` (fastmcp-pvl-core#69)."""

    _URL = "https://idp.example/.well-known/openid-configuration"

    def test_network_error_becomes_configuration_error(self):
        # fastmcp's OIDCProxy performs discovery on httpx2 (its own HTTP
        # layer); the raw failure it re-raises is an httpx2 exception.
        import httpx2

        mock_cls = MagicMock(side_effect=httpx2.ConnectError("network down"))
        config = _oidc_config()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            pytest.raises(ConfigurationError) as excinfo,
        ):
            build_oidc_proxy_auth(config)
        assert f"OIDC discovery failed at {self._URL}: network down" in str(
            excinfo.value
        )
        assert isinstance(excinfo.value.__cause__, httpx2.ConnectError)

    def test_invalid_document_becomes_configuration_error(self):
        mock_cls = MagicMock(side_effect=ValueError("Missing required OIDC endpoints"))
        config = _oidc_config()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            pytest.raises(ConfigurationError) as excinfo,
        ):
            build_oidc_proxy_auth(config)
        assert "OIDC discovery failed at" in str(excinfo.value)
        assert "Missing required OIDC endpoints" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ValueError)

    def test_secret_never_enters_message(self):
        import httpx2

        mock_cls = MagicMock(side_effect=httpx2.ConnectError("boom"))
        config = _oidc_config(oidc_client_secret="s3cr3t")
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            pytest.raises(ConfigurationError) as excinfo,
        ):
            build_oidc_proxy_auth(config)
        assert "s3cr3t" not in str(excinfo.value)

    def test_real_proxy_unreachable_idp(self, monkeypatch: pytest.MonkeyPatch):
        """Drive the real ``OIDCProxy`` with ``httpx2.get`` stubbed to fail
        (httpx2 is the HTTP layer fastmcp's discovery fetch runs on)."""
        import httpx2

        def _raise(*_a, **_kw):
            raise httpx2.ConnectError("connection refused")

        monkeypatch.setattr(httpx2, "get", _raise)
        config = _oidc_config()
        with pytest.raises(ConfigurationError, match="OIDC discovery failed at"):
            build_oidc_proxy_auth(config)

    def test_real_proxy_malformed_document(self, monkeypatch: pytest.MonkeyPatch):
        """A discovery document missing endpoints fails validation inside
        the real ``OIDCProxy``; that must also normalise."""
        import httpx2

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"issuer": "https://idp.example"}

        monkeypatch.setattr(httpx2, "get", lambda *_a, **_kw: _Resp())
        config = _oidc_config()
        with pytest.raises(ConfigurationError, match="OIDC discovery failed at"):
            build_oidc_proxy_auth(config)


class TestBuildRemoteAuth:
    def test_returns_none_when_config_missing(self):
        assert build_remote_auth(ServerConfig()) is None

    def test_returns_none_when_only_base_url(self):
        assert build_remote_auth(ServerConfig(base_url="https://x")) is None

    def test_raises_configuration_error_when_httpx_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(sys.modules, "httpx", None)
        with pytest.raises(ConfigurationError, match="requires httpx"):
            build_remote_auth(
                ServerConfig(
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                )
            )

    def test_returns_provider_when_discovery_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import httpx
        from fastmcp.server.auth import RemoteAuthProvider

        class _StubResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {
                    "jwks_uri": "https://idp.example/jwks.json",
                    "issuer": "https://idp.example/",
                }

        def _fake_get(url: str, timeout: int = 10) -> _StubResponse:  # noqa: ARG001 - mirrors httpx.get(url, timeout=...)
            return _StubResponse()

        monkeypatch.setattr(httpx, "get", _fake_get)
        auth = build_remote_auth(
            ServerConfig(
                base_url="https://x.example",
                oidc_config_url=(
                    "https://idp.example/.well-known/openid-configuration"
                ),
            )
        )
        assert isinstance(auth, RemoteAuthProvider)

    def test_raises_when_discovery_missing_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import httpx

        class _StubResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {}  # missing jwks_uri/issuer

        monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _StubResponse())
        with pytest.raises(ConfigurationError, match="incomplete"):
            build_remote_auth(
                ServerConfig(
                    base_url="https://x.example",
                    oidc_config_url=(
                        "https://idp.example/.well-known/openid-configuration"
                    ),
                )
            )

    def test_raises_when_discovery_raises(self, monkeypatch: pytest.MonkeyPatch):
        import httpx

        def _raise(*_a, **_kw):
            raise httpx.ConnectError("network down")

        monkeypatch.setattr(httpx, "get", _raise)
        with pytest.raises(ConfigurationError, match="discovery"):
            build_remote_auth(
                ServerConfig(
                    base_url="https://x.example",
                    oidc_config_url=(
                        "https://idp.example/.well-known/openid-configuration"
                    ),
                )
            )

    def test_raises_when_discovery_json_malformed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import httpx

        class _BadJSONResp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> object:
                raise ValueError("not json")

        monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _BadJSONResp())
        with pytest.raises(ConfigurationError, match="discovery"):
            build_remote_auth(
                ServerConfig(
                    base_url="https://x.example",
                    oidc_config_url=(
                        "https://idp.example/.well-known/openid-configuration"
                    ),
                )
            )


_BUILDER_FOR_MODE = {
    "remote": build_remote_auth,
    "bearer-single": build_bearer_auth,
    "oidc-proxy": build_oidc_proxy_auth,
}


class TestMissingForModeTracksTheBuilders:
    """`_missing_for_mode` must agree with the precondition each builder applies.

    ``build_oidc_proxy_auth`` calls the helper, so it cannot drift.
    ``build_remote_auth`` and ``build_bearer_auth`` test their fields
    inline for the type narrowing, so they can — and a helper that
    disagreed would make ``build_auth`` refuse a mode while naming no
    variable, leaving the operator with nothing to fix (fastmcp-pvl-core#316).
    """

    @pytest.mark.parametrize(
        ("mode", "config", "expected"),
        [
            (
                "remote",
                ServerConfig(auth_mode="remote"),
                ["BASE_URL", "OIDC_CONFIG_URL"],
            ),
            (
                "remote",
                ServerConfig(auth_mode="remote", base_url="https://x.example"),
                ["OIDC_CONFIG_URL"],
            ),
            (
                "bearer-single",
                ServerConfig(bearer_token="   "),
                ["BEARER_TOKEN"],
            ),
            (
                "oidc-proxy",
                ServerConfig(base_url="https://x.example"),
                ["OIDC_CONFIG_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"],
            ),
        ],
    )
    def test_builder_returning_none_is_explained_by_the_helper(
        self, mode, config, expected
    ):
        assert _BUILDER_FOR_MODE[mode](config) is None
        assert _missing_for_mode(config, mode) == expected
