"""Server configuration: transport, host, port, auth, stores, tool visibility.

Ported from ``fastmcp_pvl_core._config`` 10.2.0 (#2288), up to and including
``server_config_env_suffixes``; the config-surface introspection
(``server_config_surface``, ``domain_env_surface``) stays in pvl-core until
#2289. ``ProjectConfig`` composes :class:`ServerConfig` as its ``server`` field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from markdown_vault_mcp._env import (
    _resolve_key,
    env,
    env_int,
    parse_bool,
    parse_list,
    parse_scopes,
)
from markdown_vault_mcp.exceptions import ConfigurationError

Transport = Literal["stdio", "http", "sse"]

# Default subject string assigned to the single bearer-token mode (and
# the bearer leg of ``multi`` mode when only ``bearer_token`` is set).
# Mapped mode uses per-token subjects from the TOML file and ignores
# this default. Referenced from the ``ServerConfig`` field default,
# the env-loading fallback, and the ``__post_init__`` non-blank guard
# below — three call sites, one source of truth.
DEFAULT_BEARER_SUBJECT = "bearer-anon"


class _OIDCFields(NamedTuple):
    """Parsed OIDC field group, assembled by :func:`_read_oidc`.

    Purely an internal handoff between :meth:`ServerConfig.from_env` and
    the ``ServerConfig(...)`` constructor call; not part of the public
    surface.
    """

    config_url: str | None
    client_id: str | None
    client_secret: str | None
    audience: str | None
    required_scopes: tuple[str, ...]
    advertised_scopes: tuple[str, ...]
    jwt_signing_key: str | None
    verify_access_token: bool


class _BearerFields(NamedTuple):
    """Parsed bearer-auth field group, assembled by :func:`_read_bearer`."""

    token: str | None
    tokens_file: Path | None
    default_subject: str


def _read_transport(transport_raw: str) -> Transport:
    """Resolve the ``TRANSPORT`` ladder.

    Unknown values silently fall back to ``"stdio"`` — string fields
    prefer permissive defaults over raising.
    """
    if transport_raw == "http":
        return "http"
    if transport_raw == "sse":
        return "sse"
    return "stdio"


def _read_oidc(env_prefix: str) -> _OIDCFields:
    """Read and assemble the OIDC field group from ``{env_prefix}_OIDC_*``.

    Only the two scope lists and the verify-access-token bool need
    parsing; the rest pass through unchanged. Grouped here so the OIDC
    block reads as one cohesive unit, and so :meth:`ServerConfig.from_env`
    only has to call this once instead of threading eight already-read
    raw values through as parameters. Each read stays a literal
    ``env(...)`` call in this function's own source, which is exactly
    what the widened drift guard scans (see the comment above
    ``_SERVER_CONFIG_ENV_SUFFIXES``).
    """
    verify_access_raw = env(env_prefix, "OIDC_VERIFY_ACCESS_TOKEN")
    required_scopes = tuple(parse_scopes(env(env_prefix, "OIDC_REQUIRED_SCOPES")) or ())
    advertised_scopes = tuple(
        parse_scopes(env(env_prefix, "OIDC_ADVERTISED_SCOPES")) or ()
    )
    verify_access_token = (
        parse_bool(verify_access_raw) if verify_access_raw is not None else False
    )
    return _OIDCFields(
        config_url=env(env_prefix, "OIDC_CONFIG_URL"),
        client_id=env(env_prefix, "OIDC_CLIENT_ID"),
        client_secret=env(env_prefix, "OIDC_CLIENT_SECRET"),
        audience=env(env_prefix, "OIDC_AUDIENCE"),
        required_scopes=required_scopes,
        advertised_scopes=advertised_scopes,
        jwt_signing_key=env(env_prefix, "OIDC_JWT_SIGNING_KEY"),
        verify_access_token=verify_access_token,
    )


def _read_bearer(env_prefix: str) -> _BearerFields:
    """Read and assemble the bearer-auth field group from ``{env_prefix}_BEARER_*``.

    ``Path(...)`` keeps a leading ``~`` literal here. Expansion is
    performed once, in :func:`markdown_vault_mcp._auth._load_bearer_tokens`,
    so both this env-driven path and a directly-constructed
    ``ServerConfig(bearer_tokens_file=Path("~/tokens.toml"))`` resolve
    the tilde at the same call site.
    """
    tokens_file_raw = env(env_prefix, "BEARER_TOKENS_FILE")
    tokens_file = Path(tokens_file_raw) if tokens_file_raw else None
    return _BearerFields(
        token=env(env_prefix, "BEARER_TOKEN"),
        tokens_file=tokens_file,
        default_subject=env(
            env_prefix, "BEARER_DEFAULT_SUBJECT", DEFAULT_BEARER_SUBJECT
        ),
    )


def _parse_tool_list(
    raw: str | None, env_key: str, consequence: str
) -> tuple[str, ...]:
    """Parse a comma-separated tool list, rejecting a "set but empty" value.

    Shared by both halves of :func:`_read_tools_visibility`: "set but
    parses to zero names" (e.g. a lone ``,``) is rejected rather than
    treated as unset, because for ``TOOLS_ALLOW`` the silent reading
    would expose every tool — the exact opposite of the lockdown the
    operator was expressing. ``TOOLS_DENY`` gets the same guard for
    symmetry. *consequence* completes "unset it to {consequence}."

    Raises:
        ConfigurationError: If *raw* is set but parses to zero tool names.
    """
    parsed = tuple(parse_list(raw)) if raw else ()
    if raw and not parsed:
        raise ConfigurationError(
            f"{env_key} is set but contains no tool names; unset it to {consequence}."
        )
    return parsed


def _read_tools_visibility(
    env_prefix: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read and validate the ``TOOLS_ALLOW`` / ``TOOLS_DENY`` pair.

    See :func:`_parse_tool_list` for the per-var "set but empty" rule.

    Raises:
        ConfigurationError: If either var is set but parses to zero tool
            names, or if both vars are set.
    """
    tools_allow = _parse_tool_list(
        env(env_prefix, "TOOLS_ALLOW"),
        _resolve_key(env_prefix, "TOOLS_ALLOW"),
        "expose all tools",
    )
    tools_deny = _parse_tool_list(
        env(env_prefix, "TOOLS_DENY"),
        _resolve_key(env_prefix, "TOOLS_DENY"),
        "hide no tools",
    )

    if tools_allow and tools_deny:
        raise ConfigurationError(
            f"{_resolve_key(env_prefix, 'TOOLS_ALLOW')} and "
            f"{_resolve_key(env_prefix, 'TOOLS_DENY')} are both set; "
            "set at most one — an allowlist already expresses every "
            "exclusion."
        )
    return tools_allow, tools_deny


