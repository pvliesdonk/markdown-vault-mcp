"""Unit tests for the internal helpers extracted from ``_auth.py``.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_internal_helpers.py`` (#2277).

These cover the three helpers carved out of the high-complexity builders
(``resolve_auth_mode``, ``_load_bearer_tokens``, ``build_oidc_proxy_auth``)
directly, at a finer grain than the public-builder tests exercise them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from markdown_vault_mcp._auth import (
    _coerce_token_subject,
    _resolve_explicit_override,
    _warn_oidc_caveats,
)
from markdown_vault_mcp.exceptions import ConfigurationError

# --------------------------------------------------------------------------
# _resolve_explicit_override
# --------------------------------------------------------------------------


def test_override_none_returns_none() -> None:
    assert _resolve_explicit_override(None) is None


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_override_blank_returns_none_without_warning(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        assert _resolve_explicit_override(raw) is None
    assert not [r for r in caplog.records if "auth_mode_unknown" in r.getMessage()]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("remote", "remote"),
        ("oidc-proxy", "oidc-proxy"),
        ("  REMOTE  ", "remote"),
        ("OIDC-Proxy", "oidc-proxy"),
    ],
)
def test_override_valid_is_normalised(raw: str, expected: str) -> None:
    assert _resolve_explicit_override(raw) == expected


@pytest.mark.parametrize("raw", ["bearer-single", "multi", "none", "garbage"])
def test_override_invalid_returns_none_and_warns(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        assert _resolve_explicit_override(raw) is None
    assert [r for r in caplog.records if "auth_mode_unknown" in r.getMessage()]


# --------------------------------------------------------------------------
# _coerce_token_subject
# --------------------------------------------------------------------------

_P = Path("/tmp/tokens.toml")


def test_coerce_subject_valid_returns_subject() -> None:
    assert _coerce_token_subject(_P, "tok", "alice") == "alice"


def test_coerce_subject_blank_token_key_raises() -> None:
    with pytest.raises(ConfigurationError, match="token key is empty"):
        _coerce_token_subject(_P, "   ", "alice")


def test_coerce_subject_nested_table_raises() -> None:
    with pytest.raises(ConfigurationError, match="nested table"):
        _coerce_token_subject(_P, "tok", {"sub": "alice"})


def test_coerce_subject_non_string_raises() -> None:
    with pytest.raises(ConfigurationError, match="must be a string"):
        _coerce_token_subject(_P, "tok", 123)


def test_coerce_subject_empty_string_raises() -> None:
    with pytest.raises(ConfigurationError, match="subject is empty"):
        _coerce_token_subject(_P, "tok", "   ")


# --------------------------------------------------------------------------
# _warn_oidc_caveats
# --------------------------------------------------------------------------


def test_caveats_warns_missing_openid_scope(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        _warn_oidc_caveats(required_scopes=["email"], verify_id_token=True)
    assert [r for r in caplog.records if "scope_warning" in r.getMessage()]


def test_caveats_no_scope_warning_when_openid_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        _warn_oidc_caveats(required_scopes=["openid"], verify_id_token=True)
    assert not [r for r in caplog.records if "scope_warning" in r.getMessage()]


def test_caveats_no_scope_warning_when_verifying_access_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        _warn_oidc_caveats(required_scopes=["email"], verify_id_token=False)
    assert not [r for r in caplog.records if "scope_warning" in r.getMessage()]


def test_caveats_no_ephemeral_warning_regardless_of_signing_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The derived signing key survives a restart (fixed-salt HKDF), so an
    unset ``oidc_jwt_signing_key`` is not a misconfiguration and must never
    warn — regardless of platform, since HKDF derivation is
    platform-independent. ``_warn_oidc_caveats`` no longer accepts a
    ``signing_key`` parameter at all.
    """
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._auth"):
        _warn_oidc_caveats(required_scopes=["openid"], verify_id_token=True)
    assert not [r for r in caplog.records if "ephemeral_signing_key" in r.getMessage()]
