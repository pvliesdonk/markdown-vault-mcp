"""Tests for event store configuration and build_event_store()."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.config import load_config
from markdown_vault_mcp.mcp_server import build_event_store


class TestBuildEventStore:
    """Unit tests for build_event_store() URL parsing and backend selection."""

    def test_default_uses_file_backend(self, tmp_path):
        """Unset URL defaults to FileTreeStore."""
        from unittest.mock import patch

        with patch(
            "fastmcp_pvl_core._factory._DEFAULT_EVENT_STORE_DIR",
            str(tmp_path / "events"),
        ):
            store = build_event_store(None)

        assert store is not None
        assert (tmp_path / "events").is_dir()

    def test_empty_string_uses_file_backend(self, tmp_path):
        """Empty string URL defaults to FileTreeStore."""
        from unittest.mock import patch

        with patch(
            "fastmcp_pvl_core._factory._DEFAULT_EVENT_STORE_DIR",
            str(tmp_path / "events"),
        ):
            store = build_event_store("")

        assert store is not None
        assert (tmp_path / "events").is_dir()

    def test_file_url_creates_directory(self, tmp_path):
        """file:// URL creates specified directory and uses FileTreeStore."""
        target = tmp_path / "custom" / "events"
        store = build_event_store(f"file://{target}")

        assert store is not None
        assert target.is_dir()

    def test_memory_url_returns_in_memory_store(self):
        """memory:// URL returns an in-memory EventStore."""
        store = build_event_store("memory://")
        assert store is not None

    def test_unsupported_scheme_raises(self):
        """Unsupported URL scheme raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported event store URL scheme"):
            build_event_store("redis://localhost:6379")

    def test_file_url_without_path_uses_default(self, tmp_path):
        """file:// with no path falls back to default directory."""
        from unittest.mock import patch

        with patch(
            "fastmcp_pvl_core._factory._DEFAULT_EVENT_STORE_DIR",
            str(tmp_path / "events"),
        ):
            store = build_event_store("file://")

        assert store is not None
        assert (tmp_path / "events").is_dir()


class TestEventStoreConfig:
    """Tests for EVENT_STORE_URL config loading."""

    def test_event_store_url_default_none(self, monkeypatch):
        """Unset EVENT_STORE_URL yields None in config."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", raising=False)
        config = load_config()
        assert config.event_store_url is None

    def test_event_store_url_from_env(self, monkeypatch):
        """EVENT_STORE_URL is read from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "memory://")
        config = load_config()
        assert config.event_store_url == "memory://"

    def test_event_store_url_empty_is_none(self, monkeypatch):
        """Empty EVENT_STORE_URL yields None (file default)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "  ")
        config = load_config()
        assert config.event_store_url is None

    def test_event_store_url_file_path(self, monkeypatch):
        """file:// URL passed through verbatim."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EVENT_STORE_URL", "file:///data/state/events"
        )
        config = load_config()
        assert config.event_store_url == "file:///data/state/events"


class TestFileEventStorePersistence:
    """Integration test: events persist across EventStore recreations."""

    async def test_file_store_survives_restart(self, tmp_path):
        """Store an event, recreate the store, verify the event is retrievable."""
        store_dir = tmp_path / "events"
        url = f"file://{store_dir}"

        # First store instance — write an event (None message = priming event)
        store1 = build_event_store(url)
        stream_id = "test-session-1"
        event_id = await store1.store_event(stream_id, None)
        assert event_id  # UUID string returned

        # Second store instance — same path, simulating restart
        store2 = build_event_store(url)
        # Verify the event is retrievable by replaying after it
        replayed_events: list = []

        async def collect(event_id: str, data: dict | None) -> None:
            replayed_events.append((event_id, data))

        result_stream = await store2.replay_events_after(event_id, collect)
        # The stream should be found (returns stream_id, not None)
        assert result_stream == stream_id
