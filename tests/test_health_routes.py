"""The health/readiness routes (#2334).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_health.py``; its
``TestPublicSurface`` pinned pvl-core's own exports and did not come along.

Every case drives a real FastMCP ASGI app over httpx rather than calling
the handlers directly, because two of the properties under test —
where the routes land relative to the MCP mount, and that they answer
without auth — are properties of the app, not of the handler.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

from markdown_vault_mcp._health import (
    _health_prefix,
    _resolve_detail,
    register_health_routes,
)
from markdown_vault_mcp._pvl_core import ServerConfig, build_auth
from markdown_vault_mcp._url import redact_urls_in_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

_ENV_PREFIX = "APP"
_HEALTH_LOGGER = "markdown_vault_mcp._health"


class _FakeStore:
    """Minimal ``AsyncKeyValue`` stand-in for the readiness write.

    Only ``put`` is exercised: the check writes and does not read back,
    because a ``put`` that returns has been accepted on every supported
    backend and a readback would assume read-your-write.
    """

    def __init__(self, *, fail: BaseException | None = None) -> None:
        self._fail = fail

    async def put(self, **_kwargs: Any) -> None:
        if self._fail is not None:
            raise self._fail


async def _assert_check(
    client: AsyncClient, name: str, *, ok: bool, path: str = "/health/ready"
) -> dict[str, Any]:
    """Assert one named check's verdict and the status code implied by it."""
    response = await client.get(path)
    assert response.status_code == (200 if ok else 503)
    body = response.json()
    assert body["checks"][name] is ok
    return body


@asynccontextmanager
async def _serve(mcp: FastMCP, http_path: str) -> AsyncIterator[AsyncClient]:
    """Drive *mcp* as a real ASGI app over httpx."""
    app = mcp.http_app(path=http_path)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


@asynccontextmanager
async def _client(
    *,
    http_path: str = "/mcp",
    checks: Mapping[str, Any] | None = None,
    store: _FakeStore | None = None,
    auth_token: str | None = None,
) -> AsyncIterator[AsyncClient]:
    config = ServerConfig(bearer_token=auth_token)
    mcp = FastMCP("probe-mcp", auth=build_auth(config) if auth_token else None)
    with patch(
        "markdown_vault_mcp._health.build_kv_store",
        return_value=store if store is not None else _FakeStore(),
    ):
        register_health_routes(
            mcp,
            config,
            server_version="1.2.3",
            http_path=http_path,
            env_prefix=_ENV_PREFIX,
            checks=checks,
        )
        async with _serve(mcp, http_path) as client:
            yield client


class TestPathDerivation:
    """A custom route lands at the app root, so the prefix is ours to derive."""

    @pytest.mark.asyncio
    async def test_root_mount_serves_at_health(self):
        async with _client(http_path="/mcp") as c:
            assert (await c.get("/health")).status_code == 200
            assert (await c.get("/health/ready")).status_code == 200

    @pytest.mark.asyncio
    async def test_subpath_mount_namespaces_both_routes(self):
        async with _client(http_path="/myserver/mcp") as c:
            assert (await c.get("/myserver/health")).status_code == 200
            assert (await c.get("/myserver/health/ready")).status_code == 200

    @pytest.mark.asyncio
    async def test_subpath_mount_does_not_also_serve_at_the_root(self):
        """The point of deriving: two servers must be able to share a host."""
        async with _client(http_path="/myserver/mcp") as c:
            assert (await c.get("/health")).status_code == 404


class TestLiveness:
    @pytest.mark.asyncio
    async def test_stays_ok_while_a_dependency_is_down(self):
        """Liveness answers "is the process serving", not "is it useful"."""
        async with _client(store=_FakeStore(fail=RuntimeError("kv down"))) as c:
            response = await c.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_reports_identity_at_the_default_detail(self):
        async with _client() as c:
            body = (await c.get("/health")).json()
            assert body["server"] == {"name": "probe-mcp", "version": "1.2.3"}


