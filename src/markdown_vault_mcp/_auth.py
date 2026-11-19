"""Auth mode resolution and builders.

Ported from ``fastmcp_pvl_core._auth`` 10.2.0 (#2277). ``build_auth`` records
the resolved mode with ``_subject.set_current_auth_mode``, the context variable
``get_subject`` and ``get_current_auth_mode`` read.

Inspect :class:`ServerConfig` to determine which auth flavor is
configured, then dispatch to the right FastMCP auth provider.
Six modes: ``none``, ``bearer-single``, ``bearer-mapped``, ``remote``,
``oidc-proxy``, ``multi``.
"""

from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any, Final, Literal, TypeGuard, assert_never, cast

from markdown_vault_mcp._subject import set_current_auth_mode
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.utils.fs import is_regular_file

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from fastmcp.server.auth import (
        RemoteAuthProvider,
        StaticTokenVerifier,
    )
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    from markdown_vault_mcp._pvl_core import ServerConfig

logger = logging.getLogger(__name__)

AuthMode = Literal[
    "none", "bearer-single", "bearer-mapped", "remote", "oidc-proxy", "multi"
]

# The override only accepts the two OIDC modes that can apply to the same
# underlying configuration.  Bearer / multi / none are unambiguous from
# field presence, so an override for them can only restate what
# auto-detection already works out.  The silent-failure rationale this
# comment used to give no longer applies since fastmcp-pvl-core#316.
_VALID_MODES: frozenset[Literal["remote", "oidc-proxy"]] = frozenset(
    {"remote", "oidc-proxy"}
)


def _is_valid_override(value: str) -> TypeGuard[Literal["remote", "oidc-proxy"]]:
    """Narrow ``value`` to the override-valid subset of :data:`AuthMode`."""
    return value in _VALID_MODES


# Distinguishes "the builder never returned" from the ``None`` the
# ``none`` arm legitimately returns, so :func:`build_auth` can announce a
# failed build without catching the exception it is about to re-raise.
_UNBUILT: Final = object()


def _normalise_override(raw: str | None) -> str:
    """Canonicalise a raw ``AUTH_MODE`` value for comparison.

    Shared by :func:`_resolve_explicit_override` and
    :func:`_has_explicit_override` so a value one honours is the value
    the other reports as ``source=explicit``. Without it,
    ``AUTH_MODE="  ReMoTe  "`` would resolve to ``remote`` and then be
    announced as ``source=auto-detected``.
    """
    return (raw or "").strip().lower()


def _resolve_explicit_override(
    raw: str | None,
) -> Literal["remote", "oidc-proxy"] | None:
    """Resolve the ``AUTH_MODE`` override to a valid OIDC mode, or ``None``.

    The override only accepts ``remote`` or ``oidc-proxy`` (case- and
    whitespace-insensitive). An empty/unset value yields ``None``
    silently; any other non-empty value yields ``None`` with a warning.
    Callers fall back to auto-detection whenever this returns ``None``.
    """
    explicit = _normalise_override(raw)
    if not explicit:
        return None
    if _is_valid_override(explicit):
        return explicit
    logger.warning("auth_mode_unknown value=%r", explicit)
    return None


def _has_explicit_override(raw: str | None) -> bool:
    """Whether ``AUTH_MODE`` names a mode the override actually honours.

    Deliberately pure: :func:`_resolve_explicit_override` warns about
    unrecognised values, so calling it a second time purely to learn the
    provenance would emit that warning twice.
    """
    return _is_valid_override(_normalise_override(raw))


