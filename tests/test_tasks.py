"""Tests for ``configure_task_backend`` (ported from pvl-core 10.2.0, #2292)."""

from __future__ import annotations

import logging

import pytest
from fastmcp import FastMCP

from markdown_vault_mcp._tasks import _resolve_url_override, configure_task_backend
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError

_AVAILABLE = "fastmcp.server.dependencies.is_docket_available"

# NFKC-normalising host, so ``urlparse`` raises with the netloc in its
# message. A constant, because Python 3.13+ tracebacks quote the call's source.
_LEAKY_URL = "redis://alice:hunter2@h\u2100st"


@pytest.fixture(autouse=True)
def _neutral_docket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the native Docket env vars to a known baseline.

    ``TasksExtension`` resolves anything not passed explicitly from
    ``FASTMCP_DOCKET_*``; clearing makes the "operator set a native var"
    scenarios opt-in per test.
    """
    monkeypatch.delenv("FASTMCP_DOCKET_URL", raising=False)
    monkeypatch.delenv("FASTMCP_DOCKET_NAME", raising=False)


@pytest.fixture
def docket_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``is_docket_available()`` to True.

    pydocket ships with ``fastmcp[tasks]``, so this normally
    matches reality; pinning keeps the wiring tests deterministic in a
    stripped environment (and symmetric with the tests that pin False).
    """
    monkeypatch.setattr(_AVAILABLE, lambda: True)


@pytest.fixture
def mcp() -> FastMCP:
    """A fresh, unstarted server to register the extension on."""
    return FastMCP("tasks-test")


def _settings(extension):
    assert extension is not None, "expected a registered TasksExtension"
    return extension.docket_settings


def _warning_text(caplog: pytest.LogCaptureFixture) -> str:
    """Join the captured WARNING messages for content assertions."""
    return "\n".join(
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    )


@pytest.mark.usefixtures("docket_installed")
class TestExplicitTasksUrl:
    def test_redis_url_reaches_extension(self, mcp):
        config = ServerConfig(tasks_url="redis://queue-host:6379/0")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://queue-host:6379/0"

    def test_memory_url_accepted(self, mcp):
        config = ServerConfig(tasks_url="memory://")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "memory://"

    def test_unparseable_url_does_not_leak_userinfo(self, mcp):
        """A URL ``urlparse`` rejects must not echo its netloc.

        CPython's ``_checknetloc`` puts the raw netloc, userinfo
        included, in its ``ValueError``.
        """
        import traceback

        config = ServerConfig(tasks_url=_LEAKY_URL)
        with pytest.raises(ConfigurationError) as exc_info:
            configure_task_backend(mcp, "MY_APP", config)
        rendered = "".join(
            traceback.format_exception(
                type(exc_info.value), exc_info.value, exc_info.value.__traceback__
            )
        )
        for secret in ("hunter2", "alice"):
            assert secret not in str(exc_info.value)
            assert secret not in rendered
        assert "MY_APP_TASKS_URL" in str(exc_info.value)

    @pytest.mark.parametrize("bad", ["file:///data/q", "postgres://h/db", "redis"])
    def test_unsupported_scheme_raises_naming_var(self, mcp, bad):
        config = ServerConfig(tasks_url=bad)
        with pytest.raises(ConfigurationError, match="MY_APP_TASKS_URL"):
            configure_task_backend(mcp, "MY_APP", config)

    def test_invalid_scheme_raises_even_without_pydocket(self, monkeypatch, mcp):
        """The strict validation must not be short-circuited by the no-op.

        An operator typo in an explicitly-set var fails fast regardless of
        whether the extra is installed (the ``PORT`` precedent).
        """
        monkeypatch.setattr(_AVAILABLE, lambda: False)
        config = ServerConfig(tasks_url="file:///data/q")
        with pytest.raises(ConfigurationError, match="MY_APP_TASKS_URL"):
            configure_task_backend(mcp, "MY_APP", config)

    def test_error_names_scheme_not_url(self, mcp):
        """Redaction: the raw URL (possible credentials) never appears."""
        config = ServerConfig(tasks_url="amqp://user:" + "hunter2@host/vq")
        with pytest.raises(ConfigurationError) as exc_info:
            configure_task_backend(mcp, "MY_APP", config)
        assert "hunter2" not in str(exc_info.value)
        assert "'amqp'" in str(exc_info.value)

    def test_wins_over_redis_kv_store_url(self, mcp):
        config = ServerConfig(
            tasks_url="redis://tasks-host:6379/1",
            kv_store_url="redis://kv-host:6379/0",
        )
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://tasks-host:6379/1"


