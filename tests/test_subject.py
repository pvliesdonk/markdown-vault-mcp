"""``get_subject`` and ``get_claims`` (#2350).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_subject.py``.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from markdown_vault_mcp._auth import build_auth
from markdown_vault_mcp._subject import get_claims, get_subject
from markdown_vault_mcp.config_sections.server import ServerConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

_UNSET = object()


class _FakeAccessToken:
    """Minimal stand-in for fastmcp.server.auth.AccessToken."""

    def __init__(
        self,
        client_id: str | None = None,
        claims: object = _UNSET,
    ) -> None:
        self.client_id = client_id
        self.claims = {} if claims is _UNSET else claims


@pytest.fixture
def patch_get_access_token() -> Callable[
    [_FakeAccessToken | None], AbstractContextManager[Any]
]:
    """Patch FastMCP's get_access_token to return a controllable value."""

    def _set(token: _FakeAccessToken | None) -> AbstractContextManager[Any]:
        return patch("markdown_vault_mcp._subject.get_access_token", return_value=token)

    return _set


class TestGetSubjectAuthModeNone:
    def test_returns_local_when_no_token_and_mode_none(self, patch_get_access_token):
        # Simulate auth_mode=none after build_auth
        build_auth(ServerConfig())
        with patch_get_access_token(None):
            assert get_subject() == "local"


class TestGetSubjectBearerSingle:
    def test_returns_default_subject_from_client_id(self, patch_get_access_token):
        cfg = ServerConfig(bearer_token="x", bearer_default_subject="bearer-anon")
        build_auth(cfg)
        with patch_get_access_token(_FakeAccessToken(client_id="bearer-anon")):
            assert get_subject() == "bearer-anon"


class TestGetSubjectBearerMapped:
    def test_returns_mapped_subject(self, patch_get_access_token, tmp_path):
        token_file = tmp_path / "tokens.toml"
        token_file.write_text(
            '[tokens]\n"k1" = "user:alice@example.com"\n', encoding="utf-8"
        )
        cfg = ServerConfig(bearer_tokens_file=token_file)
        build_auth(cfg)
        with patch_get_access_token(
            _FakeAccessToken(client_id="user:alice@example.com")
        ):
            assert get_subject() == "user:alice@example.com"


class TestGetSubjectOIDC:
    def test_returns_sub_claim(self, patch_get_access_token):
        # Mode pointer is irrelevant here — claims["sub"] always wins.
        with patch_get_access_token(
            _FakeAccessToken(
                client_id="oidc-client-x",
                claims={"sub": "user:bob@example.com"},
            )
        ):
            assert get_subject() == "user:bob@example.com"

    def test_falls_back_to_client_id_when_sub_missing(self, patch_get_access_token):
        with patch_get_access_token(
            _FakeAccessToken(client_id="oidc-client-x", claims={})
        ):
            assert get_subject() == "oidc-client-x"

    def test_empty_sub_falls_back_to_client_id(self, patch_get_access_token):
        # ``isinstance(sub, str) and sub`` rejects empty strings; the
        # fallback to ``client_id`` must engage.
        with patch_get_access_token(
            _FakeAccessToken(client_id="oidc-client-x", claims={"sub": ""})
        ):
            assert get_subject() == "oidc-client-x"

    def test_returns_none_when_sub_and_client_id_both_empty(
        self, patch_get_access_token
    ):
        with patch_get_access_token(_FakeAccessToken(client_id="", claims={"sub": ""})):
            assert get_subject() is None

    def test_returns_none_when_sub_missing_and_client_id_none(
        self, patch_get_access_token
    ):
        with patch_get_access_token(_FakeAccessToken(client_id=None, claims={})):
            assert get_subject() is None


class TestGetSubjectMissing:
    def test_returns_none_when_no_token_and_auth_configured(
        self, patch_get_access_token
    ):
        # Simulate any auth mode != "none"
        build_auth(ServerConfig(bearer_token="t"))
        with patch_get_access_token(None):
            assert get_subject() is None