def _announce_auth_mode(
    mode: AuthMode,
    config: ServerConfig,
    provider: object,
    *,
    failed: bool = False,
) -> None:
    """Log the resolved auth mode as a startup side effect.

    Called only from :func:`build_auth`, exactly once per call, on every
    resolution path — including the paths where a builder raises, which
    is when the operator most needs to know which mode was being built.
    Nothing dedupes it: a caller that builds auth for two servers
    announces twice.

    :func:`resolve_auth_mode` never announces the mode. It is a public
    export a caller may invoke any number of times, so announcing from
    inside it emitted the line once per call rather than once per server
    (fastmcp-pvl-core#310). Its one remaining log is the ``auth_mode_unknown`` warning
    for an ``AUTH_MODE`` value it does not recognise.

    On a successful build the level is chosen by *provider*, not by
    *mode*. Since fastmcp-pvl-core#316 the two agree — a non-``none`` mode that yields no
    provider now raises in :func:`_build_provider` and arrives here as
    *failed* instead — so this is deliberately the weaker test of the
    two: it reports the posture the server actually has rather than the
    one the invariant says it should have, and keeps warning if that
    invariant is ever weakened.

    Args:
        mode: The mode :func:`resolve_auth_mode` returned.
        config: The same config it was resolved from, read only to
            report whether the mode came from ``AUTH_MODE`` or from
            auto-detection.
        provider: The provider :func:`build_auth` is about to return.
            ``None`` means the server will accept unauthenticated
            connections. Ignored when *failed* is ``True``.
        failed: ``True`` when no provider was built and an exception is
            about to propagate — either a builder raised, or the mode
            yielded no provider and :func:`_reject_unprovisioned_mode`
            refused it. No server starts at all, which is a different
            outcome from an unauthenticated one.
    """
    source = "explicit" if _has_explicit_override(config.auth_mode) else "auto-detected"
    if failed:
        logger.warning(
            "auth_mode_resolved mode=%s source=%s consequence=%s",
            mode,
            source,
            "auth provider construction failed; server will not start",
        )
    elif provider is None:
        logger.warning(
            "auth_mode_resolved mode=%s source=%s consequence=%s",
            mode,
            source,
            "server accepts unauthenticated connections",
        )
    else:
        logger.info("auth_mode_resolved mode=%s source=%s", mode, source)


def resolve_auth_mode(config: ServerConfig) -> AuthMode:
    """Decide which auth flavor to use based on configured fields.

    Precedence:

    - If ``config.auth_mode`` is set, it overrides auto-detection.  The
      override only accepts ``remote`` or ``oidc-proxy`` — these are the
      two modes that can apply to the same underlying OIDC
      configuration (all four OIDC vars set is ambiguous between them,
      depending on operator intent).  Other values (``bearer``,
      ``multi``, ``none``, and any unknown string) are ignored with a
      warning, and auto-detection is used.  The comparison is case- and
      whitespace-insensitive.
    - ``multi``: any bearer flavor (single or mapped) and an OIDC
      flavor are both configured.
    - ``bearer-mapped``: ``bearer_tokens_file`` set (takes precedence
      over a single ``bearer_token`` if both are configured).
    - ``bearer-single``: only ``bearer_token`` set.
    - ``oidc-proxy``: all four OIDC client-credential vars set
      (``base_url``, ``oidc_config_url``, ``oidc_client_id``,
      ``oidc_client_secret``).
    - ``remote``: only ``base_url`` + ``oidc_config_url`` set.
    - ``none``: nothing configured.

    Args:
        config: Populated server configuration.

    Returns:
        One of the six :data:`AuthMode` literals.
    """
    override = _resolve_explicit_override(config.auth_mode)
    if override is not None:
        return override

    has_mapped_bearer = config.bearer_tokens_file is not None
    has_single_bearer = bool(config.bearer_token) and not has_mapped_bearer
    has_oidc_proxy = all(
        (
            config.base_url,
            config.oidc_config_url,
            config.oidc_client_id,
            config.oidc_client_secret,
        )
    )
    has_remote = bool(config.base_url and config.oidc_config_url) and not has_oidc_proxy

    oidc_mode: AuthMode | None
    if has_oidc_proxy:
        oidc_mode = "oidc-proxy"
    elif has_remote:
        oidc_mode = "remote"
    else:
        oidc_mode = None

    has_any_bearer = has_mapped_bearer or has_single_bearer

    if has_any_bearer and oidc_mode is not None:
        return "multi"
    if has_mapped_bearer:
        return "bearer-mapped"
    if has_single_bearer:
        return "bearer-single"
    if oidc_mode is not None:
        return oidc_mode
    return "none"


def _coerce_token_subject(path: Path, token: str, subject: object) -> str:
    """Validate one ``token -> subject`` row, returning the subject string.

    Args:
        path: Source file, used only for error messages.
        token: The TOML table key (always a string per TOML).
        subject: The raw TOML value for *token*.

    Raises:
        ConfigurationError: blank token key, nested-table subject,
            non-string subject, or empty/whitespace-only subject.
    """
    # TOML keys are always strings, so just check they're non-blank.
    if not token.strip():
        raise ConfigurationError(
            f"bearer tokens file at {path}: token key is empty or whitespace-only"
        )
    if isinstance(subject, dict):
        raise ConfigurationError(
            f"bearer tokens file at {path}: token entry is a "
            f"nested table — quote token strings as "
            f'\'"<token>" = "<subject>"\''
        )
    if not isinstance(subject, str):
        raise ConfigurationError(
            f"bearer tokens file at {path}: subject must be a "
            f"string, got {type(subject).__name__}"
        )
    if not subject.strip():
        raise ConfigurationError(f"bearer tokens file at {path}: subject is empty")
    return subject


