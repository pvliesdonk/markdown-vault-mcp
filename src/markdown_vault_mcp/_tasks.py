"""Background-task backend wiring for FastMCP's SEP-2663 tasks extension.

Ported from ``fastmcp_pvl_core._tasks`` 10.2.0 (#2292).

FastMCP refuses to start a server carrying ``task=``-enabled tools without a
registered ``TasksExtension``, and the extension's Docket backend is selected
natively through ``FASTMCP_DOCKET_*``. :func:`configure_task_backend` resolves
the backend from this server's own variables instead, then constructs and
registers the extension.

Docket supports two backends: ``memory://`` (in-process, one process only) and
``redis://`` (distributed). It is a task queue, not a key-value store, so the
kv backends cannot back it; what is shared with ``KV_STORE_URL`` is the
operator's configuration, not the storage.

Worker tunables (``FASTMCP_DOCKET_CONCURRENCY``, ``..._WORKER_NAME``,
``..._REDELIVERY_TIMEOUT``, ``..._RECONNECTION_DELAY``,
``..._MINIMUM_CHECK_INTERVAL``) are deliberately not wrapped: the extension's
own settings read them, with sensible upstream defaults.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from markdown_vault_mcp._env import _resolve_key
from markdown_vault_mcp._url import parse_operator_url
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp_tasks import TasksExtension

    from markdown_vault_mcp.config_sections.server import ServerConfig

logger = logging.getLogger(__name__)

_DOCKET_SCHEMES = frozenset({"memory", "redis"})
"""URL schemes Docket accepts, the validation set for ``TASKS_URL``."""


def _derive_queue_name(env_prefix: str) -> str:
    """Derive the Docket queue name from the env prefix.

    Two servers sharing one Redis must not share a task queue, so the name
    replaces FastMCP's global ``"fastmcp"`` default with this server's
    identity.
    """
    return env_prefix.rstrip("_").lower().replace("_", "-")


def _tasks_available() -> bool:
    """Whether the tasks extension can run in this environment.

    Mirrors FastMCP's own activation conditions: a compatible pydocket and an
    importable ``fastmcp_tasks``. Both arrive with ``fastmcp[tasks]``; the
    guard survives for stripped installs and incompatible pydocket pins.
    """
    from fastmcp.server.dependencies import is_docket_available

    if not is_docket_available():
        return False
    # ModuleNotFoundError, not ImportError: a present fastmcp_tasks that
    # fails to import is version skew and must stay loud.
    try:
        import fastmcp_tasks  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _env_override(var: str) -> str | None:
    """The native env var's value, with empty or whitespace treated as unset.

    Otherwise ``FASTMCP_DOCKET_URL=""`` would suppress the kv derivation and
    ``FASTMCP_DOCKET_NAME=""`` would put every server on one empty queue name.
    """
    return (os.environ.get(var) or "").strip() or None


def _validated_tasks_url(env_prefix: str, config: ServerConfig) -> str | None:
    """Return ``config.tasks_url`` after checking Docket supports its scheme.

    Messages name the variable and the scheme, never the URL, which may carry
    credentials.

    Raises:
        ConfigurationError: The URL does not parse or its scheme is not
            ``memory`` or ``redis``.
    """
    if not config.tasks_url:
        return None
    variable = _resolve_key(env_prefix, "TASKS_URL")
    scheme = parse_operator_url(config.tasks_url, variable=variable).scheme
    if scheme not in _DOCKET_SCHEMES:
        raise ConfigurationError(
            f"{variable} has unsupported scheme {scheme!r}. Docket supports "
            "'memory://' and 'redis://...' only."
        )
    return config.tasks_url


def _resolve_url_override(
    env_prefix: str, config: ServerConfig, explicit_url: str | None
) -> str | None:
    """Resolve the ``url`` argument for the extension.

    Implements the precedence documented on :func:`configure_task_backend`.
    ``None`` means no override: the extension falls back to its own
    ``FASTMCP_DOCKET_*`` defaults.
    """
    native_url = _env_override("FASTMCP_DOCKET_URL")
    if explicit_url is not None:
        # Explicit against explicit: this server's variable wins, and the
        # divergence is never silent.
        if native_url not in (None, explicit_url):
            logger.warning(
                "tasks_url_env_conflict server_var=%s "
                "native_var=FASTMCP_DOCKET_URL winner=server_var action=%s",
                _resolve_key(env_prefix, "TASKS_URL"),
                "unset one of them",
            )
        return explicit_url
    if native_url is not None:
        # The native escape hatch stands.
        return None
    # Reuse the kv backend when it is a Redis Docket can use, in the same
    # order as build_kv_store: kv_store_url, then the legacy
    # event_store_url (whose deprecation warning build_kv_store emits).
    kv_url = config.kv_store_url or config.event_store_url
    if not kv_url:
        return None
    # Named for the variable actually set: the failure is in the kv URL,
    # even though it surfaces while the task backend is being configured.
    variable = (
        _resolve_key(env_prefix, "KV_STORE_URL")
        if config.kv_store_url
        else _resolve_key(env_prefix, "EVENT_STORE_URL")
    )
    if parse_operator_url(kv_url, variable=variable).scheme == "redis":
        return kv_url
    return None


def _resolve_name_override(env_prefix: str) -> str | None:
    """Derive the queue name unless ``FASTMCP_DOCKET_NAME`` is set."""
    if _env_override("FASTMCP_DOCKET_NAME") is None:
        return _derive_queue_name(env_prefix)
    return None


def _log_unavailable(env_prefix: str, explicit_url: str | None) -> None:
    """Say why no extension was registered; louder if the operator set a URL."""
    if explicit_url is None:
        logger.debug(
            "tasks_extension_not_registered reason=%s",
            "fastmcp-tasks/pydocket not installed",
        )
        return
    logger.warning(
        "tasks_url_dropped var=%s reason=%s hint=%s consequence=%s",
        _resolve_key(env_prefix, "TASKS_URL"),
        "fastmcp-tasks or a compatible pydocket is not installed",
        "both ship with fastmcp[tasks], a base dependency of this server; "
        "this environment is missing them or pins an incompatible version",
        "no tasks extension registered",
    )


def _log_effective_backend(
    env_prefix: str, config: ServerConfig, extension: TasksExtension
) -> None:
    """Log the backend the extension ended up with.

    Raises:
        ConfigurationError: The effective ``FASTMCP_DOCKET_URL`` does not
            parse; it may come from the native variable, which nothing here
            validated before.
    """
    effective = extension.docket_settings
    scheme = parse_operator_url(effective.url, variable="FASTMCP_DOCKET_URL").scheme
    if scheme == "memory" and config.transport in ("http", "sse"):
        logger.info(
            "task backend=memory process_local=true lost_on_restart=true action=%s",
            f"set {_resolve_key(env_prefix, 'TASKS_URL')} or a redis kv_store_url "
            "for a durable, multi-process queue",
        )
    else:
        logger.info("task backend=%s queue=%s", scheme, effective.name)


def configure_task_backend(
    mcp: FastMCP, env_prefix: str, config: ServerConfig
) -> TasksExtension | None:
    """Resolve the background-task backend and register the tasks extension.

    Constructs a ``fastmcp_tasks.TasksExtension`` with the resolved backend
    and registers it with ``mcp.add_extension(...)``. FastMCP rejects
    duplicate extensions and registration after startup, so call once,
    before the server runs.

    URL resolution:

    1. ``config.tasks_url`` set: validated against Docket's schemes
       (``memory://``, ``redis://``) and passed to the extension.
    2. Unset, and ``config.kv_store_url`` (or the legacy ``event_store_url``)
       is ``redis://``: that URL is reused, so one Redis URL configures every
       stateful subsystem and the task queue. An explicitly set
       ``FASTMCP_DOCKET_URL`` outranks this derived default.
    3. Otherwise no URL is passed, and the extension's own default applies
       (``FASTMCP_DOCKET_URL`` when set, ``memory://`` otherwise).

    When ``TASKS_URL`` and ``FASTMCP_DOCKET_URL`` are both set and disagree,
    ``TASKS_URL`` wins and a warning names both.

    Native-variable precedence reads the process environment only. A
    ``FASTMCP_DOCKET_*`` value that only the extension's dotenv file supplies
    is invisible to these checks and is overridden.

    The queue name is derived from *env_prefix* unless ``FASTMCP_DOCKET_NAME``
    is set.

    Without the tasks machinery nothing is registered and this returns
    ``None``, but an explicit ``TASKS_URL`` is still validated, and dropped
    with a warning when valid.

    Args:
        mcp: The root server under assembly. A mounted child's extensions do
            not propagate to the root.
        env_prefix: Env-var prefix of this server, trailing underscore
            optional. Names the variables in messages and derives the queue
            name.
        config: Server configuration; ``tasks_url``, ``kv_store_url``,
            ``event_store_url`` and ``transport`` are read.

    Returns:
        The registered ``TasksExtension``, or ``None`` when the tasks
        machinery is unavailable.

    Raises:
        ConfigurationError: ``config.tasks_url`` has a scheme Docket does not
            support, or a URL this reads does not parse: ``tasks_url``, the
            kv URL it may reuse, or the effective ``FASTMCP_DOCKET_URL``
            (checked after registration). Messages name the variable, never
            its value.
    """
    # Validate before anything can short-circuit: a typo in TASKS_URL fails
    # fast even when the tasks machinery is missing.
    explicit_url = _validated_tasks_url(env_prefix, config)
    if not _tasks_available():
        _log_unavailable(env_prefix, explicit_url)
        return None

    from fastmcp_tasks import TasksExtension

    # ``None`` arguments fall back to the extension's FASTMCP_DOCKET_* env
    # defaults, which is the escape-hatch behaviour described above.
    extension = TasksExtension(
        url=_resolve_url_override(env_prefix, config, explicit_url),
        name=_resolve_name_override(env_prefix),
    )
    mcp.add_extension(extension)
    _log_effective_backend(env_prefix, config, extension)
    return extension