class TestExplicitContextIsolation:
    def test_two_build_auth_calls_in_separate_contexts_dont_clobber(
        self, patch_get_access_token
    ):
        """Per-context isolation regression guard.

        Exercises the explicit ``contextvars.copy_context().run(...)``
        isolation pattern — each context gets its own resolved mode
        and a later ``build_auth`` in a sibling context does NOT
        bleed into a prior context's reads.

        With the old module-global storage this test FAILED at the
        first assertion (``observed["a"] == "local"``): ``write_b``
        would clobber the global to ``"bearer-single"``, and ``read_a``
        would observe that value, returning ``None`` instead of
        ``"local"``.  Under the ContextVar each context retains its own
        value.  The test does **all writes first, then all reads** so a
        within-context happy path (write-then-immediately-read) cannot
        mask the bug.

        Note: this protects test isolation and explicitly-isolated
        harnesses.  Two ``build_auth`` calls in the *same* context
        (e.g. sequential calls in the same ``main()``) still see
        last-writer-wins — see the module docstring on ``_subject.py``
        for caller guidance.
        """
        # Two independent contexts: each will call ``build_auth`` with
        # a different config, then both will be read after both writes
        # have happened.
        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()

        observed: dict[str, str | None] = {}

        def write_a() -> None:
            build_auth(ServerConfig())  # mode == "none"

        def write_b() -> None:
            build_auth(ServerConfig(bearer_token="t"))  # mode == "bearer-single"

        def read_a() -> None:
            with patch_get_access_token(None):
                observed["a"] = get_subject()

        def read_b() -> None:
            with patch_get_access_token(None):
                observed["b"] = get_subject()

        # All writes first — under module-global, ``write_b`` would
        # clobber ``write_a``.  Under ContextVar, each context retains
        # its own value.
        ctx_a.run(write_a)
        ctx_b.run(write_b)

        # Now the reads: ``ctx_a`` must still see ``"none"`` even though
        # ``ctx_b`` last set ``"bearer-single"``.
        ctx_a.run(read_a)
        ctx_b.run(read_b)

        assert observed["a"] == "local"
        assert observed["b"] is None


class TestGetClaims:
    def test_returns_claims_dict_when_token_has_claims(self, patch_get_access_token):
        claims = {"sub": "u1", "name": "Alice", "email": "alice@example.com"}
        with patch_get_access_token(_FakeAccessToken(claims=claims)):
            assert get_claims() == claims

    def test_returns_empty_dict_when_token_has_no_claims(self, patch_get_access_token):
        with patch_get_access_token(_FakeAccessToken(client_id="x", claims={})):
            assert get_claims() == {}

    def test_returns_empty_dict_when_claims_field_is_none(self, patch_get_access_token):
        with patch_get_access_token(_FakeAccessToken(claims=None)):
            assert get_claims() == {}

    def test_returns_empty_dict_when_claims_field_is_non_dict(
        self, patch_get_access_token
    ):
        with patch_get_access_token(_FakeAccessToken(claims=["unexpected"])):
            assert get_claims() == {}

    def test_returns_none_when_no_token_in_auth_mode(self, patch_get_access_token):
        build_auth(ServerConfig(bearer_token="t"))
        with patch_get_access_token(None):
            assert get_claims() is None

    def test_returns_none_when_no_token_in_no_auth_mode(self, patch_get_access_token):
        # Unlike get_subject(), get_claims() always returns None when there is
        # no token — there are no claims to return regardless of auth mode.
        build_auth(ServerConfig())
        with patch_get_access_token(None):
            assert get_claims() is None

    def test_returns_full_dict_including_non_string_values(
        self, patch_get_access_token
    ):
        claims = {"sub": "u1", "groups": ["admin", "editor"], "exp": 9999999999}
        with patch_get_access_token(_FakeAccessToken(claims=claims)):
            assert get_claims() == claims


# Note: ``multi`` mode is not exercised here. When a token is present,
# the bearer-vs-OIDC distinction flows through the token's ``claims`` and
# ``client_id`` (not the resolved auth mode), so the OIDC and bearer
# tests above already cover both runtime paths in ``multi`` deployments.