def _load_bearer_tokens(path: Path) -> dict[str, str]:
    """Parse a bearer-token TOML file into a {token: subject} dict.

    The path is normalised with :meth:`Path.expanduser` first.  This is
    the single expansion site for both env-loaded configs (``from_env``
    intentionally keeps a leading ``~`` literal) and direct-construction
    configs (where ``Path("~/tokens.toml")`` also keeps the tilde
    literal).  Both call sites converge on the same expanded path here.

    Raises:
        ConfigurationError: file missing, unparseable, schema-invalid, or
            containing empty/non-string values.
    """
    path = path.expanduser()
    try:
        # The project's stat-based check (#1625): a refused stat is reported,
        # not read as "not there".
        present = is_regular_file(path)
    except OSError as exc:
        raise ConfigurationError(
            f"bearer tokens file at {path} could not be read: {exc}"
        ) from exc
    if not present:
        raise ConfigurationError(
            f"bearer tokens file not found or not a regular file: {path}"
        )
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(
            f"bearer tokens file at {path} could not be read: {exc}"
        ) from exc
    if not raw:
        raise ConfigurationError(f"bearer tokens file is empty: {path}")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"bearer tokens file at {path} could not be parsed: {exc}"
        ) from exc
    tokens = data.get("tokens")
    if not isinstance(tokens, dict) or not tokens:
        raise ConfigurationError(
            f"bearer tokens file at {path} must define a non-empty [tokens] table"
        )
    result: dict[str, str] = {}
    for token, subject in tokens.items():
        result[token] = _coerce_token_subject(path, token, subject)
    return result


def build_bearer_auth(config: ServerConfig) -> StaticTokenVerifier | None:
    """Build a :class:`StaticTokenVerifier` for either bearer flavor.

    Two modes:

    - **Mapped** (``bearer_tokens_file`` set): parse the TOML file and
      build one verifier entry per ``token → subject`` row. The subject
      is carried via the entry's ``client_id`` so request-time code can
      retrieve it via :func:`get_subject`.
    - **Single** (``bearer_token`` set, no file): one verifier entry
      whose ``client_id`` is ``config.bearer_default_subject``
      (defaults to ``"bearer-anon"``).

    If both ``bearer_tokens_file`` and ``bearer_token`` are configured,
    the file wins and a ``WARNING`` is logged.

    Args:
        config: Populated server configuration.

    Returns:
        A configured :class:`StaticTokenVerifier`, or ``None`` when
        neither flavor is configured.

    Raises:
        ConfigurationError: when ``bearer_tokens_file`` is set but the
            file is missing, unparseable, or schema-invalid.
    """
    from fastmcp.server.auth import StaticTokenVerifier

    tokens_file = config.bearer_tokens_file
    if tokens_file is not None:
        if config.bearer_token:
            logger.warning(
                "bearer_tokens_file_takes_precedence "
                "bearer_tokens_file=%s bearer_token=<redacted>",
                tokens_file,
            )
        mapping = _load_bearer_tokens(tokens_file)
        return StaticTokenVerifier(
            tokens={
                token: {"client_id": subject, "scopes": ["read", "write"]}
                for token, subject in mapping.items()
            },
        )

    token = (config.bearer_token or "").strip()
    if not token:
        logger.debug("bearer_auth_skipped reason=not_configured")
        return None

    # ``ServerConfig.__post_init__`` normalises blank/whitespace-only
    # ``bearer_default_subject`` to the package default, so we can read
    # the field directly without a defensive consumer-side fallback.
    logger.debug("bearer_auth_enabled token=<redacted>")
    return StaticTokenVerifier(
        tokens={
            token: {
                "client_id": config.bearer_default_subject,
                "scopes": ["read", "write"],
            },
        },
    )