@dataclass(frozen=True)
class ServerConfig:
    """Universal fields every FastMCP server needs.

    Compose into a domain config; never inherit from this class.
    """

    transport: Transport = field(
        default="stdio",
        metadata={
            "help": (
                "Transport the server speaks: ``stdio`` for local Claude "
                "Desktop/Code, ``http`` or ``sse`` for a network server."
            ),
            "tags": ("server",),
            # Emitted by a routing select in the wizard, not a free-text field.
            "wizard": {"control": "emit"},
        },
    )
    host: str = field(
        default="127.0.0.1",
        metadata={
            "help": "Interface the HTTP server binds to.",
            "tags": ("server",),
            "wizard": {"group": "Server", "when": "server"},
        },
    )
    port: int = field(
        default=8000,
        metadata={
            "help": "TCP port for the HTTP server.",
            "tags": ("server",),
            "wizard": {"group": "Server", "when": "server"},
        },
    )
    shutdown_grace_s: int = field(
        default=3,
        metadata={
            "help": (
                "Seconds SIGTERM may spend draining in-flight requests "
                "before the HTTP server exits. Keep it at or below the "
                "termination grace period the orchestrator allows. ``0`` "
                "drops in-flight requests immediately."
            ),
            "tags": ("server",),
            "wizard": {"group": "Server", "when": "server"},
        },
    )
    base_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "Public base URL of the deployed server, for example "
                "``https://mcp.example.com``. Required for OIDC. Also the "
                "fallback source of the MCP Apps domain when ``app_domain`` "
                "is unset."
            ),
            "tags": ("server", "oidc", "apps"),
            "wizard": {"when": "server"},
        },
    )

    bearer_token: str | None = field(
        default=None,
        metadata={
            "help": (
                "Single shared bearer token; enables bearer auth unless "
                "``bearer_tokens_file`` is set, which takes precedence."
            ),
            "tags": ("auth", "bearer"),
            "wizard": {"when": "bearer", "secret": True},
        },
    )

    oidc_config_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "OIDC discovery document URL, for example "
                "``https://auth.example.com/.well-known/openid-configuration``."
            ),
            "tags": ("auth", "oidc"),
            "wizard": {"when": "oidc"},
        },
    )
    oidc_client_id: str | None = field(
        default=None,
        metadata={
            "help": "OIDC client identifier registered with the provider.",
            "tags": ("auth", "oidc"),
            "wizard": {"when": "oidc"},
        },
    )
    oidc_client_secret: str | None = field(
        default=None,
        metadata={
            "help": "OIDC client secret registered with the provider.",
            "tags": ("auth", "oidc"),
            "wizard": {"when": "oidc", "secret": True},
        },
    )
    oidc_audience: str | None = field(
        default=None,
        metadata={
            "help": (
                "Expected ``aud`` claim; tokens issued for another audience "
                "are rejected."
            ),
            "tags": ("auth", "oidc"),
            "wizard": {"group": "Auth", "when": "oidc"},
        },
    )
    oidc_required_scopes: tuple[str, ...] = field(
        default_factory=tuple,
        metadata={
            "help": (
                "Scopes a caller must present, space- or comma-separated. "
                "Defaults to ``openid`` in oidc-proxy mode."
            ),
            "tags": ("auth", "oidc"),
            "wizard": {"group": "Auth", "when": "oidc"},
        },
    )
    oidc_advertised_scopes: tuple[str, ...] = field(
        default_factory=tuple,
        metadata={
            "help": (
                "Scopes advertised to MCP clients in protected-resource "
                "metadata, space- or comma-separated. Overrides the default "
                "``openid offline_access``; ``oidc_required_scopes`` is always "
                "added on top. Set this when the registered client is not "
                "permitted ``offline_access``, or to have clients request "
                "extra claim scopes (such as ``groups``) without also requiring "
                "them in every token."
            ),
            "tags": ("auth", "oidc"),
            "wizard": {"group": "Auth", "when": "oidc"},
        },
    )
    oidc_jwt_signing_key: str | None = field(
        default=None,
        metadata={
            "help": (
                "Signing key for issued tokens; used in oidc-proxy mode only. "
                "When unset, the key is derived deterministically from "
                "``oidc_client_secret``, so tokens survive a restart. Rotating "
                "that secret then invalidates every issued token. Set "
                "this explicitly to decouple token validity from secret "
                "rotation. Generate with ``openssl rand -hex 32``."
            ),
            "tags": ("auth", "oidc"),
            "wizard": {"when": "oidc", "secret": True},
        },
    )
    oidc_verify_access_token: bool = field(
        default=False,
        metadata={
            "help": "Validate the access token instead of the id token.",
            "tags": ("auth", "oidc"),
            "wizard": {"group": "Auth", "when": "oidc"},
        },
    )

    kv_store_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "Persistent-state backend URL shared by every part of the "
                "server that needs state. ``memory://`` is in-process and "
                "lost on restart; ``file:///path`` persists on one server; "
                "``redis://``, ``dynamodb://`` and ``mongodb://`` each need "
                "their matching extra. When unset, defaults to "
                "``file:///data/state`` (the volume family Docker images "
                "mount), or to ``memory://`` (with a warning) on a host "
                "where that directory is not usable."
            ),
            "tags": ("persistence", "readme"),
            "wizard": {"group": "Persistence", "when": "server"},
        },
    )
    event_store_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "Legacy state-backend override, used by "
                "``build_event_store`` and ``build_kv_store`` only when "
                "``kv_store_url`` is unset. It then backs every namespace, "
                "not just HTTP resumability. Prefer ``kv_store_url`` for new "
                "deployments."
            ),
            "tags": ("persistence",),
            "wizard": {"group": "Persistence", "when": "server"},
        },
    )
    tasks_url: str | None = field(
        default=None,
        metadata={
            "help": (
                "Background-task (Docket) backend URL: ``memory://`` is "
                "in-process and lost on restart; ``redis://`` is durable and "
                "multi-process. When unset, a ``redis://`` ``kv_store_url`` "
                "is reused for tasks too; otherwise fastmcp's ``memory://`` "
                "default applies. Only meaningful when task-enabled tools "
                "exist. Applied via ``configure_task_backend``."
            ),
            "tags": ("persistence", "tasks"),
            "wizard": {"group": "Persistence", "when": "server"},
        },
    )
    app_domain: str | None = field(
        default=None,
        metadata={
            "help": (
                "MCP Apps iframe domain, used for CSP sandboxing. Overrides "
                "the host derived from ``base_url``."
            ),
            "tags": ("apps",),
            "wizard": {"group": "MCP Apps", "when": "server"},
        },
    )

    tools_allow: tuple[str, ...] = field(
        default_factory=tuple,
        metadata={
            "help": (
                "Comma-separated explicit tool names this instance exposes; "
                "every other tool is hidden from listings and cannot be "
                "invoked. Names matching no registered tool are inert. "
                "Mutually exclusive with ``tools_deny``. Takes effect through "
                "``apply_tool_visibility``."
            ),
            "tags": ("server",),
            "wizard": "inferred",
        },
    )
    tools_deny: tuple[str, ...] = field(
        default_factory=tuple,
        metadata={
            "help": (
                "Comma-separated explicit tool names hidden from this "
                "instance (absent from listings, cannot be invoked). Names "
                "matching no registered tool are inert. Mutually exclusive "
                "with ``tools_allow``. Takes effect through "
                "``apply_tool_visibility``."
            ),
            "tags": ("server",),
            "wizard": "inferred",
        },
    )

    auth_mode: str | None = field(
        default=None,
        metadata={
            "help": (
                "Explicit auth-mode override, accepting ``remote`` or "
                "``oidc-proxy`` (case- and whitespace-insensitive). When "
                "unset the mode is auto-detected from which auth variables "
                "are set; the override exists because having all four OIDC "
                "variables set is ambiguous between those two modes. Other "
                "values are ignored with a warning."
            ),
            "tags": ("auth",),
            "wizard": "inferred",
        },
    )

    bearer_tokens_file: Path | None = field(
        default=None,
        metadata={
            "help": (
                "Path to a TOML file mapping bearer tokens to subjects; "
                "overrides the single-token ``bearer_token`` mode."
            ),
            "tags": ("auth", "bearer"),
            "wizard": {"group": "Auth", "when": "bearer"},
        },
    )
    bearer_default_subject: str = field(
        default=DEFAULT_BEARER_SUBJECT,
        metadata={
            "help": (
                "Subject assigned to the single-token bearer mode; ignored "
                "when ``bearer_tokens_file`` is set, since mapped mode carries "
                "per-token subjects."
            ),
            "tags": ("auth", "bearer"),
            "wizard": {"group": "Auth", "when": "bearer"},
        },
    )

    def __post_init__(self) -> None:
        """Enforce the non-blank ``bearer_default_subject`` invariant.

        Blank / whitespace-only values are rewritten to
        :data:`DEFAULT_BEARER_SUBJECT` rather than rejected, so that
        direct construction (``ServerConfig(bearer_default_subject="")``)
        stays permissive.  This guard exists specifically for the
        direct-construction call site — the ``from_env`` path never
        reaches it, because :func:`markdown_vault_mcp._env.env` already
        strips and falls back to its ``default`` argument before the
        ``cls(...)`` call.

        Without this guard, a caller that constructs
        ``ServerConfig`` directly with an empty string would otherwise
        produce a ``StaticTokenVerifier`` entry with an empty
        ``client_id`` — exactly the foot-gun the consumer-side
        defensive fallback in ``_auth.py`` was previously papering over.
        """
        if not self.bearer_default_subject.strip():
            # ``object.__setattr__`` bypasses the frozen-dataclass guard;
            # this is the documented escape hatch for ``__post_init__``
            # normalisation on a frozen dataclass.
            object.__setattr__(self, "bearer_default_subject", DEFAULT_BEARER_SUBJECT)

    @classmethod
    def from_env(cls, env_prefix: str) -> ServerConfig:
        """Load all fields from ``{env_prefix}_*`` environment variables.

        Unknown values for ``TRANSPORT`` silently fall back to ``"stdio"``
        rather than raising — string fields prefer permissive defaults.
        ``PORT``, by contrast, is parsed strictly via :func:`env_int`: a
        non-integer or out-of-``1..65535`` value raises
        :class:`ConfigurationError` naming the var, so an operator typo
        fails fast at load instead of binding an invalid port later.

        Args:
            env_prefix: Env var prefix, no trailing underscore needed.

        Returns:
            A populated :class:`ServerConfig` instance.

        Raises:
            ConfigurationError: If ``{env_prefix}_PORT`` or
                ``{env_prefix}_SHUTDOWN_GRACE_S`` is set to a non-integer or
                out-of-range value; if ``{env_prefix}_TOOLS_ALLOW`` and
                ``{env_prefix}_TOOLS_DENY`` are both set; or if either is set
                but parses to zero tool names (e.g. a lone ``,``).
        """
        transport = _read_transport(env(env_prefix, "TRANSPORT", "stdio"))
        host = env(env_prefix, "HOST", "127.0.0.1")
        shutdown_grace_s = env_int(
            env_prefix, "SHUTDOWN_GRACE_S", 3, strict=True, minimum=0
        )

        # Each `_read_*` helper below does its own literal `env(...)` reads
        # from `env_prefix` (see the comment above `_SERVER_CONFIG_ENV_SUFFIXES`
        # for why those reads must stay literal).
        oidc = _read_oidc(env_prefix)
        bearer = _read_bearer(env_prefix)
        tools_allow, tools_deny = _read_tools_visibility(env_prefix)

        return cls(
            transport=transport,
            host=host,
            port=env_int(
                env_prefix, "PORT", 8000, strict=True, minimum=1, maximum=65535
            ),
            shutdown_grace_s=shutdown_grace_s,
            base_url=env(env_prefix, "BASE_URL"),
            bearer_token=bearer.token,
            oidc_config_url=oidc.config_url,
            oidc_client_id=oidc.client_id,
            oidc_client_secret=oidc.client_secret,
            oidc_audience=oidc.audience,
            oidc_required_scopes=oidc.required_scopes,
            oidc_advertised_scopes=oidc.advertised_scopes,
            oidc_jwt_signing_key=oidc.jwt_signing_key,
            oidc_verify_access_token=oidc.verify_access_token,
            kv_store_url=env(env_prefix, "KV_STORE_URL"),
            event_store_url=env(env_prefix, "EVENT_STORE_URL"),
            tasks_url=env(env_prefix, "TASKS_URL"),
            app_domain=env(env_prefix, "APP_DOMAIN"),
            tools_allow=tools_allow,
            tools_deny=tools_deny,
            auth_mode=env(env_prefix, "AUTH_MODE"),
            bearer_tokens_file=bearer.tokens_file,
            bearer_default_subject=bearer.default_subject,
        )


