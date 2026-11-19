"""Unauthenticated liveness and readiness routes.

Ported from ``fastmcp_pvl_core._health`` 10.2.0 (#2334).

A container orchestrator probing the server has, without this,
nothing better than a TCP connect — which proves a socket is accepting
and nothing else, so a server whose backing store is gone still reports
healthy (fastmcp-pvl-core#313).

Two routes, because the two questions have different answers:

* ``…/health`` — **liveness**. Static, no I/O, ``200`` for as long as the
  process serves. A failure here means restart me.
* ``…/health/ready`` — **readiness**. Runs every registered check and
  answers ``503`` if any fails. A failure here means take me out of
  rotation; it is not a reason to restart, because an unreachable Redis
  is not fixed by killing the process.

Both sit outside the MCP mount and outside auth — that is what a probe
needs, and it is what ``mcp.custom_route`` gives: the MCP endpoint still
answers ``401`` while these answer ``200``.

Path derivation is ours to do. ``custom_route`` registers at the ASGI app
root regardless of where MCP is mounted, so a server mounted under
``/myserver/mcp`` would otherwise publish its health route at the host
root and collide with every sibling sharing that hostname — the same
class of problem as the OAuth discovery route in fastmcp-pvl-core#196. The routes
are therefore derived from the mount path: ``/myserver/mcp`` yields
``/myserver/health``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse

from markdown_vault_mcp._http_path import normalise_http_path
from markdown_vault_mcp._kv_store import build_kv_store
from markdown_vault_mcp._url import redact_urls_in_text

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from starlette.requests import Request

    from markdown_vault_mcp._pvl_core import ServerConfig

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Any]
"""Sync or async zero-arg callable answering "can this server serve?".

