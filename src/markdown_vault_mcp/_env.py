"""Environment variable helpers.

Every ``{PREFIX}_{NAME}`` read in this project goes through :func:`env` and
its typed siblings, so naming, blank handling and error text stay consistent.
Moved here from ``fastmcp_pvl_core`` 10.2.0 (#2275, epic #2241).

The config-surface generator finds a variable by an unqualified call to
``env``, ``env_int`` or ``env_float`` with a literal suffix inside a
``from_env``, so import these names unaliased there.
"""

from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING, Literal, TypeVar, overload

if TYPE_CHECKING:
    from markdown_vault_mcp._pvl_core import ConfigurationError

logger = logging.getLogger(__name__)

_Number = TypeVar("_Number", int, float)


def _resolve_key(prefix: str, name: str) -> str:
    """Build the ``{PREFIX}_{NAME}`` env var key (trailing ``_`` on prefix optional)."""
    return f"{prefix.rstrip('_')}_{name}"


def _required_with_default(func: str) -> TypeError:
    """The error for ``required=True`` combined with a default — a caller bug."""
    return TypeError(f"{func}: required=True cannot be combined with a default")


def _configuration_error(message: str) -> ConfigurationError:
    """Build the config error, importing its class only when one is raised.

    The class is still pvl-core's: core code not yet moved (``ServerConfig``,
    auth, transfer) raises it too, and callers here catch one type, so it becomes
    this project's own class only once no core code is left (#2283). Importing
    it here, not at module level, keeps a plain read such as the CLI client's
    profile from loading pvl-core and FastMCP (#2016).
    """
    from markdown_vault_mcp._pvl_core import ConfigurationError

    return ConfigurationError(message)


def _missing(key: str) -> ConfigurationError:
    """The error for a ``required=True`` var that is unset or blank."""
    return _configuration_error(f"{key} is required but not set")


@overload
def env(
    prefix: str, name: str, default: None = ..., *, required: Literal[True]
) -> str: ...
@overload
def env(prefix: str, name: str, *, required: Literal[False] = ...) -> str | None: ...
@overload
def env(
    prefix: str, name: str, default: None, *, required: Literal[False] = ...
) -> str | None: ...
@overload
def env(
    prefix: str, name: str, default: str, *, required: Literal[False] = ...
) -> str: ...
@overload
def env(
    prefix: str, name: str, default: None = ..., *, required: bool
) -> str | None: ...
def env(
    prefix: str, name: str, default: str | None = None, *, required: bool = False
) -> str | None:
    """Read ``{PREFIX}_{NAME}`` from the environment.

    Args:
        prefix: Env var prefix (trailing underscore optional).
        name: Variable name (without prefix).
        default: Value to return if unset or empty after strip.
        required: When ``True``, an unset or blank var raises
            :class:`ConfigurationError` (``"{KEY} is required but not set"``)
            instead of returning a default.  Keyword-only, and exclusive with
            *default*.  A literal ``required=True`` in a config's
            ``from_env`` is also what the config-surface generator reports as
            required, so generated docs mark the var required.

    Returns:
        The env var value stripped of whitespace, or ``default``.

    Raises:
        ConfigurationError: ``required=True`` and the var is unset or blank.
        TypeError: ``required=True`` together with a non-``None`` *default*.
    """
    if required and default is not None:
        raise _required_with_default("env")
    key = _resolve_key(prefix, name)
    raw = os.environ.get(key)
    value = raw.strip() if raw is not None else ""
    if value:
        return value
    if required:
        raise _missing(key)
    return default


def _reject(
    key: str,
    requirement: str,
    got: object,
    *,
    default: _Number | None,
    strict: bool,
    cause: Exception | None = None,
) -> _Number | None:
    """Reject a malformed or out-of-range value.

    In ``strict`` mode raises :class:`ConfigurationError`; otherwise logs a
    ``WARNING`` and returns *default*.  *requirement* is the human phrase
    completing ``{KEY} must ...`` (e.g. ``"be an integer"``, ``"be >= 1"``).
    *cause* is the originating ``ValueError`` on the parse path; it is chained
    into the raised error (``raise ... from cause``) so the low-level reason
    stays attached, and is ``None`` on the bounds / non-finite paths.
    """
    if strict:
        raise _configuration_error(f"{key} must {requirement}; got {got!r}") from cause
    logger.warning(
        "env_value_rejected key=%s requirement=%s got=%r default=%r",
        key,
        requirement,
        got,
        default,
    )
    return default