# Scopes advertised to MCP clients in protected-resource
# metadata when the operator does not override the set.
#
# This is deliberately *not* the same list as ``required_scopes``. The two
# answer different questions: ``required_scopes`` is "what must be present
# in a token for this server to accept it", while the advertised set is
# "what a client should ask the authorization server for". Deriving the
# second from the first is what issue fastmcp-pvl-core#280 reported — in ``multi`` mode
# ``required_scopes`` must be empty (bearer tokens carry no scopes, fastmcp-pvl-core#249),
# so the metadata advertised ``[]``, clients requested no scopes at all,
# and the resulting grant had no ``offline_access`` and therefore no
# refresh token: every session died at access-token expiry and needed a
# human at a browser again.
#
# ``openid`` makes the grant an OIDC one (subject identity, id_token).
# ``offline_access`` is what makes the authorization server issue a
# refresh token.
_DEFAULT_ADVERTISED_SCOPES: tuple[str, ...] = ("openid", "offline_access")


def _dedupe(scopes: Iterable[str]) -> list[str]:
    """Return *scopes* with duplicates removed, first occurrence winning."""
    seen: dict[str, None] = {}
    for scope in scopes:
        seen.setdefault(scope, None)
    return list(seen)


def _resolve_advertised_scopes(
    config: ServerConfig,
    *,
    provider_scopes_supported: Iterable[str] | None,
) -> list[str]:
    """Decide which scopes to advertise to MCP clients.

    The advertised set is :data:`_DEFAULT_ADVERTISED_SCOPES`,
    or ``config.oidc_advertised_scopes`` when the operator set it, plus
    ``config.oidc_required_scopes`` — a scope this server *requires* must
    always be advertised, or clients would never request it and every
    token would then fail the requirement.

    The default is filtered against the authorization
    server's published ``scopes_supported`` when there is one: an
    authorization server that does not support ``offline_access`` may
    reject the whole authorization request with ``invalid_scope`` rather
    than ignore it. An operator-set list is taken verbatim — the
    operator knows their deployment better than discovery does, and a
    client-level restriction is not visible in discovery anyway.

    Args:
        config: Populated server configuration.
        provider_scopes_supported: ``scopes_supported`` as published by
            the authorization server's discovery document, or ``None``
            when it publishes none (the field is optional).

    Returns:
        The scope list to advertise, in request order. May be empty only
        when discovery contradicts every default and nothing is
        required.
    """
    operator_set = list(config.oidc_advertised_scopes)
    if operator_set:
        base = operator_set
    else:
        base = list(_DEFAULT_ADVERTISED_SCOPES)
        if provider_scopes_supported is not None:
            supported = set(provider_scopes_supported)
            if supported:
                dropped = [scope for scope in base if scope not in supported]
                if dropped:
                    logger.warning(
                        "oidc_advertised_scope_dropped scopes=%s consequence=%s",
                        ",".join(dropped),
                        "authorization server discovery does not list them; "
                        "clients will not request them (no offline_access "
                        "means no refresh token, so sessions end at "
                        "access-token expiry)",
                    )
                base = [scope for scope in base if scope in supported]

    advertised = _dedupe([*base, *config.oidc_required_scopes])
    logger.debug("oidc_advertised_scopes scopes=%s", ",".join(advertised))
    return advertised


def _warn_oidc_caveats(*, required_scopes: list[str], verify_id_token: bool) -> None:
    """Emit operator warnings for misconfiguration-prone OIDC-proxy settings.

    Warns when id_token verification is on but ``openid`` is absent from
    *required_scopes* (the id_token may never be issued).
    """
    if verify_id_token and "openid" not in required_scopes:
        logger.warning(
            "oidc_proxy_auth_scope_warning "
            "verify_id_token=True missing_scope=openid "
            "consequence=%s remedy=%s",
            "the id_token may be absent from the token response",
            "add 'openid' to required_scopes or set oidc_verify_access_token=True",
        )