# The env-var suffixes ``ServerConfig.from_env`` (directly, or through the
# ``_read_*`` helpers it calls) reads — the part after a project's
# ``{PREFIX}_``. Kept in lockstep by
# ``test_config.py::TestServerConfigEnvSuffixes``, whose
# ``_suffixes_read_by_from_env`` AST-scans ``ServerConfig.from_env`` *and*
# every module-level ``_read_*`` function in this file for
# ``env``/``env_int``/``env_float`` calls whose suffix is a string literal,
# and fails if such a read is added/removed/renamed without updating this
# set. The helpers are discovered by introspection (any callable whose name
# starts with ``_read_``), not a hard-coded list, so a new helper is picked
# up automatically. The scan deliberately targets ``from_env`` plus the
# ``_read_*`` functions rather than the whole module: those are the
# functions that *constitute* the ``ServerConfig`` read surface by
# convention, so an env read added anywhere else in this file later (the
# ``domain_env_suffixes``/``domain_env_surface`` machinery, a future helper
# that doesn't follow the ``_read_`` naming convention) cannot silently
# join this set just by sharing a module with ``from_env``.
#
# Keep every read in the ``env(prefix, "LITERAL")`` form: a suffix built
# from a variable or passed by keyword is invisible to the scan. A read may
# live either directly in ``from_env`` or inside a ``_read_*`` helper it
# calls — both are scanned — but it must not be threaded through as an
# already-read parameter (that hides the literal from both places at once).
_SERVER_CONFIG_ENV_SUFFIXES: frozenset[str] = frozenset(
    {
        "TRANSPORT",
        "HOST",
        "PORT",
        "SHUTDOWN_GRACE_S",
        "BASE_URL",
        "BEARER_TOKEN",
        "BEARER_TOKENS_FILE",
        "BEARER_DEFAULT_SUBJECT",
        "OIDC_CONFIG_URL",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
        "OIDC_AUDIENCE",
        "OIDC_REQUIRED_SCOPES",
        "OIDC_ADVERTISED_SCOPES",
        "OIDC_JWT_SIGNING_KEY",
        "OIDC_VERIFY_ACCESS_TOKEN",
        "KV_STORE_URL",
        "EVENT_STORE_URL",
        "TASKS_URL",
        "APP_DOMAIN",
        "AUTH_MODE",
        "TOOLS_ALLOW",
        "TOOLS_DENY",
    }
)


def server_config_env_suffixes() -> frozenset[str]:
    """Return the env-var suffixes :meth:`ServerConfig.from_env` reads.

    Each suffix is the part after a project's ``{PREFIX}_`` (e.g. ``BASE_URL``
    for ``MYAPP_BASE_URL``), so a consumer can check whether a given
    ``{PREFIX}_{SUFFIX}`` variable is part of the upstream ``ServerConfig``
    surface — for instance to detect drift between a config-wizard spec and the
    real config surface.

    Excludes native ``FASTMCP_*`` variables and consumer-specific
    (``ProjectConfig``) variables: it is purely the ``ServerConfig.from_env``
    surface.
    """
    return _SERVER_CONFIG_ENV_SUFFIXES
