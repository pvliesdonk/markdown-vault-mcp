"""Unit tests for the Principal / identity layer (#1160).

Covers the frozen :class:`~markdown_vault_mcp._identity.Principal` value, its
OKF actor derivation, the contextvar binding helpers, and
:func:`~markdown_vault_mcp._identity.resolve_mcp_principal` — including the
claim-extraction edge cases previously pinned on the git strategy's deleted
``_extract_claim`` (absent claim, empty string, non-string value, no
configured key, no token).

The monkeypatch targets are the ``fastmcp_pvl_core`` package attributes —
enabled by the deliberate function-local imports in ``_identity`` (the same
pattern ``_okf_write`` uses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp import _identity
from markdown_vault_mcp._identity import (
    Principal,
    bound_principal,
    configure_identity_claims,
    current_principal,
    resolve_mcp_principal,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_identity_claims() -> Iterator[None]:
    """Isolate the module-level claim-key registration per test."""
    yield
    configure_identity_claims(name_claim=None, email_claim=None)


def _patch_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subject: str | None,
    claims: dict[str, Any] | None,
) -> None:
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: subject)
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: claims)


class TestPrincipalOkfActor:
    """okf_actor keeps exact parity with resolve_write_actor's rules."""

    def test_human_with_subject_is_human_actor(self) -> None:
        p = Principal(subject="peter", display_name=None, email=None, kind="human")
        assert p.okf_actor("1.2.3") == "human:peter"

    def test_local_kind_is_tool_actor(self) -> None:
        p = Principal(subject=None, display_name=None, email=None, kind="local")
        assert p.okf_actor("1.2.3") == "markdown-vault-mcp/1.2.3"

    def test_human_without_subject_is_tool_actor(self) -> None:
        """Defensive: a subject-less principal never stamps ``human:``."""
        p = Principal(subject=None, display_name="A", email=None, kind="human")
        assert p.okf_actor("1.2.3") == "markdown-vault-mcp/1.2.3"


class TestPrincipalContextVar:
    """bound_principal / current_principal mirror the OKF intent contextvar."""

    def test_unbound_is_none(self) -> None:
        assert current_principal() is None

    def test_bound_value_is_visible_and_reset(self) -> None:
        p = Principal(subject="s", display_name=None, email=None, kind="human")
        with bound_principal(p):
            assert current_principal() is p
        assert current_principal() is None

    def test_nested_bindings_restore_outer(self) -> None:
        outer = Principal(subject="o", display_name=None, email=None, kind="human")
        inner = Principal(subject="i", display_name=None, email=None, kind="human")
        with bound_principal(outer):
            with bound_principal(inner):
                assert current_principal() is inner
            assert current_principal() is outer


