"""The key-value store behind the event store, chosen by one operator URL.

Ported from ``fastmcp_pvl_core._kv_store`` and ``_factory.build_event_store``
10.2.0 (#2279). Core's health checks, jobs and transfer links still call
core's own copy until their areas move (#2278, #2281, #2282).

URL scheme dispatch:

- ``memory://``: :class:`MemoryStore`, in-process and lost on restart.
- ``file:///path``: :class:`FileTreeStore`, persistence on one server.
- ``redis://host:port[/db]``: :class:`RedisStore`, needs
  ``py-key-value-aio[redis]``; the URL is forwarded verbatim.
- ``dynamodb://<table_name>[?region=...&endpoint=...]``:
  :class:`DynamoDBStore`, needs ``py-key-value-aio[dynamodb]``.
- ``mongodb://host:port[/db]``: :class:`MongoDBStore`, needs
  ``py-key-value-aio[mongodb]``; the URL is forwarded verbatim.

With no URL configured the default is ``file://`` at ``/data/state``, the
volume Docker images mount, degrading to ``memory://`` on a host where that
directory is not usable (see :func:`_default_url`).

Backend imports are lazy so memory and file deployments do not load the
optional client libraries.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import ParseResult, parse_qs

from markdown_vault_mcp._pvl_core import ConfigurationError
from markdown_vault_mcp._url import UNPARSEABLE, parse_operator_url, try_parse_url
from markdown_vault_mcp.utils.fs import is_directory

if TYPE_CHECKING:
    from fastmcp.server.event_store import EventStore
    from key_value.aio.protocols.key_value import AsyncKeyValue

    from markdown_vault_mcp._pvl_core import ServerConfig

logger = logging.getLogger(__name__)


_DEFAULT_KV_STORE_DIR = "/data/state"
"""Preferred directory for the file-tree backend when no URL is given.

Docker images mount a persistent volume at ``/data``; :func:`_default_url`
degrades to ``memory://`` where the directory is not usable. Tests patch
this module attribute to redirect the default to a tmp path.
"""

_default_fallback_warned: bool = False
"""Whether the unusable-default-directory warning has been logged.

A server that builds several namespaced stores logs it once, not once per
store. Tests reset it with ``monkeypatch.setattr``.
"""

_legacy_url_warned: bool = False
"""Whether the legacy ``EVENT_STORE_URL`` deprecation warning has been logged.

