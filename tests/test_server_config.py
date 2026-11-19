"""``ServerConfig``: defaults, ``from_env`` and its env suffixes (#2288).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_config.py``
(``TestServerConfigDefaults``, ``TestServerConfigFromEnv``,
``TestServerConfigEnvSuffixes``); its introspection tests are in
``tests/test_config_surface.py`` (#2289).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._auth import build_bearer_auth
from markdown_vault_mcp.config_sections.server import (
    ServerConfig,
    server_config_env_suffixes,
)
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class TestServerConfigDefaults:
    def test_default_transport_is_stdio(self):
        config = ServerConfig()
        assert config.transport == "stdio"

    def test_default_host_port(self):
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_auth_fields_default_to_none(self):
        config = ServerConfig()
        assert config.bearer_token is None
        assert config.oidc_config_url is None
        assert config.oidc_client_id is None
        assert config.oidc_required_scopes == ()

    def test_bearer_tokens_file_defaults_to_none(self):
        assert ServerConfig().bearer_tokens_file is None

    def test_bearer_default_subject_default(self):
        assert ServerConfig().bearer_default_subject == "bearer-anon"

    def test_blank_bearer_default_subject_normalises_at_construction(self):
        # The non-blank invariant lives on the dataclass (``__post_init__``),
        # so direct construction with an empty / whitespace-only value
        # must produce a config carrying the package default.  These
        # assertions are observable on the construction surface itself,
        # without relying on downstream consumers (``build_bearer_auth``)
        # to paper over a blank subject.
        assert ServerConfig(bearer_default_subject="").bearer_default_subject == (
            "bearer-anon"
        )
        assert ServerConfig(bearer_default_subject="   ").bearer_default_subject == (
            "bearer-anon"
        )


class TestServerConfigFromEnv:
    def test_reads_transport(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_TRANSPORT", "http")
        config = ServerConfig.from_env("MYAPP")
        assert config.transport == "http"

    def test_reads_host_port(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_HOST", "0.0.0.0")  # noqa: S104, RUF100 - Asserted default host; S104 is gate-only.
        monkeypatch.setenv("MYAPP_PORT", "9000")
        config = ServerConfig.from_env("MYAPP")
        assert config.host == "0.0.0.0"  # noqa: S104, RUF100 - Asserted default host; S104 is gate-only.
        assert config.port == 9000

    def test_port_unset_defaults_to_8000(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_PORT", raising=False)
        assert ServerConfig.from_env("MYAPP").port == 8000

    def test_malformed_port_raises_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_PORT", "abc")
        with pytest.raises(ConfigurationError) as exc:
            ServerConfig.from_env("MYAPP")
        assert "MYAPP_PORT" in str(exc.value)

    @pytest.mark.parametrize("raw", ["0", "-1", "70000"])
    def test_out_of_range_port_raises_configuration_error(
        self, raw: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_PORT", raw)
        with pytest.raises(ConfigurationError) as exc:
            ServerConfig.from_env("MYAPP")
        assert "MYAPP_PORT" in str(exc.value)

    @pytest.mark.parametrize("raw", ["1", "65535"])
    def test_boundary_ports_accepted(self, raw: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_PORT", raw)
        assert ServerConfig.from_env("MYAPP").port == int(raw)

    def test_shutdown_grace_defaults_to_three(self, monkeypatch):
        monkeypatch.delenv("MYAPP_SHUTDOWN_GRACE_S", raising=False)
        assert ServerConfig.from_env("MYAPP").shutdown_grace_s == 3

    def test_shutdown_grace_read_from_env(self, monkeypatch):
        monkeypatch.setenv("MYAPP_SHUTDOWN_GRACE_S", "30")
        assert ServerConfig.from_env("MYAPP").shutdown_grace_s == 30

    def test_shutdown_grace_zero_is_allowed(self, monkeypatch):
        """0 means "do not drain" — a deliberate choice, not a misconfiguration."""
        monkeypatch.setenv("MYAPP_SHUTDOWN_GRACE_S", "0")
        assert ServerConfig.from_env("MYAPP").shutdown_grace_s == 0

    @pytest.mark.parametrize("value", ["-1", "not-a-number", "3.5"])
    def test_shutdown_grace_rejects_invalid(self, monkeypatch, value):
        monkeypatch.setenv("MYAPP_SHUTDOWN_GRACE_S", value)
        with pytest.raises(ConfigurationError, match="MYAPP_SHUTDOWN_GRACE_S"):
            ServerConfig.from_env("MYAPP")

    def test_reads_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_BEARER_TOKEN", "secret")
        config = ServerConfig.from_env("MYAPP")
        assert config.bearer_token == "secret"

    def test_reads_oidc_vars(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_BASE_URL", "https://x.example")
        monkeypatch.setenv(
            "MYAPP_OIDC_CONFIG_URL",
            "https://idp.example/.well-known/openid-configuration",
        )
        monkeypatch.setenv("MYAPP_OIDC_CLIENT_ID", "cid")
        monkeypatch.setenv("MYAPP_OIDC_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("MYAPP_OIDC_AUDIENCE", "aud.example")
        monkeypatch.setenv("MYAPP_OIDC_JWT_SIGNING_KEY", "sigkey")
        monkeypatch.setenv("MYAPP_OIDC_REQUIRED_SCOPES", "openid profile")
        config = ServerConfig.from_env("MYAPP")
        assert config.base_url == "https://x.example"
        assert (
            config.oidc_config_url
            == "https://idp.example/.well-known/openid-configuration"
        )
        assert config.oidc_client_id == "cid"
        assert config.oidc_client_secret == "csecret"
        assert config.oidc_audience == "aud.example"
        assert config.oidc_jwt_signing_key == "sigkey"
        assert config.oidc_required_scopes == ("openid", "profile")

    def test_reads_advertised_scopes(self, monkeypatch: pytest.MonkeyPatch):
        """The advertised set is read separately from the required one (#280)."""
        monkeypatch.setenv("MYAPP_OIDC_ADVERTISED_SCOPES", "openid, groups")
        config = ServerConfig.from_env("MYAPP")
        assert config.oidc_advertised_scopes == ("openid", "groups")
        assert config.oidc_required_scopes == ()

    def test_advertised_scopes_default_to_empty(self, monkeypatch: pytest.MonkeyPatch):
        """Empty means "pvl-core picks", not "advertise nothing"."""
        monkeypatch.delenv("MYAPP_OIDC_ADVERTISED_SCOPES", raising=False)
        assert ServerConfig.from_env("MYAPP").oidc_advertised_scopes == ()

    def test_reads_event_store_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_EVENT_STORE_URL", "file:///data/events")
        config = ServerConfig.from_env("MYAPP")
        assert config.event_store_url == "file:///data/events"

    def test_reads_kv_store_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_KV_STORE_URL", "redis://localhost:6379/0")
        config = ServerConfig.from_env("MYAPP")
        assert config.kv_store_url == "redis://localhost:6379/0"

    def test_kv_store_url_defaults_to_none(self):
        assert ServerConfig().kv_store_url is None

    def test_reads_app_domain(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_APP_DOMAIN", "mcp.example.com")
        assert ServerConfig.from_env("MYAPP").app_domain == "mcp.example.com"

    def test_oidc_verify_access_token_defaults_to_false(self):
        assert ServerConfig().oidc_verify_access_token is False

    def test_reads_oidc_verify_access_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_OIDC_VERIFY_ACCESS_TOKEN", "true")
        assert ServerConfig.from_env("MYAPP").oidc_verify_access_token is True

    def test_reads_auth_mode(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_AUTH_MODE", "oidc-proxy")
        assert ServerConfig.from_env("MYAPP").auth_mode == "oidc-proxy"

    def test_auth_mode_defaults_to_none(self):
        assert ServerConfig().auth_mode is None

    def test_reads_tools_allow(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_TOOLS_ALLOW", "search, get_item ,fetch")
        config = ServerConfig.from_env("MYAPP")
        assert config.tools_allow == ("search", "get_item", "fetch")
        assert config.tools_deny == ()

    def test_reads_tools_deny(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_TOOLS_DENY", "delete_item,, purge ")
        config = ServerConfig.from_env("MYAPP")
        assert config.tools_deny == ("delete_item", "purge")
        assert config.tools_allow == ()

    def test_tool_lists_default_to_empty(self):
        config = ServerConfig()
        assert config.tools_allow == ()
        assert config.tools_deny == ()

    def test_blank_tool_list_vars_read_as_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_TOOLS_ALLOW", "   ")
        monkeypatch.setenv("MYAPP_TOOLS_DENY", "")
        config = ServerConfig.from_env("MYAPP")
        assert config.tools_allow == ()
        assert config.tools_deny == ()

    def test_tools_allow_set_but_empty_after_parsing_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A lone ',' is not blank, so it must fail fast rather than read as
        unset — the silent reading would expose every tool, the opposite of
        the lockdown the operator was expressing."""
        monkeypatch.setenv("MYAPP_TOOLS_ALLOW", " , ")
        with pytest.raises(ConfigurationError, match="MYAPP_TOOLS_ALLOW is set but"):
            ServerConfig.from_env("MYAPP")

    def test_tools_deny_set_but_empty_after_parsing_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_TOOLS_DENY", ",,")
        with pytest.raises(ConfigurationError, match="MYAPP_TOOLS_DENY is set but"):
            ServerConfig.from_env("MYAPP")

    def test_both_tool_lists_set_raises_naming_the_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_TOOLS_ALLOW", "search")
        monkeypatch.setenv("MYAPP_TOOLS_DENY", "purge")
        with pytest.raises(
            ConfigurationError, match="MYAPP_TOOLS_ALLOW and MYAPP_TOOLS_DENY"
        ):
            ServerConfig.from_env("MYAPP")

    def test_invalid_transport_falls_back_to_stdio(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_TRANSPORT", "websocket")
        assert ServerConfig.from_env("MYAPP").transport == "stdio"

    def test_is_frozen(self):
        config = ServerConfig()
        with pytest.raises(AttributeError):
            config.transport = "http"  # type: ignore[misc]

    def test_reads_bearer_tokens_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        token_file = tmp_path / "tokens.toml"
        token_file.write_text("[tokens]\n", encoding="utf-8")
        monkeypatch.setenv("MYAPP_BEARER_TOKENS_FILE", str(token_file))
        config = ServerConfig.from_env("MYAPP")
        assert config.bearer_tokens_file == token_file

    def test_bearer_tokens_file_keeps_tilde_literal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # ``from_env`` no longer expands ``~`` — the loader is the single
        # expansion site.  Verifies the load-bearing change in this PR:
        # the env-driven path stays literal on the dataclass and only
        # resolves to the on-disk file when it reaches the loader.
        token_file = tmp_path / "tokens.toml"
        token_file.write_text('[tokens]\n"k1" = "user:alice"\n', encoding="utf-8")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("MYAPP_BEARER_TOKENS_FILE", "~/tokens.toml")
        config = ServerConfig.from_env("MYAPP")
        # Field is the literal ``~/tokens.toml`` — not expanded yet.
        assert str(config.bearer_tokens_file) == "~/tokens.toml"
        # Expanding by hand (with the patched ``$HOME``) lands on the
        # actual file the loader will touch.
        assert config.bearer_tokens_file is not None
        assert config.bearer_tokens_file.expanduser() == token_file
        # End-to-end: the loader resolves the tilde and returns a verifier
        # carrying the mapped subject.  Symmetric with the loader-side test
        # in ``test_auth_bearer_tokens_file.py::test_tilde_path_expands_at_load_time``.
        auth = build_bearer_auth(config)
        assert auth is not None
        assert auth.tokens["k1"]["client_id"] == "user:alice"

    def test_reads_bearer_default_subject(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_BEARER_DEFAULT_SUBJECT", "service:bot")
        config = ServerConfig.from_env("MYAPP")
        assert config.bearer_default_subject == "service:bot"

    def test_bearer_default_subject_falls_back_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("MYAPP_BEARER_DEFAULT_SUBJECT", raising=False)
        config = ServerConfig.from_env("MYAPP")
        assert config.bearer_default_subject == "bearer-anon"


def _suffixes_read_by_from_env() -> set[str]:
    """The literal env suffixes the ``ServerConfig`` surface actually reads.

    Statically extracts the second positional argument of each
    ``env``/``env_int``/``env_float`` call **whose suffix is a string
    literal** (calls with a variable, keyword, or attribute-form suffix are
    skipped), scanning ``ServerConfig.from_env`` itself *plus* every
    module-level ``_read_*`` helper function defined in
    ``config_sections.server`` — e.g. ``_read_oidc``, ``_read_bearer``,
    ``_read_tools_visibility``. The helpers are discovered by introspection
    (any module-level function whose name starts with ``_read_``), not a
    hard-coded list, so a newly-added helper is picked up automatically
    without touching this test.

    Deliberately scans only ``from_env`` plus the ``_read_*`` functions,
    not the whole ``_config`` module: those are the functions that
    *constitute* the ``ServerConfig`` read surface by convention, so a
    stray env read added elsewhere in the file later (e.g. inside the
    ``domain_env_suffixes``/``domain_env_surface`` machinery, or a future
    helper that doesn't follow the ``_read_`` naming convention) cannot
    silently join this set just by sharing a module with ``from_env``.
    """
    import ast
    import inspect
    import textwrap

    from markdown_vault_mcp.config_sections import server as _config_module

    read_funcs = {"env", "env_int", "env_float"}

    def _literal_suffixes(func: Callable[..., object]) -> set[str]:
        src = textwrap.dedent(inspect.getsource(func))
        literals: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in read_funcs
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                literals.add(node.args[1].value)
        return literals

    found: set[str] = set(_literal_suffixes(ServerConfig.from_env))
    for name, member in vars(_config_module).items():
        if (
            name.startswith("_read_")
            and inspect.isfunction(member)
            and member.__module__ == _config_module.__name__
        ):
            found |= _literal_suffixes(member)
    return found


class TestServerConfigEnvSuffixes:
    def test_returns_frozenset_with_known_suffixes(self):

        suffixes = server_config_env_suffixes()
        assert isinstance(suffixes, frozenset)
        # Representative members across the categories from_env reads.
        assert {
            "TRANSPORT",
            "HOST",
            "PORT",
            "BASE_URL",
            "BEARER_TOKEN",
            "OIDC_CONFIG_URL",
            "KV_STORE_URL",
            "AUTH_MODE",
        } <= suffixes

    def test_matches_what_from_env_actually_reads(self):
        """Anti-drift: the declared set must equal the literal suffixes from_env reads.

        A literal-string read added/removed/renamed in ``from_env`` — or in a
        ``_read_*`` helper it calls — without updating the declared set fails
        here. (A suffix built from a variable or passed by keyword is
        invisible to the scan — see ``_suffixes_read_by_from_env``.)
        """

        assert server_config_env_suffixes() == _suffixes_read_by_from_env()
