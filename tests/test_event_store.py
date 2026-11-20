"""Tests for event store configuration and build_event_store()."""

from __future__ import annotations

import pytest

from markdown_vault_mcp._kv_store import build_event_store
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError

# NFKC-normalising host, so ``urlparse`` raises with the netloc in its message.
_LEAKY_URL = "redis://alice:hunter2@h\u2100st"


class TestBuildEventStore:
    """Unit tests for build_event_store() backend selection from ServerConfig."""

    def test_default_uses_file_backend(self, tmp_path):
        """An empty config defaults to a file-backed store at the package default dir."""
        from unittest.mock import patch

        with patch(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR",
            str(tmp_path / "events"),
        ):
            store = build_event_store(ServerConfig())

        assert store is not None
        assert (tmp_path / "events").is_dir()

    def test_empty_string_uses_file_backend(self, tmp_path):
        """Empty event_store_url defaults to a file-backed store at the package default dir."""
        from unittest.mock import patch

        with patch(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR",
            str(tmp_path / "events"),
        ):
            store = build_event_store(ServerConfig(event_store_url=""))

        assert store is not None
        assert (tmp_path / "events").is_dir()

    def test_file_url_creates_directory(self, tmp_path):
        """file:// URL creates the specified directory and uses a file backend."""
        target = tmp_path / "custom" / "events"
        store = build_event_store(ServerConfig(event_store_url=f"file://{target}"))

        assert store is not None
        assert target.is_dir()

    def test_memory_url_returns_in_memory_store(self):
        """memory:// URL returns an in-memory EventStore."""
        store = build_event_store(ServerConfig(event_store_url="memory://"))
        assert store is not None

    def test_kv_store_url_takes_priority(self, tmp_path):
        """kv_store_url is honoured over the legacy event_store_url."""
        ignored = tmp_path / "should-not-exist"
        store = build_event_store(
            ServerConfig(kv_store_url="memory://", event_store_url=f"file://{ignored}"),
        )

        assert store is not None
        # If event_store_url had been used, file:// would have created this dir.
        assert not ignored.exists()

    def test_unsupported_scheme_raises(self):
        """An unrecognised URL scheme raises ConfigurationError."""
        with pytest.raises(ConfigurationError):
            build_event_store(ServerConfig(event_store_url="bogus://host"))

    def test_uses_events_namespace(self):
        """The event store's collections live under the ``events`` prefix."""
        from key_value.aio.wrappers.prefix_collections import (
            PrefixCollectionsWrapper,
        )

        store = build_event_store(ServerConfig(kv_store_url="memory://"))
        storage = store._storage  # type: ignore[attr-defined]
        assert isinstance(storage, PrefixCollectionsWrapper)
        assert storage.prefix == "events"

    def test_unparseable_url_does_not_leak_credentials(self):
        """A URL ``urlparse`` rejects never reaches the message or traceback."""
        import traceback

        # A constant, not an inline literal: Python 3.13+ tracebacks quote
        # the call's source lines, which would match the secret themselves.
        with pytest.raises(ConfigurationError) as exc_info:
            build_event_store(ServerConfig(kv_store_url=_LEAKY_URL))
        exc = exc_info.value
        rendered = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        assert "hunter2" not in rendered
        assert "alice" not in rendered

    def test_file_url_without_path_raises(self):
        """The factory rejects a path-less file:// URL (use the file:///path form)."""
        with pytest.raises(ConfigurationError):
            build_event_store(ServerConfig(event_store_url="file://"))


class TestEventStoreConfig:
    """Tests for EVENT_STORE_URL config loading."""

    def test_event_store_url_default_none(self, monkeypatch):
        """Unset EVENT_STORE_URL yields None in config."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", raising=False)
        config = ProjectConfig.from_env()
        assert config.server.event_store_url is None

    def test_event_store_url_from_env(self, monkeypatch):
        """EVENT_STORE_URL is read from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "memory://")
        config = ProjectConfig.from_env()
        assert config.server.event_store_url == "memory://"

    def test_kv_store_url_from_env(self, monkeypatch):
        """KV_STORE_URL is read into config.server.kv_store_url (preferred over EVENT_STORE_URL)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_KV_STORE_URL", "memory://")
        config = ProjectConfig.from_env()
        assert config.server.kv_store_url == "memory://"

    def test_event_store_url_empty_is_none(self, monkeypatch):
        """Empty EVENT_STORE_URL yields None (file default)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "  ")
        config = ProjectConfig.from_env()
        assert config.server.event_store_url is None

    def test_event_store_url_file_path(self, monkeypatch):
        """file:// URL passed through verbatim."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "file:///data/state/events"
        )
        config = ProjectConfig.from_env()
        assert config.server.event_store_url == "file:///data/state/events"


class TestFileEventStorePersistence:
    """Integration test: events persist across EventStore recreations."""

    async def test_file_store_survives_restart(self, tmp_path):
        """Store an event, recreate the store, verify the event is retrievable."""
        store_dir = tmp_path / "events"
        url = f"file://{store_dir}"

        # First store instance — write an event (None message = priming event)
        store1 = build_event_store(ServerConfig(event_store_url=url))
        stream_id = "test-session-1"
        event_id = await store1.store_event(stream_id, None)
        assert event_id  # UUID string returned

        # Second store instance — same path, simulating restart
        store2 = build_event_store(ServerConfig(event_store_url=url))
        # Verify the event is retrievable by replaying after it
        replayed_events: list = []

        async def collect(event_id: str, data: dict | None) -> None:
            replayed_events.append((event_id, data))

        result_stream = await store2.replay_events_after(event_id, collect)
        # The stream should be found (returns stream_id, not None)
        assert result_stream == stream_id
