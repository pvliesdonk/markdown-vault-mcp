"""Tests for resolve_auth_mode.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_mode.py`` (#2277).
"""

from __future__ import annotations

import pytest

from markdown_vault_mcp._auth import resolve_auth_mode
from markdown_vault_mcp.config_sections.server import ServerConfig


def _cfg(**kwargs) -> ServerConfig:
    return ServerConfig(**kwargs)


class TestResolveAuthMode:
    def test_none_when_no_auth_configured(self):
        assert resolve_auth_mode(_cfg()) == "none"

    def test_bearer_only(self):
        assert resolve_auth_mode(_cfg(bearer_token="x")) == "bearer-single"

    def test_oidc_proxy_when_all_four_oidc_vars_set(self):
        assert (
            resolve_auth_mode(
                _cfg(
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                    oidc_client_id="cid",
                    oidc_client_secret="csecret",
                )
            )
            == "oidc-proxy"
        )

    def test_remote_when_only_base_url_and_config_url(self):
        assert (
            resolve_auth_mode(
                _cfg(
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                )
            )
            == "remote"
        )

    def test_multi_when_bearer_and_oidc_proxy(self):
        assert (
            resolve_auth_mode(
                _cfg(
                    bearer_token="x",
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                    oidc_client_id="cid",
                    oidc_client_secret="csecret",
                )
            )
            == "multi"
        )

    def test_multi_when_bearer_and_remote(self):
        assert (
            resolve_auth_mode(
                _cfg(
                    bearer_token="x",
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                )
            )
            == "multi"
        )

    def test_bearer_mapped_when_tokens_file_set(self, tmp_path):
        token_file = tmp_path / "tokens.toml"
        token_file.write_text('[tokens]\n"abc" = "user:alice"\n', encoding="utf-8")
        cfg = _cfg(bearer_tokens_file=token_file)
        assert resolve_auth_mode(cfg) == "bearer-mapped"

    def test_bearer_mapped_takes_precedence_when_both_set(self, tmp_path):
        token_file = tmp_path / "tokens.toml"
        token_file.write_text('[tokens]\n"abc" = "user:alice"\n', encoding="utf-8")
        cfg = _cfg(bearer_token="x", bearer_tokens_file=token_file)
        assert resolve_auth_mode(cfg) == "bearer-mapped"

    def test_multi_with_bearer_mapped(self, tmp_path):
        token_file = tmp_path / "tokens.toml"
        token_file.write_text('[tokens]\n"abc" = "user:alice"\n', encoding="utf-8")
        cfg = _cfg(
            bearer_tokens_file=token_file,
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
        )
        assert resolve_auth_mode(cfg) == "multi"


class TestExplicitOverride:
    def test_override_remote_when_all_four_oidc_vars_set(self):
        # Explicit 'remote' beats auto-detected 'oidc-proxy' when all four
        # OIDC client-credential vars are set.  This is the primary
        # motivating case for the override: operators who want local JWKS
        # validation instead of proxied DCR even though credentials are
        # available.
        assert (
            resolve_auth_mode(
                _cfg(
                    auth_mode="remote",
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                    oidc_client_id="cid",
                    oidc_client_secret="csecret",
                )
            )
            == "remote"
        )

    def test_override_oidc_proxy_when_only_partial_oidc_config(self):
        # Matches MV semantics (see markdown_vault_mcp/config.py
        # ``resolve_auth_mode``): the override forces the mode even when
        # the downstream builder will fail to construct a provider.
        # Downstream ``build_oidc_proxy_auth`` is responsible for
        # reporting missing fields.  ``resolve_auth_mode`` itself does
        # not gate on field presence.
        assert (
            resolve_auth_mode(
                _cfg(
                    auth_mode="oidc-proxy",
                    base_url="https://x",
                    oidc_config_url="https://idp/.well-known/openid-configuration",
                )
            )
            == "oidc-proxy"
        )

    def test_override_is_case_insensitive_and_trims(self):
        assert resolve_auth_mode(_cfg(auth_mode="  OIDC-PROXY  ")) == "oidc-proxy"
        assert resolve_auth_mode(_cfg(auth_mode="Remote")) == "remote"

    def test_blank_override_falls_back_to_auto_detection(self):
        assert resolve_auth_mode(_cfg(auth_mode="   ")) == "none"

    @pytest.mark.parametrize("bad", ["bearer-single", "multi", "none", "bogus"])
    def test_unknown_override_falls_back_to_auto_detection(self, bad: str):
        # Only ``remote`` and ``oidc-proxy`` are accepted as override
        # values; everything else (including the bearer-flavor literals
        # and previously-accepted ``bearer``/``multi``/``none``) is
        # rejected with a warning and auto-detection runs instead.
        # With ``bearer_token`` set, auto-detection yields
        # ``bearer-single``.
        assert (
            resolve_auth_mode(_cfg(auth_mode=bad, bearer_token="x")) == "bearer-single"
        )

    def test_rejected_override_without_autodetect_candidates_returns_none(self):
        # With no fields configured, the auto-detect fallback for an
        # unrecognized override is ``none``.  Previously ``auth_mode="bearer"``
        # would return ``"bearer"`` here and downstream would silently
        # build no auth provider — the exact silent failure this change
        # prevents.
        assert resolve_auth_mode(_cfg(auth_mode="bearer")) == "none"
