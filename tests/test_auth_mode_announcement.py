"""Tests for the startup auth-mode announcement (issue #310).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_mode_announcement.py`` (#2277).

``build_auth`` is the single place that announces which auth mode a
server resolved, and the only place. ``resolve_auth_mode`` stays silent
so that a caller invoking it directly cannot double-announce.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp._auth import _announce_auth_mode, build_auth, resolve_auth_mode
from markdown_vault_mcp._subject import get_current_auth_mode
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError

_AUTH_LOGGER = "markdown_vault_mcp._auth"


def _is_mode_announcement(record: logging.LogRecord) -> bool:
    """Whether *record* is ``_auth`` announcing a resolved mode.

    Matches on ``auth_mode`` appearing anywhere rather than on the current
    message prefix. The line #310 removed was shaped
    ``auth_mode=<mode> (explicit via AUTH_MODE)``; a filter tied to today's
    wording would let that exact regression back in unseen.

    ``auth_mode_unknown`` is excluded — it reports a rejected ``AUTH_MODE``
    value, not a resolved mode, and legitimately fires from the resolver.
    """
    if record.name != _AUTH_LOGGER:
        return False
    if record.levelno < logging.INFO:
        return False
    message = record.getMessage()
    if message.startswith("auth_mode_unknown"):
        return False
    return "auth_mode" in message


def _announcements(caplog: object) -> list[logging.LogRecord]:
    """Mode announcements captured so far.

    ``caplog`` captures at the root logger regardless of any ``logger=``
    scoping, so sibling loggers land in ``caplog.records`` too;
    :func:`_is_mode_announcement` filters by logger name explicitly rather
    than trusting the scope.
    """
    records: list[logging.LogRecord] = caplog.records  # type: ignore[attr-defined]
    return [r for r in records if _is_mode_announcement(r)]


def _assert_sole_announcement(
    caplog: object,
    *,
    level: int,
    contains: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
) -> logging.LogRecord:
    """Assert exactly one announcement was emitted, and return it.

    The ``len(records) == 1`` half is the point of #310 and is asserted on
    every path rather than only where duplication was observed.
    """
    records = _announcements(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    message = record.getMessage()
    assert record.levelno == level, message
    for fragment in contains:
        assert fragment in message
    for fragment in absent:
        assert fragment not in message
    return record


def _boom(*_args: object, **_kw: object) -> object:
    """``httpx.get`` replacement that fails OIDC discovery."""
    import httpx

    raise httpx.ConnectError("boom")


def _remote_config(**overrides: object) -> ServerConfig:
    base: dict[str, object] = {
        "base_url": "https://mcp.example.com",
        "oidc_config_url": "https://idp.example/.well-known/openid-configuration",
    }
    base.update(overrides)
    return ServerConfig(**base)  # type: ignore[arg-type]


def _mock_discovery() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "jwks_uri": "https://idp.example/jwks",
        "issuer": "https://idp.example/",
    }
    return resp


class TestAnnouncementOnEveryPath:
    def test_auto_detected_mode_is_announced_at_info(self, caplog):
        caplog.set_level(logging.DEBUG)
        build_auth(ServerConfig(bearer_token="x"))

        _assert_sole_announcement(
            caplog,
            level=logging.INFO,
            contains=("mode=bearer-single", "source=auto-detected"),
        )

    def test_explicit_override_is_announced_once_not_twice(self, caplog):
        """Regression guard for the duplicate emission in issue #310."""
        caplog.set_level(logging.DEBUG)
        with patch("httpx.get", return_value=_mock_discovery()):
            build_auth(_remote_config(auth_mode="remote"))

        _assert_sole_announcement(
            caplog, level=logging.INFO, contains=("mode=remote", "source=explicit")
        )

    def test_bearer_mapped_mode_is_announced(self, caplog, tmp_path):
        tokens = tmp_path / "tokens.toml"
        tokens.write_text('[tokens]\n"tok" = "user:alice"\n', encoding="utf-8")
        caplog.set_level(logging.DEBUG)
        build_auth(ServerConfig(bearer_tokens_file=tokens))

        _assert_sole_announcement(
            caplog, level=logging.INFO, contains=("mode=bearer-mapped",)
        )

    def test_normalised_override_still_reports_explicit_provenance(self, caplog):
        """Provenance must survive the case/whitespace normalisation.

        ``resolve_auth_mode`` honours ``"  ReMoTe  "``; if the announcement
        used a different normalisation it would credit auto-detection for a
        mode the operator chose.
        """
        caplog.set_level(logging.DEBUG)
        with patch("httpx.get", return_value=_mock_discovery()):
            build_auth(_remote_config(auth_mode="  ReMoTe  "))

        _assert_sole_announcement(
            caplog, level=logging.INFO, contains=("mode=remote", "source=explicit")
        )

    def test_multi_mode_is_announced(self, caplog):
        caplog.set_level(logging.DEBUG)
        with patch("httpx.get", return_value=_mock_discovery()):
            build_auth(_remote_config(bearer_token="x"))

        _assert_sole_announcement(caplog, level=logging.INFO, contains=("mode=multi",))