Same one-shot rule as :data:`_default_fallback_warned`.
"""

_EVENTS_NAMESPACE = "events"
_MAX_EVENTS_PER_STREAM = 100
_EVENT_TTL_S = 3600


def build_event_store(config: ServerConfig) -> EventStore:
    """Build the MCP event store for the HTTP transport.

    The storage comes from :func:`build_kv_store` under the ``events``
    namespace, so ``KV_STORE_URL`` (or the legacy ``EVENT_STORE_URL``)
    selects the backend.

    Args:
        config: Server configuration whose ``kv_store_url`` or
            ``event_store_url`` selects the backend.

    Returns:
        An event store keeping 100 events per stream for one hour.

    Raises:
        ConfigurationError: The store URL is unusable; see
            :func:`build_kv_store`.
    """
    from fastmcp.server.event_store import EventStore

    storage = build_kv_store(config, namespace=_EVENTS_NAMESPACE)
    return EventStore(
        storage=storage,
        max_events_per_stream=_MAX_EVENTS_PER_STREAM,
        ttl=_EVENT_TTL_S,
    )


def build_kv_store(config: ServerConfig, *, namespace: str) -> AsyncKeyValue:
    """Build a namespaced ``AsyncKeyValue`` store from operator config.

    URL resolution order:

    1. ``config.kv_store_url``.
    2. ``config.event_store_url``, the legacy variable; logs a one-shot
       deprecation warning when used.
    3. ``file://`` at :data:`_DEFAULT_KV_STORE_DIR` where that directory is
       usable, else ``memory://`` (see :func:`_default_url`).

    The store is wrapped in a ``PrefixCollectionsWrapper`` with
    ``prefix=namespace``, so subsystems sharing a backend cannot collide on
    collection names.

    Args:
        config: Server configuration whose ``kv_store_url`` or
            ``event_store_url`` selects the backend.
        namespace: Prefix that isolates the calling subsystem's
            collections. Must be non-empty.

    Returns:
        The configured store, wrapped for namespace isolation.

    Raises:
        ValueError: *namespace* is empty. Callers pass it as a literal, so
            this is a programming error, not a configuration one.
        ConfigurationError: The operator's URL is unusable (an unknown
            scheme, a malformed ``file://`` URL, a ``dynamodb://`` URL with
            no table, a URL that does not parse) or the backend it selects
            fails to import. An explicit ``file://`` directory that cannot
            be created still raises ``OSError`` from ``mkdir``.
    """
    if not namespace.strip():
        raise ValueError(
            "namespace must be a non-empty string — it is the "
            "PrefixCollectionsWrapper prefix that isolates subsystems "
            "sharing the same backend."
        )

    url, variable = _resolve_url(config)
    backend = _build_backend(url, variable=variable)

    from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

    return PrefixCollectionsWrapper(key_value=backend, prefix=namespace)


def _resolve_url(config: ServerConfig) -> tuple[str, str]:
    """Pick the store URL and the name of the setting it came from."""
    url: str | None
    if config.kv_store_url is None and config.event_store_url is not None:
        _warn_legacy_url(config.event_store_url)
        url, variable = config.event_store_url, "{PREFIX}_EVENT_STORE_URL"
    else:
        url, variable = config.kv_store_url, "{PREFIX}_KV_STORE_URL"
    if url:
        return url, variable
    # Generated, not operator-set: do not tell them to fix a variable they
    # never touched.
    return _default_url(), "the generated default store URL"


def _warn_legacy_url(url: str) -> None:
    """Log the legacy-variable deprecation warning, once per process."""
    global _legacy_url_warned
    if _legacy_url_warned:
        return
    # Only the scheme: the URL may carry credentials in its userinfo.
    logger.warning(
        "kv_store_legacy_fallback kv_store_url=<unset> "
        "event_store_url_scheme=%r action=%s",
        _scheme_for_log(url),
        "Set <PREFIX>_KV_STORE_URL to migrate.",
    )
    _legacy_url_warned = True


def _default_url() -> str:
    """Resolve the backend URL for a deployment that configured none.

    ``file://`` at :data:`_DEFAULT_KV_STORE_DIR` is the intended default,
    but the same code runs unconfigured on hosts without ``/data``: CI
    runners, ``uvx`` installs, the stdio plugin. There the eager ``mkdir``
    would fail server construction, so the default falls back to
    ``memory://`` with one warning naming the variable to set.

    The probe requires the parent to exist rather than creating it:
    ``/data`` is a mount point, and creating it on a host that never
    mounted it would leak a root-level directory.

    An explicitly configured ``file://`` URL is never degraded; only this
    unset default is best-effort.
    """
    directory = Path(_DEFAULT_KV_STORE_DIR)
    reason = _unusable_reason(directory)
    if reason is None:
        return f"file://{_DEFAULT_KV_STORE_DIR}"

    global _default_fallback_warned
    if not _default_fallback_warned:
        logger.warning(
            "kv_store_default_fallback kv_store_url=<unset> fallback=memory:// "
            "reason=%s consequence=%s action=%s",
            reason,
            "state is in-process and lost on restart",
            "Set <PREFIX>_KV_STORE_URL to choose a backend explicitly.",
        )
        _default_fallback_warned = True
    return "memory://"


def _unusable_reason(directory: Path) -> str | None:
    """Why *directory* cannot back the default store, or ``None`` if it can.

    Only called on :data:`_DEFAULT_KV_STORE_DIR`, so the returned paths
    never come from an operator URL that might carry credentials.
    """
    parent = directory.parent
    try:
        parent_is_directory = is_directory(parent)
    except OSError as exc:
        return f"{parent} is not accessible ({exc.strerror or exc})"
    if not parent_is_directory:
        return f"{parent} does not exist"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"{directory} is not creatable ({exc.strerror or exc})"
    # mkdir(exist_ok=True) is a no-op on an existing directory, so a
    # read-only mount gets this far and would only fail on first write.
    if not os.access(directory, os.W_OK | os.X_OK):
        return f"{directory} is not writable"
    return None


def _scheme_for_log(url: str) -> str:
    """Scheme of *url* for a log line, or :data:`UNPARSEABLE`; never raises."""
    parsed = try_parse_url(url)
    return parsed.scheme if parsed is not None else UNPARSEABLE


def _missing_extra(store: str, extra: str, exc: ImportError) -> ConfigurationError:
    """The error for a backend whose client library does not import.

    It quotes the import error, so the hint cannot misdescribe an unrelated
    import failure.
    """
    return ConfigurationError(
        f"{store} requires 'py-key-value-aio[{extra}]'. Add it to the "
        f"environment the server runs in. The import failed with: {exc}"
    )


def _file_store(parsed: ParseResult) -> AsyncKeyValue:
    """Build the ``file://`` backend for :func:`_build_backend`."""
    # Messages name the scheme only, never the URL, which may carry
    # credentials. ``file://host/path`` is valid syntax, but operators
    # almost always meant the three-slash form.
    if parsed.netloc:
        raise ConfigurationError(
            "file:// URL has a host component. Use the three-slash "
            "form: 'file:///absolute/path'."
        )
    if not parsed.path:
        raise ConfigurationError(
            "file:// URL is missing a path. Use 'file:///absolute/path'."
        )
    # Import before creating the directory, so a missing library leaves no
    # orphan directory behind.
    try:
        from key_value.aio.stores.filetree import FileTreeStore
    except ImportError as exc:  # pragma: no cover - fastmcp pulls this in
        raise _missing_extra("FileTreeStore", "filetree", exc) from exc
    directory = parsed.path
    Path(directory).mkdir(parents=True, exist_ok=True)
    logger.info("kv_store backend=file directory=%s", directory)
    return FileTreeStore(data_directory=directory)