def build_oidc_proxy_auth(config: ServerConfig) -> OIDCProxy | None:
    """Build an :class:`OIDCProxy` provider, or return ``None``.

    Requires all four of ``base_url``, ``oidc_config_url``,
    ``oidc_client_id``, and ``oidc_client_secret`` on *config*.  By
    default the proxy verifies the upstream ``id_token`` (a standard JWT
    per OIDC Core) rather than the ``access_token`` — this works with
    every OIDC provider, including those that issue opaque access tokens
    (e.g. Authelia).  Set ``config.oidc_verify_access_token=True`` to
    revert to access-token verification.

    ``required_scopes`` defaults to ``["openid"]`` when *config* does not
    configure any, matching OIDC Core semantics (``openid`` must be
    requested for an id_token to be issued).

    What the proxy *advertises* to clients (and forwards upstream) is a
    separate, wider set — see :func:`_resolve_advertised_scopes`. It
    includes ``offline_access`` by default so the upstream grant carries
    a refresh token.

    Args:
        config: Populated server configuration.

    Returns:
        A configured :class:`OIDCProxy`, or ``None`` when any of the
        four required fields is missing.

    Raises:
        ConfigurationError: OIDC discovery at ``config.oidc_config_url``
            failed (unreachable, non-2xx, malformed, or missing required
            endpoints). Same contract as :func:`build_remote_auth`.
    """
    # Shared with the invariant in ``_build_provider`` so that what a skip
    # reports here and what a refusal names there cannot drift apart.
    missing = _missing_for_mode(config, "oidc-proxy")
    if missing:
        logger.debug("oidc_proxy_auth_skipped missing=%s", ",".join(missing))
        return None

    # Narrow types — all four are non-None after the guard above.
    base_url = cast("str", config.base_url)
    oidc_config_url = cast("str", config.oidc_config_url)
    oidc_client_id = cast("str", config.oidc_client_id)
    oidc_client_secret = cast("str", config.oidc_client_secret)

    required_scopes: list[str] = list(config.oidc_required_scopes) or ["openid"]

    verify_access_token = config.oidc_verify_access_token
    verify_id_token = not verify_access_token

    _warn_oidc_caveats(
        required_scopes=required_scopes,
        verify_id_token=verify_id_token,
    )

    import httpx2
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    # ``OIDCProxy.__init__`` performs discovery eagerly and re-raises the
    # raw failure: ``httpx2.HTTPError`` from fastmcp's network layer
    # (fastmcp performs this fetch itself, on httpx2 — this project's own
    # ``httpx`` never enters this path), and ``ValueError`` from JSON
    # decoding, pydantic validation (a ``ValueError`` subclass), or the
    # endpoint check. Normalise to the same ``ConfigurationError``
    # contract ``build_remote_auth`` uses so a consumer's
    # ``except ConfigurationError`` covers every OIDC mode (fastmcp-pvl-core#69). Only
    # the URL enters the message; the secret never does.
    try:
        proxy = OIDCProxy(
            config_url=oidc_config_url,
            client_id=oidc_client_id,
            client_secret=oidc_client_secret,
            base_url=base_url,
            audience=config.oidc_audience,
            required_scopes=required_scopes,
            jwt_signing_key=config.oidc_jwt_signing_key,
            verify_id_token=verify_id_token,
            require_authorization_consent=False,
        )
    except (httpx2.HTTPError, ValueError) as exc:
        raise ConfigurationError(
            f"OIDC discovery failed at {oidc_config_url}: {exc}"
        ) from exc

    # Widen what the proxy advertises (and asks the upstream provider
    # for) beyond ``required_scopes``, which it would otherwise reuse for
    # both. The proxy only issues a refresh token of its own when the
    # upstream response carried one, and upstream only issues one when
    # ``offline_access`` was requested — so without this the proxy's
    # sessions expire exactly like the ``remote`` ones in fastmcp-pvl-core#280.
    # ``update_default_scopes`` touches the advertised / default / DCR
    # scope set only; ``proxy.required_scopes`` (what a token must carry
    # to be accepted) is deliberately left alone.
    proxy.update_default_scopes(
        _resolve_advertised_scopes(
            config,
            provider_scopes_supported=proxy.oidc_config.scopes_supported,
        )
    )
    return proxy