class TestUnauthenticatedServersWarn:
    """The level is chosen by the provider, not by the mode.

    This is what lets the equivalent block in the generated server go
    (fastmcp-server-template#605): that block derived its warning from
    ``auth is None``, so a mode-derived level here would be a strictly
    weaker guard than the one being deleted.
    """

    def test_none_mode_warns_with_unauthenticated_notice(self, caplog):
        caplog.set_level(logging.DEBUG)
        build_auth(ServerConfig())

        _assert_sole_announcement(
            caplog, level=logging.WARNING, contains=("mode=none", "unauthenticated")
        )

    def test_level_follows_the_provider_not_the_mode(self, caplog):
        """The predicate PR #317 chose, pinned directly on the announcer.

        Every configuration that used to reach ``build_auth`` with a
        non-``none`` mode and no provider now raises instead (#316), so
        this is the only remaining detector for the provider-vs-mode
        mutation that #317 rejected: keying the level off
        ``mode == "none"`` passes every other test in the suite while
        leaving a providerless ``oidc-proxy`` announced at ``INFO``.
        Calling the announcer directly is what makes the guard
        expressible now that the dispatcher refuses that state.
        """
        caplog.set_level(logging.DEBUG)
        _announce_auth_mode("oidc-proxy", ServerConfig(), None)

        _assert_sole_announcement(
            caplog,
            level=logging.WARNING,
            contains=("mode=oidc-proxy", "unauthenticated"),
        )

    def test_provider_backed_mode_does_not_warn(self, caplog):
        caplog.set_level(logging.DEBUG)
        assert build_auth(ServerConfig(bearer_token="x")) is not None

        _assert_sole_announcement(
            caplog, level=logging.INFO, absent=("unauthenticated",)
        )


class TestFailedBuildStillAnnounces:
    """A builder that raises must still name the mode it was building.

    The line #310 removed fired *before* the discovery call, so an
    operator whose IdP was unreachable at least learned which mode the
    server was in. Announcing only after a successful dispatch would
    have quietly lost that.
    """

    def test_discovery_failure_announces_the_mode_being_built(self, caplog):
        caplog.set_level(logging.DEBUG)
        with (
            patch("httpx.get", side_effect=_boom),
            pytest.raises(ConfigurationError),
        ):
            build_auth(_remote_config(auth_mode="remote"))

        _assert_sole_announcement(
            caplog, level=logging.WARNING, contains=("mode=remote", "source=explicit")
        )

    def test_configured_mode_without_a_provider_is_a_refusal_not_a_warning(
        self, caplog
    ):
        """#316: this configuration used to start a server that accepted anyone.

        ``AUTH_MODE=oidc-proxy`` without client credentials resolves to
        ``oidc-proxy`` and the builder returns ``None``. The invariant in
        ``_build_provider`` now turns that into a refusal, so the
        announcement must take the failed path — announcing it as
        unauthenticated would describe a server that no longer starts.
        """
        caplog.set_level(logging.DEBUG)
        with pytest.raises(ConfigurationError, match="OIDC_CLIENT_ID"):
            build_auth(_remote_config(auth_mode="oidc-proxy"))

        _assert_sole_announcement(
            caplog,
            level=logging.WARNING,
            contains=("mode=oidc-proxy", "source=explicit", "will not start"),
            absent=("unauthenticated",),
        )

    def test_failed_build_is_not_reported_as_unauthenticated(self, caplog):
        """A server that refuses to start is not a server accepting anyone."""
        caplog.set_level(logging.DEBUG)
        with (
            patch("httpx.get", side_effect=_boom),
            pytest.raises(ConfigurationError),
        ):
            build_auth(_remote_config())

        _assert_sole_announcement(
            caplog,
            level=logging.WARNING,
            contains=("will not start",),
            absent=("unauthenticated",),
        )


class TestResolverStaysSilent:
    def test_resolve_auth_mode_alone_announces_nothing(self, caplog):
        """A caller invoking the resolver directly must not announce.

        ``resolve_auth_mode`` is a public export callable any number of
        times; announcing there is what produced the duplicate line.
        """
        caplog.set_level(logging.DEBUG)
        assert resolve_auth_mode(_remote_config(auth_mode="remote")) == "remote"
        assert _announcements(caplog) == []

    def test_unknown_override_still_warns(self, caplog):
        """The unknown-value warning is not an announcement and stays put."""
        caplog.set_level(logging.DEBUG)
        assert resolve_auth_mode(ServerConfig(auth_mode="bogus")) == "none"

        warnings = [
            r
            for r in caplog.records
            if r.name == _AUTH_LOGGER and "auth_mode_unknown" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING


class TestResolvedModeIsRetrievable:
    def test_none_before_build_auth(self):
        assert get_current_auth_mode() is None

    def test_returns_resolved_mode_without_recomputing(self):
        """The seam that lets downstream drop its second resolver call."""
        build_auth(ServerConfig(bearer_token="x"))
        assert get_current_auth_mode() == "bearer-single"

    def test_reports_none_mode_rather_than_absence(self):
        """``none`` is a resolved mode, distinct from "never resolved"."""
        build_auth(ServerConfig())
        assert get_current_auth_mode() == "none"