def _redis_store(url: str, parsed: ParseResult) -> AsyncKeyValue:
    """Build the ``redis://`` backend for :func:`_build_backend`."""
    try:
        from key_value.aio.stores.redis import RedisStore
    except ImportError as exc:
        raise _missing_extra("RedisStore", "redis", exc) from exc
    logger.info("kv_store backend=redis host=%s", parsed.hostname)
    return RedisStore(url=url)


def _dynamodb_store(parsed: ParseResult) -> AsyncKeyValue:
    """Build the ``dynamodb://`` backend for :func:`_build_backend`."""
    try:
        from key_value.aio.stores.dynamodb import DynamoDBStore
    except ImportError as exc:
        raise _missing_extra("DynamoDBStore", "dynamodb", exc) from exc
    # The table name lives in the netloc; a stray ":..." is discarded
    # rather than rejected.
    table_name = parsed.netloc.split(":")[0]
    if not table_name:
        raise ConfigurationError(
            "dynamodb:// URL must include a table name, e.g. "
            "'dynamodb://my-table?region=us-east-1'"
        )
    query = parse_qs(parsed.query)
    logger.info("kv_store backend=dynamodb table=%s", table_name)
    return DynamoDBStore(
        table_name=table_name,
        region_name=query.get("region", [None])[0],
        endpoint_url=query.get("endpoint", [None])[0],
    )


def _mongodb_store(url: str, parsed: ParseResult) -> AsyncKeyValue:
    """Build the ``mongodb://`` backend for :func:`_build_backend`."""
    try:
        from key_value.aio.stores.mongodb import MongoDBStore
    except ImportError as exc:
        raise _missing_extra("MongoDBStore", "mongodb", exc) from exc
    logger.info("kv_store backend=mongodb host=%s", parsed.hostname)
    return MongoDBStore(url=url)


def _build_backend(url: str, *, variable: str) -> AsyncKeyValue:
    """Dispatch a URL to its backing store.

    Private so callers cannot bypass the namespace wrapper that
    :func:`build_kv_store` applies.

    Args:
        url: The resolved store URL.
        variable: Name of the setting *url* came from, for the error
            message if it does not parse.
    """
    parsed = parse_operator_url(url, variable=variable)
    scheme = parsed.scheme
    if scheme == "memory":
        from key_value.aio.stores.memory import MemoryStore

        logger.info("kv_store backend=memory lost_on_restart=true")
        return MemoryStore()
    if scheme == "file":
        return _file_store(parsed)
    if scheme == "redis":
        return _redis_store(url, parsed)
    if scheme == "dynamodb":
        return _dynamodb_store(parsed)
    if scheme == "mongodb":
        return _mongodb_store(url, parsed)
    raise ConfigurationError(
        f"Unsupported kv_store URL scheme {scheme!r}. Use one of: "
        "'memory://', 'file:///path', 'redis://...', "
        "'dynamodb://<table>?region=...', 'mongodb://...'."
    )