Returning falsy, or raising, means not-ready. A coroutine return value is
awaited. Deliberately says nothing about *how* the answer is obtained:
the built-in key-value check is a live round-trip, while a domain check
may be a cached read of a background task's last result. Baking I/O into
this contract would exclude the cheap half.
"""

_KV_CHECK_NAME = "kv_store"
_RESERVED_CHECK_NAMES = frozenset({_KV_CHECK_NAME})
"""Names registered here; a domain check may not shadow them."""

_DETAIL_LEVELS = ("status", "standard", "full")
_DEFAULT_DETAIL = "standard"

_CONVENTIONAL_MOUNT_SEGMENT = "mcp"

_HEALTH_NAMESPACE = "health"
_PROBE_KEY = "probe"
_PROBE_TTL_S = 60.0

_CHECK_TIMEOUT_S = 5.0
"""Per-check ceiling. Generous for a probe, and short enough that a
readiness answer arrives inside a normal orchestrator interval."""


def _resolve_detail(env_prefix: str) -> str:
    """Read ``<PREFIX>_HEALTH_DETAIL``, warning on an unrecognised value.

    Operator configuration rather than a hook: how much an unauthenticated
    body may say depends on whether the port is reachable only from an
    internal network, which the code cannot know.
    """
    from markdown_vault_mcp._env import env

    value = (env(env_prefix, "HEALTH_DETAIL", _DEFAULT_DETAIL) or "").strip().lower()
    if value in _DETAIL_LEVELS:
        return value
    logger.warning(
        "health_detail_unknown value=%r default=%r",
        value,
        _DEFAULT_DETAIL,
    )
    return _DEFAULT_DETAIL


def _health_prefix(http_path: str) -> str:
    """Derive the health path prefix from the MCP mount path.

    Strips the conventional trailing ``mcp`` segment and keeps whatever
    namespace precedes it: ``/scholar/mcp`` yields ``/scholar``, and the
    default ``/mcp`` yields ``""`` so the conventional ``/health`` is what
    a single-server host serves.

    A mount that is *not* the conventional segment keeps its whole path as
    the namespace — ``/scholar`` yields ``/scholar``, not ``""``. Dropping
    the last segment unconditionally would map ``/scholar`` and ``/vault``
    both onto ``/health``, reintroducing the cross-server collision this
    derivation exists to prevent.

    Two servers collide only when one is mounted inside the other's path
    (``/scholar`` and ``/scholar/mcp``), which is already an unworkable
    deployment. Any two mounts that do not nest get distinct health paths.
    """
    mount = normalise_http_path(http_path)
    segments = [part for part in mount.split("/") if part]
    if segments and segments[-1] == _CONVENTIONAL_MOUNT_SEGMENT:
        segments = segments[:-1]
    return "/" + "/".join(segments) if segments else ""


async def _invoke(check: HealthCheck) -> bool:
    """Call one check, awaiting and bounding it when it returns a coroutine.

    Raises whatever the check raises; :func:`_gather_checks` turns that
    into a not-ready verdict. The timeout stops a blackholed backend
    holding the probe open until the OS TCP timeout, which would
    accumulate one readiness coroutine per probe interval and never
    answer inside the prober's own budget. A synchronous check that
    blocks is beyond reach here — that is why anything touching the
    network belongs on the async side of the hook.
    """
    result = check()
    if inspect.isawaitable(result):
        result = await asyncio.wait_for(result, _CHECK_TIMEOUT_S)
    return bool(result)


async def _gather_checks(
    checks: Mapping[str, HealthCheck],
) -> tuple[dict[str, bool], dict[str, BaseException]]:
    """Run every check concurrently and collect verdicts and failures.

    ``return_exceptions=True`` is doing the error handling: a check that
    raises must not fail the route, because "this check blew up" is a
    not-ready verdict like any other, and one broken check must not hide
    the verdicts of the rest. Running them concurrently also keeps the
    probe's latency at the slowest check rather than their sum.
    """
    names = list(checks)
    outcomes = await asyncio.gather(
        *(_invoke(checks[name]) for name in names),
        return_exceptions=True,
    )
    results: dict[str, bool] = {}
    errors: dict[str, BaseException] = {}
    for name, outcome in zip(names, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            results[name] = False
            errors[name] = outcome
        else:
            results[name] = outcome
    return results, errors


def register_health_routes(  # noqa: PLR0913, RUF100 -- ported keyword-only signature
    mcp: FastMCP,
    config: ServerConfig,
    *,
    server_version: str,
    http_path: str,
    env_prefix: str,
    checks: Mapping[str, HealthCheck] | None = None,
) -> None:
    """Register unauthenticated liveness and readiness routes on *mcp*.

    Serves ``<prefix>/health`` and ``<prefix>/health/ready``. ``<prefix>``
    is *http_path* with a conventional trailing ``mcp`` segment removed,
    so the default ``/mcp`` publishes ``/health`` and ``/scholar/mcp``
    publishes ``/scholar/health``. A mount that is not that segment keeps
    its whole path — ``/scholar`` publishes ``/scholar/health`` — see
    :func:`_health_prefix` for why.

    Args:
        mcp: The FastMCP server to register the routes on.
        config: The server's :class:`ServerConfig`; supplies the KV
            backend selection the built-in readiness check probes.
        server_version: Identity: the server's own version. Taken as an
            argument rather than looked up, so no distribution name is
            resolved at runtime. The server *name* is read from
            ``mcp.name``, which the caller already supplied.
        http_path: The same mount path passed to ``mcp.run`` /
            ``mcp.http_app``. Required rather than defaulted: a health
            route published at the wrong prefix is worse than none, and the
            value lives in the caller's CLI.
        env_prefix: The server's env-var prefix, used to read
            ``<PREFIX>_HEALTH_DETAIL`` (operator config: ``status`` /
            ``standard`` / ``full``, default ``standard``).
        checks: Extra readiness checks by name: whether a domain index
            has loaded, for example, which only the caller knows.
            A check answers "does this make the server unable to serve";
            whether a given domain signal clears that bar is the caller's
            judgment, which is why there is no severity knob. Names in
            :data:`_RESERVED_CHECK_NAMES` are rejected.

    Raises:
        ValueError: If *checks* contains a reserved name.
    """
    domain_checks = dict(checks or {})
    clashes = sorted(set(domain_checks) & _RESERVED_CHECK_NAMES)
    if clashes:
        raise ValueError(
            f"health check name(s) {clashes} are reserved; rename the domain check"
        )

    detail = _resolve_detail(env_prefix)
    prefix = _health_prefix(http_path)

    # Built once here rather than per probe. Store construction is lazy
    # (no connection is opened), but ``build_kv_store`` logs its backend
    # selection at INFO, so building it per request would put a
    # ``kv_store backend=…`` line in the operator log on every probe —
    # several a minute under a normal orchestrator interval.
    store = build_kv_store(config, namespace=_HEALTH_NAMESPACE)

    async def _kv_check() -> bool:
        # A write, not a read. ``get`` on a key that was never written
        # returns ``None`` from a backend that is entirely gone —
        # verified against ``file://`` after deleting its directory — so
        # a read-only probe reports ready for exactly the broken-volume
        # case this route exists to catch (fastmcp-pvl-core#313). ``put`` raises there.
        #
        # No readback. On every supported backend a ``put`` that returns
        # has been accepted, so reading it back adds no detection power,
        # and it would add a read-your-write assumption that DynamoDB's
        # default eventually-consistent ``get_item`` and a Mongo
        # secondary read do not make — turning a cold start into a
        # spurious 503 on a healthy server.
        #
        # What this does not catch: a backend that resolved to
        # ``memory://`` because the state directory was unusable at
        # startup. A write succeeds against RAM. That is a startup
        # concern, reported by ``build_kv_store``'s own backend log line.
        await store.put(
            collection=_HEALTH_NAMESPACE,
            key=_PROBE_KEY,
            value={"probe": True},
            ttl=_PROBE_TTL_S,
        )
        return True

    all_checks: dict[str, HealthCheck] = {_KV_CHECK_NAME: _kv_check, **domain_checks}

    @mcp.custom_route(f"{prefix}/health", methods=["GET"])
    async def _liveness(_request: Request) -> JSONResponse:
        body: dict[str, Any] = {"status": "ok"}
        if detail != "status":
            body["server"] = {"name": mcp.name, "version": server_version}
        return JSONResponse(body)

    @mcp.custom_route(f"{prefix}/health/ready", methods=["GET"])
    async def _readiness(_request: Request) -> JSONResponse:
        results, failures = await _gather_checks(all_checks)
        ready = all(results.values())

        body: dict[str, Any] = {"status": "ready" if ready else "not_ready"}
        if detail != "status":
            body["checks"] = results
        # Only checks that *raised* appear here: a check that simply
        # returned falsy reported not-ready without a reason, and there
        # is nothing to name. Its verdict is already in ``checks``.
        if detail == "full" and failures:
            body["errors"] = {
                name: {
                    "type": type(exc).__name__,
                    "detail": redact_urls_in_text(str(exc)),
                }
                for name, exc in failures.items()
            }
        return JSONResponse(body, status_code=200 if ready else 503)