class TestReadiness:
    @pytest.mark.asyncio
    async def test_ready_when_every_check_passes(self):
        async with _client() as c:
            response = await c.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["status"] == "ready"
            assert response.json()["checks"] == {"kv_store": True}

    @pytest.mark.asyncio
    async def test_503_when_the_kv_round_trip_fails(self):
        async with _client(store=_FakeStore(fail=RuntimeError("kv down"))) as c:
            body = await _assert_check(c, "kv_store", ok=False)
            assert body["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_503_when_a_domain_check_returns_falsy(self):
        async with _client(checks={"s2_key": lambda: False}) as c:
            response = await c.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["checks"] == {"kv_store": True, "s2_key": False}

    @pytest.mark.asyncio
    async def test_a_raising_domain_check_is_not_ready_rather_than_a_500(self):
        def _boom() -> bool:
            raise RuntimeError("key revoked")

        async with _client(checks={"s2_key": _boom}) as c:
            await _assert_check(c, "s2_key", ok=False)

    @pytest.mark.asyncio
    async def test_accepts_an_async_domain_check(self):
        """Scholar's signal is a cached read; a KV probe is I/O. Both fit."""

        async def _ok() -> bool:
            return True

        async with _client(checks={"s2_key": _ok}) as c:
            await _assert_check(c, "s2_key", ok=True)

    @pytest.mark.asyncio
    async def test_one_failing_check_does_not_hide_the_others(self):
        async with _client(checks={"a": lambda: True, "b": lambda: False}) as c:
            checks = (await c.get("/health/ready")).json()["checks"]
            assert checks == {"kv_store": True, "a": True, "b": False}


class TestStoreLifetime:
    @pytest.mark.asyncio
    async def test_the_kv_store_is_built_once_not_per_probe(self):
        """``build_kv_store`` logs its backend at INFO on every call.

        Building per request would put a ``kv_store backend=…`` line in
        the operator log several times a minute under a normal probe
        interval.
        """
        mcp = FastMCP("probe-mcp")
        with patch(
            "markdown_vault_mcp._health.build_kv_store", return_value=_FakeStore()
        ) as build:
            register_health_routes(
                mcp,
                ServerConfig(),
                server_version="1.2.3",
                http_path="/mcp",
                env_prefix=_ENV_PREFIX,
            )
            async with _serve(mcp, "/mcp") as c:
                for _ in range(3):
                    assert (await c.get("/health/ready")).status_code == 200
        assert build.call_count == 1


class TestReservedCheckNames:
    def test_a_domain_check_cannot_shadow_a_core_check(self):
        mcp = FastMCP("probe-mcp")
        with pytest.raises(ValueError, match="kv_store"):
            register_health_routes(
                mcp,
                ServerConfig(),
                server_version="1.2.3",
                http_path="/mcp",
                env_prefix=_ENV_PREFIX,
                checks={"kv_store": lambda: True},
            )


class TestDetailLevels:
    @pytest.mark.asyncio
    async def test_status_level_omits_identity_and_checks(self, monkeypatch):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "status")
        async with _client(store=_FakeStore(fail=RuntimeError("kv down"))) as c:
            assert (await c.get("/health")).json() == {"status": "ok"}
            ready = await c.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json() == {"status": "not_ready"}

    @pytest.mark.asyncio
    async def test_standard_level_reports_booleans_but_no_reason(self, monkeypatch):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "standard")
        async with _client(store=_FakeStore(fail=RuntimeError("kv down"))) as c:
            body = (await c.get("/health/ready")).json()
            assert body["checks"] == {"kv_store": False}
            assert "errors" not in body

    @pytest.mark.asyncio
    async def test_full_level_names_the_failure(self, monkeypatch):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "full")
        async with _client(store=_FakeStore(fail=RuntimeError("kv down"))) as c:
            body = (await c.get("/health/ready")).json()
            assert body["errors"]["kv_store"]["type"] == "RuntimeError"
            assert "kv down" in body["errors"]["kv_store"]["detail"]

    @pytest.mark.asyncio
    async def test_full_level_reports_nothing_when_everything_passes(self, monkeypatch):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "full")
        async with _client() as c:
            assert "errors" not in (await c.get("/health/ready")).json()

    @pytest.mark.asyncio
    async def test_unrecognised_level_warns_and_falls_back_to_standard(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "verbose")
        caplog.set_level(logging.DEBUG)
        async with _client() as c:
            body = (await c.get("/health")).json()
        assert body["server"]["name"] == "probe-mcp"
        warnings = [
            r
            for r in caplog.records
            if r.name == _HEALTH_LOGGER and "health_detail_unknown" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING


class TestDetailResolution:
    """Pins the resolved value, not its downstream effect.

    Every unknown string behaves like ``standard`` by accident of how the
    body comparisons read (``!= "status"``, ``== "full"``), so asserting
    on a response body cannot tell a real fallback from a passthrough.
    """

    def test_unset_defaults_to_standard(self, monkeypatch):
        monkeypatch.delenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", raising=False)
        assert _resolve_detail(_ENV_PREFIX) == "standard"

    @pytest.mark.parametrize("level", ["status", "standard", "full"])
    def test_each_valid_level_is_returned_verbatim(self, monkeypatch, level):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", level)
        assert _resolve_detail(_ENV_PREFIX) == level

    @pytest.mark.parametrize("raw", ["verbose", "ful", "", "  ", "FULL "])
    def test_unknown_and_padded_values(self, monkeypatch, raw):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", raw)
        expected = "full" if raw.strip().lower() == "full" else "standard"
        assert _resolve_detail(_ENV_PREFIX) == expected


class TestFullLevelRedaction:
    """``full`` is for trusted networks, but it still never emits a credential."""

    @pytest.mark.asyncio
    async def test_userinfo_and_query_are_stripped_from_urls_in_the_reason(
        self, monkeypatch
    ):
        monkeypatch.setenv(f"{_ENV_PREFIX}_HEALTH_DETAIL", "full")
        failure = RuntimeError(
            "cannot reach redis://admin:hunter2@cache.internal:6379/0?token=abc"
        )
        async with _client(store=_FakeStore(fail=failure)) as c:
            reported = (await c.get("/health/ready")).json()["errors"]["kv_store"][
                "detail"
            ]
        assert "hunter2" not in reported
        assert "admin" not in reported
        assert "token=abc" not in reported
        assert "cache.internal" in reported


class TestUnauthenticatedAccess:
    @pytest.mark.asyncio
    async def test_both_routes_answer_while_the_mcp_mount_requires_auth(self):
        async with _client(auth_token="secret") as c:
            assert (await c.get("/health")).status_code == 200
            assert (await c.get("/health/ready")).status_code == 200
            assert (await c.get("/mcp")).status_code == 401


class TestKvCheckDetectsARealFailure:
    """Against a real store, not the fake — the fake cannot prove this."""

    @pytest.mark.asyncio
    async def test_a_vanished_file_backend_is_not_ready(self, tmp_path):
        """The exact failure #313 opens with.

        ``get`` on a never-written key returns ``None`` from a backend
        whose directory has been deleted, so a read-only probe answers
        ready for a server that has lost its state entirely. Only a write
        round-trip notices.
        """
        import shutil

        directory = tmp_path / "state"
        directory.mkdir()
        config = ServerConfig(kv_store_url=f"file://{directory}")
        mcp = FastMCP("probe-mcp")
        register_health_routes(
            mcp,
            config,
            server_version="1.2.3",
            http_path="/mcp",
            env_prefix=_ENV_PREFIX,
        )
        async with _serve(mcp, "/mcp") as c:
            assert (await c.get("/health/ready")).status_code == 200
            shutil.rmtree(directory)
            assert (await c.get("/health/ready")).status_code == 503


class TestCheckTimeout:
    @pytest.mark.asyncio
    async def test_a_hanging_check_is_not_ready_rather_than_hanging_the_probe(
        self, monkeypatch
    ):
        import asyncio

        monkeypatch.setattr("markdown_vault_mcp._health._CHECK_TIMEOUT_S", 0.05)

        async def _hang() -> bool:
            await asyncio.sleep(30)
            return True

        async with _client(checks={"slow": _hang}) as c:
            body = await asyncio.wait_for(_assert_check(c, "slow", ok=False), timeout=5)
        assert body["status"] == "not_ready"


class TestPrefixInjectivity:
    @pytest.mark.parametrize(
        ("mount", "expected"),
        [
            ("/mcp", ""),
            ("/", ""),
            ("/scholar/mcp", "/scholar"),
            ("/api/v1/mcp", "/api/v1"),
            ("/scholar", "/scholar"),
            ("/vault", "/vault"),
        ],
    )
    def test_prefix_derivation(self, mount, expected):
        assert _health_prefix(mount) == expected

    def test_non_nesting_mounts_never_share_a_health_path(self):
        """The property the derivation exists for.

        Dropping the final segment unconditionally mapped ``/scholar`` and
        ``/vault`` both onto ``/health``.
        """
        mounts = ["/mcp", "/scholar", "/vault", "/api/v1/mcp", "/tenant-a/mcp"]
        prefixes = [_health_prefix(m) for m in mounts]
        assert len(set(prefixes)) == len(prefixes)

    def test_a_mount_nested_inside_another_is_the_one_case_that_collides(self):
        """Documented rather than fixed: those two cannot coexist anyway."""
        assert _health_prefix("/scholar") == _health_prefix("/scholar/mcp")


class TestRedactionRobustness:
    @pytest.mark.parametrize(
        "url",
        [
            "mongodb://u:pw@h1:27017,h2:27017/db",
            "redis://user:pw@[::1]:6379/0",
            "redis://user:pw@host:notaport/0",
            "redis://user:pw@host:99999/0",
        ],
    )
    def test_exotic_authorities_do_not_raise(self, url):
        """A redactor that can raise turns a 503 into an unhandled 500."""
        out = redact_urls_in_text(f"cannot reach {url}")
        assert "pw" not in out
        assert out.startswith("cannot reach ")

    def test_every_url_in_one_token_is_stripped_not_just_the_first(self):
        """A single non-greedy substitution left the second credential."""
        out = redact_urls_in_text("redis://u1:p1@h1/0,redis://u2:secret2@h2/0")
        assert "secret2" not in out
        assert "p1" not in out
        assert out == "redis://h1/0,redis://h2/0"

    def test_a_password_containing_an_at_sign_is_fully_stripped(self):
        """Stopping at the first ``@`` left the rest of the password."""
        out = redact_urls_in_text("redis://user:p@ss@cache.internal:6379/0")
        assert "ss@" not in out
        assert out == "redis://cache.internal:6379/0"