def _check_bounds(
    key: str,
    value: _Number,
    *,
    default: _Number | None,
    strict: bool,
    minimum: _Number | None,
    maximum: _Number | None,
) -> _Number | None:
    """Return *value* if within the inclusive bounds, else reject it."""
    if minimum is not None and value < minimum:
        return _reject(key, f"be >= {minimum}", value, default=default, strict=strict)
    if maximum is not None and value > maximum:
        return _reject(key, f"be <= {maximum}", value, default=default, strict=strict)
    return value


@overload
def env_int(
    prefix: str,
    name: str,
    default: None = ...,
    *,
    required: Literal[True],
    strict: bool = ...,
    minimum: int | None = ...,
    maximum: int | None = ...,
) -> int: ...
@overload
def env_int(
    prefix: str,
    name: str,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: int | None = ...,
    maximum: int | None = ...,
) -> int | None: ...
@overload
def env_int(
    prefix: str,
    name: str,
    default: int,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: int | None = ...,
    maximum: int | None = ...,
) -> int: ...
@overload
def env_int(
    prefix: str,
    name: str,
    default: None,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: int | None = ...,
    maximum: int | None = ...,
) -> int | None: ...
@overload
def env_int(
    prefix: str,
    name: str,
    default: None = ...,
    *,
    required: bool,
    strict: bool = ...,
    minimum: int | None = ...,
    maximum: int | None = ...,
) -> int | None: ...
def env_int(
    prefix: str,
    name: str,
    default: int | None = None,
    *,
    required: bool = False,
    strict: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Read ``{PREFIX}_{NAME}`` as an integer, with a default and optional bounds.

    The value is parsed with Python's :func:`int`, so it accepts the same
    forms — a leading sign and PEP 515 underscore separators (``"1_000"`` →
    ``1000``).  A non-integer string is invalid, including a float literal
    (``"42.5"``) or scientific notation (``"1e3"``); use :func:`env_float`
    for those.

    Args:
        prefix: Env var prefix (trailing underscore optional).
        name: Variable name (without prefix).
        default: The trusted fallback, returned **as-is and never itself
            bounds-checked**, when the var is unset/blank and — in soft mode —
            when the value is invalid or out of range.  ``minimum``/``maximum``
            validate the operator's env value, not this developer-supplied
            default.
        required: When ``True``, an unset or blank var raises
            :class:`ConfigurationError` (``"{KEY} is required but not set"``),
            and an invalid or out-of-range value raises as if ``strict=True``
            — there is no default to fall back to.  Keyword-only, and
            exclusive with *default*.
        strict: When ``True``, an invalid or out-of-range value raises
            :class:`ConfigurationError` naming the var.  When ``False`` (the
            default), it logs a ``WARNING`` and returns *default* — which is
            ``None`` when no default was supplied, so in soft mode an invalid
            value yields ``None`` (still warned).  Use ``strict=True`` when an
            invalid value must fail hard rather than degrade to ``None``.
        minimum: Inclusive lower bound; values below it are rejected.
        maximum: Inclusive upper bound; values above it are rejected.

    Returns:
        The parsed integer, or *default* when unset/blank (or, in soft mode,
        when the value is invalid or out of range).  An unset var never warns
        or raises.

    Raises:
        ConfigurationError: ``required=True`` and the var is unset or blank,
            or ``strict``/``required`` and the value is invalid or out of range.
        TypeError: ``required=True`` together with a non-``None`` *default*.
    """
    if required and default is not None:
        raise _required_with_default("env_int")
    raw = env(prefix, name)
    if raw is None:
        if required:
            raise _missing(_resolve_key(prefix, name))
        return default
    strict = strict or required
    key = _resolve_key(prefix, name)
    try:
        value = int(raw)
    except ValueError as exc:
        return _reject(
            key, "be an integer", raw, default=default, strict=strict, cause=exc
        )
    return _check_bounds(
        key, value, default=default, strict=strict, minimum=minimum, maximum=maximum
    )


@overload
def env_float(
    prefix: str,
    name: str,
    default: None = ...,
    *,
    required: Literal[True],
    strict: bool = ...,
    minimum: float | None = ...,
    maximum: float | None = ...,
) -> float: ...
@overload
def env_float(
    prefix: str,
    name: str,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: float | None = ...,
    maximum: float | None = ...,
) -> float | None: ...
@overload
def env_float(
    prefix: str,
    name: str,
    default: float,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: float | None = ...,
    maximum: float | None = ...,
) -> float: ...
@overload
def env_float(
    prefix: str,
    name: str,
    default: None,
    *,
    required: Literal[False] = ...,
    strict: bool = ...,
    minimum: float | None = ...,
    maximum: float | None = ...,
) -> float | None: ...
@overload
def env_float(
    prefix: str,
    name: str,
    default: None = ...,
    *,
    required: bool,
    strict: bool = ...,
    minimum: float | None = ...,
    maximum: float | None = ...,
) -> float | None: ...
def env_float(
    prefix: str,
    name: str,
    default: float | None = None,
    *,
    required: bool = False,
    strict: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    """Read ``{PREFIX}_{NAME}`` as a float, with a default and optional bounds.

    Behaves like :func:`env_int` but parses with Python's :func:`float`, so it
    accepts the same forms — a leading sign, PEP 515 underscore separators
    (``"1_000.5"``), and scientific notation (``"1e3"`` → ``1000.0``).
    Non-finite values (``nan``, ``inf``, ``-inf``) are rejected as invalid,
    since a config value is expected to be finite.

    Args:
        prefix: Env var prefix (trailing underscore optional).
        name: Variable name (without prefix).
        default: The trusted fallback, returned **as-is and never itself
            bounds-checked**, when the var is unset/blank and — in soft mode —
            when the value is invalid or out of range.  ``minimum``/``maximum``
            validate the operator's env value, not this developer-supplied
            default.
        required: When ``True``, an unset or blank var raises
            :class:`ConfigurationError` (``"{KEY} is required but not set"``),
            and an invalid or out-of-range value raises as if ``strict=True``
            — there is no default to fall back to.  Keyword-only, and
            exclusive with *default*.
        strict: When ``True``, an invalid or out-of-range value raises
            :class:`ConfigurationError` naming the var.  When ``False`` (the
            default), it logs a ``WARNING`` and returns *default* — which is
            ``None`` when no default was supplied, so in soft mode an invalid
            value yields ``None`` (still warned).  Use ``strict=True`` when an
            invalid value must fail hard rather than degrade to ``None``.
        minimum: Inclusive lower bound; values below it are rejected.
        maximum: Inclusive upper bound; values above it are rejected.

    Returns:
        The parsed float, or *default* when unset/blank (or, in soft mode,
        when the value is invalid or out of range).  An unset var never warns
        or raises.

    Raises:
        ConfigurationError: ``required=True`` and the var is unset or blank,
            or ``strict``/``required`` and the value is invalid or out of range.
        TypeError: ``required=True`` together with a non-``None`` *default*.
    """
    if required and default is not None:
        raise _required_with_default("env_float")
    raw = env(prefix, name)
    if raw is None:
        if required:
            raise _missing(_resolve_key(prefix, name))
        return default
    strict = strict or required
    key = _resolve_key(prefix, name)
    try:
        value = float(raw)
    except ValueError as exc:
        return _reject(
            key, "be a number", raw, default=default, strict=strict, cause=exc
        )
    if not math.isfinite(value):
        return _reject(key, "be a finite number", raw, default=default, strict=strict)
    return _check_bounds(
        key, value, default=default, strict=strict, minimum=minimum, maximum=maximum
    )


def parse_bool(value: str) -> bool:
    """Parse common truthy strings to ``bool``.

    Args:
        value: Raw string value.

    Returns:
        ``True`` for ``1``, ``true``, ``yes``, ``on`` (case-insensitive);
        ``False`` otherwise.
    """
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_list(value: str) -> list[str]:
    """Parse a comma-separated list, trimming and dropping empties.

    Args:
        value: Comma-separated string.

    Returns:
        List of non-empty, stripped items.  Returns ``[]`` when *value* is
        blank.
    """
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_scopes(value: str | None) -> list[str] | None:
    """Parse an OIDC/OAuth scopes string (space- or comma-separated).

    Args:
        value: Raw scopes string, or ``None``.

    Returns:
        List of scope tokens.  ``None`` when *value* is ``None``; ``[]``
        when *value* is a blank string.
    """
    if value is None:
        return None
    normalized = value.replace(",", " ")
    return [s for s in normalized.split() if s]