class TestResolveMcpPrincipal:
    """resolve_mcp_principal: subject rules + configured-claim extraction."""

    def test_authenticated_subject_makes_human_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_context(monkeypatch, subject="peter", claims={"sub": "peter"})
        p = resolve_mcp_principal()
        assert p.kind == "human"
        assert p.subject == "peter"

    def test_local_sentinel_makes_local_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The auth-mode-none sentinel maps to kind='local', subject=None."""
        _patch_context(monkeypatch, subject="local", claims=None)
        p = resolve_mcp_principal()
        assert p.kind == "local"
        assert p.subject is None

    def test_no_subject_makes_local_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_context(monkeypatch, subject=None, claims=None)
        p = resolve_mcp_principal()
        assert p.kind == "local"
        assert p.subject is None
        assert p.display_name is None
        assert p.email is None

    def test_configured_claims_populate_name_and_email(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure_identity_claims(name_claim="name", email_claim="email")
        _patch_context(
            monkeypatch,
            subject="user123",
            claims={"name": "Alice Human", "email": "alice@humans.org"},
        )
        p = resolve_mcp_principal()
        assert p == Principal(
            subject="user123",
            display_name="Alice Human",
            email="alice@humans.org",
            kind="human",
        )

    def test_unconfigured_claims_stay_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without registered claim keys, name/email are never read."""
        _patch_context(
            monkeypatch, subject="user123", claims={"name": "Alice", "email": "a@b"}
        )
        p = resolve_mcp_principal()
        assert p.display_name is None
        assert p.email is None

    def test_absent_claim_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configure_identity_claims(name_claim="name", email_claim="email")
        _patch_context(monkeypatch, subject="user123", claims={"sub": "user123"})
        p = resolve_mcp_principal()
        assert p.display_name is None
        assert p.email is None

    def test_empty_string_claim_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configure_identity_claims(name_claim="name", email_claim=None)
        _patch_context(monkeypatch, subject="user123", claims={"name": ""})
        assert resolve_mcp_principal().display_name is None

    def test_non_string_claim_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configure_identity_claims(name_claim="email_verified", email_claim=None)
        _patch_context(monkeypatch, subject="user123", claims={"email_verified": True})
        assert resolve_mcp_principal().display_name is None

    def test_no_token_claims_none_is_handled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_claims() -> None (no token) yields None name/email."""
        configure_identity_claims(name_claim="name", email_claim="email")
        _patch_context(monkeypatch, subject="local", claims=None)
        p = resolve_mcp_principal()
        assert p.display_name is None
        assert p.email is None

    def test_claims_without_subject_still_populate_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token with claims but no usable subject keeps the claim fields.

        Matches the pre-#1160 behaviour where the git author claims and the
        OKF subject were independent reads: the commit author can come from
        the claims even when OKF attribution falls back to the tool actor.
        """
        configure_identity_claims(name_claim="name", email_claim=None)
        _patch_context(monkeypatch, subject=None, claims={"name": "Alice"})
        p = resolve_mcp_principal()
        assert p.kind == "local"
        assert p.display_name == "Alice"


class TestOkfResolversPreferBoundPrincipal:
    """_okf_write's resolvers use the bound Principal when one exists (#1160).

    The context-reading fallback path is pinned — unmodified — by
    ``tests/test_okf_write.py``; these tests cover the new preferred branch.
    """

    def test_resolve_write_actor_uses_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp._okf_write import resolve_write_actor

        # get_subject would say something else entirely; the binding wins.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "other")
        p = Principal(subject="peter", display_name=None, email=None, kind="human")
        with bound_principal(p):
            assert resolve_write_actor() == "human:peter"

    def test_resolve_write_actor_local_principal_is_tool_actor(self) -> None:
        from markdown_vault_mcp._okf_write import resolve_write_actor

        p = Principal(subject=None, display_name=None, email=None, kind="local")
        with bound_principal(p):
            assert resolve_write_actor().startswith("markdown-vault-mcp/")

    def test_resolve_human_subject_uses_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp._okf_write import resolve_human_subject

        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "other")
        p = Principal(subject="peter", display_name=None, email=None, kind="human")
        with bound_principal(p):
            assert resolve_human_subject() == "peter"

    def test_resolve_human_subject_local_principal_is_none(self) -> None:
        from markdown_vault_mcp._okf_write import resolve_human_subject

        p = Principal(subject=None, display_name=None, email=None, kind="local")
        with bound_principal(p):
            assert resolve_human_subject() is None


class TestConfigureIdentityClaims:
    def test_registration_is_read_by_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_context(monkeypatch, subject="u", claims={"nickname": "Al"})
        configure_identity_claims(name_claim="nickname", email_claim=None)
        assert resolve_mcp_principal().display_name == "Al"
        configure_identity_claims(name_claim=None, email_claim=None)
        assert resolve_mcp_principal().display_name is None

    def test_module_state_is_set(self) -> None:
        configure_identity_claims(name_claim="a", email_claim="b")
        assert (_identity._name_claim, _identity._email_claim) == ("a", "b")