@pytest.mark.usefixtures("docket_installed")
class TestKvDerivation:
    def test_redis_kv_store_url_reused(self, mcp):
        config = ServerConfig(kv_store_url="redis://kv-host:6379/0")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://kv-host:6379/0"

    def test_legacy_event_store_url_reused(self, mcp):
        config = ServerConfig(event_store_url="redis://legacy-host:6379/0")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://legacy-host:6379/0"

    @pytest.mark.parametrize(
        "kv_url", ["memory://", "file:///data/state", "mongodb://h:27017/db", None]
    )
    def test_non_redis_kv_leaves_url_untouched(self, mcp, kv_url):
        config = ServerConfig(kv_store_url=kv_url)
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "memory://"


@pytest.mark.usefixtures("docket_installed")
class TestNativeEscapeHatch:
    def test_native_url_beats_kv_derivation(self, mcp, monkeypatch):
        """An explicit FASTMCP_DOCKET_URL outranks the derived default."""
        monkeypatch.setenv("FASTMCP_DOCKET_URL", "redis://native-host:6379/2")
        config = ServerConfig(kv_store_url="redis://kv-host:6379/0")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://native-host:6379/2"

    def test_explicit_tasks_url_beats_native_with_warning(
        self, mcp, monkeypatch, caplog
    ):
        monkeypatch.setenv("FASTMCP_DOCKET_URL", "redis://native-host:6379/2")
        config = ServerConfig(tasks_url="redis://tasks-host:6379/1")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._tasks"):
            ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://tasks-host:6379/1"
        warning = _warning_text(caplog)
        assert "MY_APP_TASKS_URL" in warning
        assert "FASTMCP_DOCKET_URL" in warning

    def test_agreeing_explicit_values_do_not_warn(self, mcp, monkeypatch, caplog):
        monkeypatch.setenv("FASTMCP_DOCKET_URL", "redis://same-host:6379/0")
        config = ServerConfig(tasks_url="redis://same-host:6379/0")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._tasks"):
            configure_task_backend(mcp, "MY_APP", config)
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_native_name_respected(self, mcp, monkeypatch):
        monkeypatch.setenv("FASTMCP_DOCKET_NAME", "operator-queue")
        ext = configure_task_backend(mcp, "MY_APP", ServerConfig())
        assert _settings(ext).name == "operator-queue"

    def test_empty_native_url_counts_as_unset(self, mcp, monkeypatch):
        """FASTMCP_DOCKET_URL="" must not suppress the kv derivation."""
        monkeypatch.setenv("FASTMCP_DOCKET_URL", "")
        config = ServerConfig(kv_store_url="redis://kv-host:6379/0")
        ext = configure_task_backend(mcp, "MY_APP", config)
        assert _settings(ext).url == "redis://kv-host:6379/0"

    def test_empty_native_name_counts_as_unset(self, mcp, monkeypatch):
        """FASTMCP_DOCKET_NAME="" must not collapse servers onto one
        empty queue name — the derivation still applies."""
        monkeypatch.setenv("FASTMCP_DOCKET_NAME", "  ")
        ext = configure_task_backend(mcp, "MY_APP", ServerConfig())
        assert _settings(ext).name == "my-app"


@pytest.mark.usefixtures("docket_installed")
class TestQueueName:
    def test_derived_from_prefix(self, mcp):
        ext = configure_task_backend(mcp, "MY_APP", ServerConfig())
        assert _settings(ext).name == "my-app"

    def test_trailing_underscore_normalised(self, mcp):
        ext = configure_task_backend(mcp, "MY_APP_", ServerConfig())
        assert _settings(ext).name == "my-app"

    def test_derived_even_when_url_untouched(self, mcp):
        """Queue identity is set on every path, not only when a URL resolves.

        Two family servers pointed at one Redis via native vars must still
        get distinct queues.
        """
        config = ServerConfig(kv_store_url="file:///data/state")
        ext = configure_task_backend(mcp, "SCHOLAR_MCP", config)
        assert _settings(ext).name == "scholar-mcp"