def build_remote_auth(config: ServerConfig) -> RemoteAuthProvider | None:
    """Build a :class:`RemoteAuthProvider` from OIDC discovery.

    Fetches the OIDC discovery document at startup to extract
    ``jwks_uri`` and ``issuer``, then constructs a ``JWTVerifier`` for
    local token validation via JWKS.  No client credentials are needed —
    tokens are validated locally.

    The scopes advertised in protected-resource metadata are decided by
    :func:`_resolve_advertised_scopes`, independently of the verifier's
    ``required_scopes`` — the former tells clients what to ask the
    authorization server for, the latter is what a token must carry to
    be accepted here.

    Requires ``base_url`` and ``oidc_config_url`` on *config*.  Returns
    ``None`` only as a precondition signal when either is missing.  An
    explicit ``AUTH_MODE=remote`` does reach this path with them unset,
    and since fastmcp-pvl-core#316 :func:`build_auth` turns that ``None`` into a
    :class:`ConfigurationError` rather than starting an unauthenticated
    server; the signal itself stays for direct callers composing
    flavours.  Other failure modes — ``httpx`` not installed, the
    discovery request failing (network error or malformed JSON), the
    discovery document missing ``jwks_uri`` / ``issuer`` — raise
    :class:`ConfigurationError` rather than returning ``None``: a
    server that asked for OIDC and cannot get it must fail at startup,
    never silently degrade to "no auth at all" or "bearer break-glass
    only".

    Args:
        config: Populated server configuration.

    Returns:
        A configured :class:`RemoteAuthProvider`, or ``None`` when
        ``base_url`` / ``oidc_config_url`` are absent (precondition
        miss; not a failure mode).

    Raises:
        ConfigurationError: ``httpx`` missing, discovery failed
            (network error or malformed JSON), or the discovery
            document is incomplete.
    """
    # Kept as an inline test rather than a ``_missing_for_mode`` call:
    # the helper is opaque to mypy, and narrowing both fields here is
    # worth more than sharing the predicate.
    # ``tests/test_auth_builders.py`` pins the two against each other.
    if not config.base_url or not config.oidc_config_url:
        logger.debug("remote_auth_skipped reason=missing_base_url_or_config_url")
        return None

    try:
        import httpx
    except ImportError as exc:
        raise ConfigurationError(
            "remote auth requires httpx, a dependency of markdown-vault-mcp; "
            "reinstall the package. Refusing to start without auth"
        ) from exc

    try:
        resp = httpx.get(config.oidc_config_url, timeout=10)
        resp.raise_for_status()
        discovery = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Catches both network-layer failures (``httpx.HTTPError``) and
        # parse-layer failures (``ValueError`` from ``resp.json()``);
        # phrasing avoids "fetch" so the message stays accurate for the
        # parse case too.
        raise ConfigurationError(
            f"OIDC discovery failed at {config.oidc_config_url}: {exc}"
        ) from exc

    jwks_uri = discovery.get("jwks_uri")
    issuer = discovery.get("issuer")
    if not jwks_uri or not issuer:
        raise ConfigurationError(
            f"OIDC discovery document at {config.oidc_config_url} is "
            f"incomplete: jwks_uri={jwks_uri!r} issuer={issuer!r}"
        )

    required_scopes: list[str] | None = list(config.oidc_required_scopes) or None

    discovered_scopes = discovery.get("scopes_supported")
    advertised_scopes = _resolve_advertised_scopes(
        config,
        provider_scopes_supported=(
            [str(scope) for scope in discovered_scopes]
            if isinstance(discovered_scopes, list)
            else None
        ),
    )

    from fastmcp.server.auth import JWTVerifier, RemoteAuthProvider

    verifier = JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=config.oidc_audience,
        required_scopes=required_scopes,
    )
    # ``scopes_supported`` is passed explicitly rather than left to fall
    # back to the verifier's: the verifier's is derived from
    # ``required_scopes``, which is empty here whenever the operator
    # configured none, and an empty advertised list makes clients request
    # no scopes at all (fastmcp-pvl-core#280).
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=config.base_url,
        scopes_supported=advertised_scopes,
    )


def _build_multi_auth(config: ServerConfig) -> Any:
    """Compose the OIDC + bearer pair that ``multi`` mode returns.

    Split out of the dispatch so the dispatcher stays close to pure
    dispatch: this arm carries provider construction, two invariant
    checks, and the load-bearing ``required_scopes=[]`` contract, all
    of which belong together and none of which the other arms share.
    """
    # ``build_remote_auth`` raises ``ConfigurationError`` on
    # discovery / dependency failures, so the only way for either
    # ``oidc_auth`` or ``bearer_auth`` to be ``None`` here is a
    # precondition mismatch (e.g. ``build_oidc_proxy_auth`` finds
    # missing fields and ``build_remote_auth`` likewise returns
    # ``None`` from its precondition check).  In multi mode that
    # is itself a misconfiguration: ``resolve_auth_mode`` only
    # picks "multi" when both bearer and OIDC inputs are present
    # at startup.  Hard-fail rather than silent-degrade.
    oidc_auth: OIDCProxy | RemoteAuthProvider | None = build_oidc_proxy_auth(
        config
    ) or build_remote_auth(config)
    bearer_auth = build_bearer_auth(config)

    if oidc_auth is None:
        raise ConfigurationError(
            "multi-mode auth requires both OIDC and bearer providers; "
            "OIDC builder returned None — check OIDC configuration "
            "(base_url, oidc_config_url, and the proxy fields if used). "
            "Refusing to start without OIDC; would otherwise silently "
            "degrade to bearer-only and break the operator's "
            "real-identity contract."
        )
    if bearer_auth is None:
        raise ConfigurationError(
            "multi-mode auth requires both OIDC and bearer providers; "
            "bearer builder returned None — check bearer configuration "
            "(bearer_token or bearer_tokens_file)."
        )

    from fastmcp.server.auth import MultiAuth

    # ``required_scopes=[]`` is load-bearing: without it, OIDC's
    # ``["openid"]`` scope propagates to FastMCP's
    # RequireAuthMiddleware and rejects bearer tokens lacking
    # ``openid`` with 403 insufficient_scope (markdown-vault-mcp PR #249).
    # It does not reach the published metadata: ``get_routes``
    # delegates to ``server``, which advertises the set
    # ``_resolve_advertised_scopes`` gave it (fastmcp-pvl-core#280).
    #
    # OIDCProxy / RemoteAuthProvider (both OAuthProvider
    # subclasses) MUST go in ``server=`` — passing an
    # ``OAuthProvider`` in ``verifiers=`` silently drops its OAuth
    # routes because ``get_routes`` / ``get_well_known_routes``
    # only delegate to ``self.server``.
    return MultiAuth(
        server=oidc_auth,
        verifiers=[bearer_auth],
        required_scopes=[],
    )


def _missing_for_mode(config: ServerConfig, mode: AuthMode) -> list[str]:
    """Names of the operator variables *mode* requires and *config* lacks.

    Returned unprefixed (``OIDC_CLIENT_ID``, not
    ``MYSERVER_OIDC_CLIENT_ID``): :class:`ServerConfig` does not carry the
    env prefix, which :meth:`ServerConfig.from_env` takes as an argument
    and does not retain. The real prefix is therefore not recoverable
    here, and the caller emits a literal ``{PREFIX}_`` placeholder rather
    than substituting it.

    Each entry must mirror the precondition its builder actually
    applies: ``build_oidc_proxy_auth`` calls this function, but
    ``build_remote_auth`` and ``build_bearer_auth`` test their fields
    inline for the type narrowing, so those two can drift.
    ``tests/test_auth_builders.py`` pins them against each other.

    Only the modes whose builder can return ``None`` are listed.
    ``bearer-mapped`` reaches its builder with ``bearer_tokens_file``
    already set, and ``multi`` raises on its own, so both yield an empty
    list — as does ``none``, which requires nothing.

    Args:
        config: Populated server configuration.
        mode: The mode :func:`resolve_auth_mode` settled on.

    Returns:
        Unset variable names in declaration order; empty when the mode
        requires nothing or everything it requires is set.
    """
    required: dict[str, object]
    if mode == "oidc-proxy":
        required = {
            "BASE_URL": config.base_url,
            "OIDC_CONFIG_URL": config.oidc_config_url,
            "OIDC_CLIENT_ID": config.oidc_client_id,
            # Reduced to a bool so the secret itself never enters the
            # dict's value side (static-analysis taint tools flag it
            # otherwise, and this feeds an operator-visible message).
            "OIDC_CLIENT_SECRET": bool(config.oidc_client_secret),
        }
    elif mode == "remote":
        required = {
            "BASE_URL": config.base_url,
            "OIDC_CONFIG_URL": config.oidc_config_url,
        }
    elif mode == "bearer-single":
        # ``.strip()`` mirrors the builder: a whitespace-only token is
        # what the builder rejects, so it is what counts as unset here.
        required = {"BEARER_TOKEN": (config.bearer_token or "").strip()}
    else:
        return []
    return [name for name, value in required.items() if not value]


def _reject_unprovisioned_mode(
    config: ServerConfig, mode: AuthMode, provider: object
) -> None:
    """Refuse a resolved mode that asked for auth and got no provider.

    A builder returns ``None`` as a precondition signal meaning "this
    flavor is not configured, try the next one". *mode* is already
    settled by the time this runs, so there is no next one: the signal
    arriving here means the operator configured auth and would otherwise
    get a server that accepts anyone (fastmcp-pvl-core#316).

    Called from :func:`_build_provider` rather than from
    :func:`build_auth` after the dispatch returns, so that the raise
    leaves ``build_auth``'s provider sentinel untouched and the
    announcement reports "server will not start" instead of the
    contradictory "accepts unauthenticated connections".

    Args:
        config: Populated server configuration.
        mode: The mode :func:`resolve_auth_mode` settled on.
        provider: What the mode's builder returned.

    Raises:
        ConfigurationError: *mode* is not ``none`` and *provider* is
            ``None``, naming the variables that are unset.
    """
    if mode == "none" or provider is not None:
        return
    missing = _missing_for_mode(config, mode)
    detail = (
        "; unset: " + ", ".join(f"{{PREFIX}}_{name}" for name in missing)
        if missing
        else ""
    )
    raise ConfigurationError(
        f"auth mode {mode} is configured but no auth provider could be "
        f"built{detail}; refusing to start a server that would accept "
        "unauthenticated connections"
    )