@pytest.mark.usefixtures("docket_installed")
class TestExtensionRegistration:
    async def test_extension_is_registered_on_the_server(self, mcp):
        """A task=True tool starts without further wiring once
        ``configure_task_backend`` ran."""
        from fastmcp import Client
        from fastmcp.utilities.tasks import TaskConfig

        configure_task_backend(mcp, "MY_APP", ServerConfig())

        @mcp.tool(task=TaskConfig(mode="optional"))
        async def slow() -> str:
            return "done"

        # Startup performs the task-enabled-tools-need-the-extension check;
        # connecting a client exercises it.
        async with Client(mcp) as client:
            tools = await client.list_tools()
        assert "slow" in [t.name for t in tools]

    def test_second_call_raises_on_duplicate_extension(self, mcp):
        """fastmcp rejects duplicate extension identifiers; calling the
        helper twice on one server is a caller bug, surfaced loudly."""
        configure_task_backend(mcp, "MY_APP", ServerConfig())
        with pytest.raises(ValueError):
            configure_task_backend(mcp, "MY_APP", ServerConfig())


class TestNoPydocket:
    def test_noop_without_pydocket(self, monkeypatch, mcp):
        monkeypatch.setattr(_AVAILABLE, lambda: False)
        config = ServerConfig(kv_store_url="redis://kv-host:6379/0")
        assert configure_task_backend(mcp, "MY_APP", config) is None

    def test_noop_without_fastmcp_tasks_module(self, monkeypatch, mcp, caplog):
        """A stripped fork: pydocket compatible but fastmcp_tasks absent.

        ``sys.modules[name] = None`` makes ``import fastmcp_tasks`` raise
        ModuleNotFoundError, the exact exception the guard narrows to.
        """
        import sys

        monkeypatch.setattr(_AVAILABLE, lambda: True)
        monkeypatch.setitem(sys.modules, "fastmcp_tasks", None)
        config = ServerConfig(tasks_url="redis://tasks-host:6379/1")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._tasks"):
            assert configure_task_backend(mcp, "MY_APP", config) is None
        assert "MY_APP_TASKS_URL" in _warning_text(caplog)

    def test_explicit_tasks_url_warns_when_dropped(self, monkeypatch, mcp, caplog):
        monkeypatch.setattr(_AVAILABLE, lambda: False)
        config = ServerConfig(tasks_url="redis://tasks-host:6379/1")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._tasks"):
            assert configure_task_backend(mcp, "MY_APP", config) is None
        warning = _warning_text(caplog)
        assert "MY_APP_TASKS_URL" in warning
        assert "pydocket" in warning


@pytest.mark.usefixtures("docket_installed")
class TestMemoryBackendSignal:
    def test_memory_plus_http_logs_process_local_note(self, mcp, caplog):
        config = ServerConfig(transport="http")
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._tasks"):
            configure_task_backend(mcp, "MY_APP", config)
        messages = "\n".join(r.getMessage() for r in caplog.records)
        assert "backend=memory" in messages
        assert "process_local=true" in messages

    def test_memory_plus_stdio_logs_plain_line(self, mcp, caplog):
        config = ServerConfig(transport="stdio")
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._tasks"):
            configure_task_backend(mcp, "MY_APP", config)
        messages = "\n".join(r.getMessage() for r in caplog.records)
        assert "process_local" not in messages

    def test_redis_logs_scheme_and_queue_only(self, mcp, caplog):
        """Redaction: host/credentials never reach the log line."""
        config = ServerConfig(
            transport="http", tasks_url="redis://user:" + "hunter2@tasks-host:6379/1"
        )
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp._tasks"):
            configure_task_backend(mcp, "MY_APP", config)
        messages = "\n".join(r.getMessage() for r in caplog.records)
        assert "backend=redis" in messages
        assert "queue=my-app" in messages
        assert "hunter2" not in messages


def test_reused_kv_url_does_not_leak_userinfo():
    """The kv URL the task backend may reuse is parsed without echoing it."""
    import traceback

    with pytest.raises(ConfigurationError) as exc_info:
        _resolve_url_override("MY_APP", ServerConfig(kv_store_url=_LEAKY_URL), None)
    exc = exc_info.value
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    for secret in ("hunter2", "alice"):
        assert secret not in rendered
    assert "MY_APP_KV_STORE_URL" in str(exc)