def _build_provider(config: ServerConfig, mode: AuthMode) -> Any:
    """Construct the provider for an already-resolved *mode*.

    Split out of :func:`build_auth` so the announcement can wrap it:
    a builder that raises must still tell the operator which mode was
    being built, which an announcement placed after an inline dispatch
    cannot do.
    """
    # ``match``-based dispatch with an explicit ``case _`` calling
    # ``assert_never`` makes adding a new :data:`AuthMode` literal a
    # mypy error rather than a silent fall-through.
    provider: Any
    match mode:
        case "none":
            provider = None
        case "bearer-single" | "bearer-mapped":
            provider = build_bearer_auth(config)
        case "oidc-proxy":
            provider = build_oidc_proxy_auth(config)
        case "remote":
            provider = build_remote_auth(config)
        case "multi":
            provider = _build_multi_auth(config)
        case _:
            assert_never(mode)

    _reject_unprovisioned_mode(config, mode, provider)
    return provider


def build_auth(config: ServerConfig) -> Any:
    """Dispatch to the correct FastMCP auth provider for *config*.

    Resolves the auth mode via :func:`resolve_auth_mode` and composes
    the individual builders.  In ``multi`` mode, wraps an OIDC provider
    and a bearer verifier into a single
    :class:`~fastmcp.server.auth.MultiAuth` with ``required_scopes=[]``.
    That empty list governs acceptance only; the scopes advertised to
    clients come from the wrapped OIDC provider and are set by
    :func:`_resolve_advertised_scopes`.

    Records the resolved mode for
    :func:`markdown_vault_mcp._subject.get_current_auth_mode` and announces it to
    the operator log (see :func:`_announce_auth_mode`), both as startup
    side effects. Nothing dedupes either: a caller composing two servers
    calls this twice and the second call wins the recorded mode.

    Args:
        config: Populated server configuration.

    Returns:
        The appropriate auth provider:

        - ``None`` when no auth is configured.
        - A :class:`~fastmcp.server.auth.StaticTokenVerifier` in
          ``bearer-single`` or ``bearer-mapped`` mode.
        - An :class:`~fastmcp.server.auth.oidc_proxy.OIDCProxy` in
          ``oidc-proxy`` mode.
        - A :class:`~fastmcp.server.auth.RemoteAuthProvider` in
          ``remote`` mode.
        - A :class:`~fastmcp.server.auth.MultiAuth` in ``multi`` mode,
          always constructed with ``required_scopes=[]`` (load-bearing —
          see implementation comment).

        ``None`` is returned only in ``none`` mode. Any other resolved
        mode either yields a provider or raises (fastmcp-pvl-core#316).

    Raises:
        ConfigurationError: the resolved mode is not ``none`` and no
            provider could be built for it — an ``AUTH_MODE`` override
            the rest of the configuration cannot satisfy, or a
            whitespace-only ``bearer_token``. Also propagated from the
            individual builders (OIDC discovery failure, a missing
            extra, an unreadable bearer tokens file).
    """
    mode = resolve_auth_mode(config)
    # Record the resolved mode for ``get_subject`` and
    # ``get_current_auth_mode``; must run before the dispatch below so
    # tools called in stdio/no-auth servers still get ``"local"`` even
    # though the ``none`` arm yields no provider.
    set_current_auth_mode(mode)

    # The announcement runs in a ``finally`` rather than after the call so
    # that a builder which raises still names the mode it was building —
    # a failed startup is exactly when the operator needs that, and the
    # exception alone does not carry it. The sentinel distinguishes "no
    # provider was built" from the legitimate ``None`` the ``none`` arm
    # returns, without catching and re-raising anything.
    provider: Any = _UNBUILT
    try:
        provider = _build_provider(config, mode)
    finally:
        built = provider is not _UNBUILT
        _announce_auth_mode(
            mode,
            config,
            provider if built else None,
            failed=not built,
        )
    return provider
