"""Tests for cli.py — argument parsing and subcommand dispatch."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp.cli import _build_parser, _cmd_serve, main

if TYPE_CHECKING:
    from pathlib import Path


class TestBuildParser:
    """Test argument parser construction."""

    def test_no_command_exits(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_serve_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.transport == "stdio"

    def test_serve_sse_transport(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve", "--transport", "sse"])
        assert args.transport == "sse"

    def test_serve_http_transport(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve", "--transport", "http"])
        assert args.transport == "http"
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.http_path is None

    def test_serve_http_custom_host_port(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "9000"]
        )
        assert args.transport == "http"
        assert args.host == "127.0.0.1"
        assert args.port == 9000

    def test_serve_http_custom_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--transport", "http", "--http-path", "/vault/mcp"]
        )
        assert args.transport == "http"
        assert args.http_path == "/vault/mcp"

    def test_serve_legacy_path_alias_still_works(self) -> None:
        """The legacy --path spelling is kept as an alias for --http-path.

        Protects existing Dockerfiles, systemd units, and service configs
        from the rename; the argparse dest remains ``http_path``.
        """
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--transport", "http", "--path", "/legacy/mcp"]
        )
        assert args.http_path == "/legacy/mcp"

    def test_index_command(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["index"])
        assert args.command == "index"
        assert args.force is False

    def test_index_force_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["index", "--force"])
        assert args.force is True

    def test_search_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["search", "hello world"])
        assert args.command == "search"
        assert args.query == "hello world"
        assert args.limit == 10
        assert args.mode == "keyword"
        assert args.folder is None
        assert args.json is False

    def test_search_all_options(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "search",
                "test query",
                "-n",
                "5",
                "-m",
                "hybrid",
                "--folder",
                "Journal",
                "--json",
            ]
        )
        assert args.query == "test query"
        assert args.limit == 5
        assert args.mode == "hybrid"
        assert args.folder == "Journal"
        assert args.json is True

    def test_index_source_dir_and_index_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["index", "--source-dir", "/data/vault", "--index-path", "/data/idx.db"]
        )
        assert args.source_dir == "/data/vault"
        assert args.index_path == "/data/idx.db"

    def test_search_source_dir(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["search", "query", "--source-dir", "/data/vault"])
        assert args.source_dir == "/data/vault"

    def test_reindex_command(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reindex"])
        assert args.command == "reindex"

    def test_reindex_source_dir_and_index_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["reindex", "--source-dir", "/data/vault", "--index-path", "/data/idx.db"]
        )
        assert args.source_dir == "/data/vault"
        assert args.index_path == "/data/idx.db"

    def test_verbose_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["-v", "index"])
        assert args.verbose is True


class TestMainDispatch:
    """Test main() dispatches to the correct subcommand handler."""

    def test_no_command_exits(self) -> None:
        with (
            patch("sys.argv", ["markdown-vault-mcp"]),
            pytest.raises(SystemExit, match="2"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_index_dispatch(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()
        mock_commands.__getitem__.assert_called_once_with("index")
        mock_handler.assert_called_once()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_valueerror_exits_with_message(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock(side_effect=ValueError("SOURCE_DIR not set"))
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with (
            patch("sys.argv", ["markdown-vault-mcp", "index"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_serve_dispatch(self, mock_commands: MagicMock) -> None:
        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with patch("sys.argv", ["markdown-vault-mcp", "serve"]):
            main()
        mock_commands.__getitem__.assert_called_once_with("serve")
        mock_handler.assert_called_once()

    @patch("markdown_vault_mcp.cli._COMMANDS")
    @patch("markdown_vault_mcp.cli.configure_logging_from_env")
    def test_verbose_enables_debug_for_both_logger_trees(
        self, mock_configure: MagicMock, mock_commands: MagicMock
    ) -> None:
        """``-v`` routes to configure_logging_from_env and silences httpx/httpcore."""
        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        with patch("sys.argv", ["markdown-vault-mcp", "-v", "index"]):
            main()
        mock_configure.assert_called_once_with(verbose=True)
        import logging

        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_verbose_sets_fastmcp_log_level_env(self, mock_commands: MagicMock) -> None:
        """``-v`` sets FASTMCP_LOG_LEVEL=DEBUG via the real configure_logging_from_env.

        Intentionally exercises the real helper (not mocked) so it covers the
        env-var contract MV depends on.  Saves and restores the root logger's
        level + handlers and FASTMCP_LOG_LEVEL to keep global state clean.
        """
        import logging
        import os

        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        root = logging.getLogger()
        saved_env = os.environ.pop("FASTMCP_LOG_LEVEL", None)
        saved_level = root.level
        saved_handlers = root.handlers[:]
        try:
            with patch("sys.argv", ["markdown-vault-mcp", "-v", "index"]):
                main()
            assert os.environ.get("FASTMCP_LOG_LEVEL") == "DEBUG"
        finally:
            if saved_env is not None:
                os.environ["FASTMCP_LOG_LEVEL"] = saved_env
            else:
                os.environ.pop("FASTMCP_LOG_LEVEL", None)
            root.setLevel(saved_level)
            root.handlers[:] = saved_handlers

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_no_verbose_does_not_set_fastmcp_log_level(
        self, mock_commands: MagicMock
    ) -> None:
        """Without ``-v``, FASTMCP_LOG_LEVEL is not touched."""
        import os

        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        old = os.environ.pop("FASTMCP_LOG_LEVEL", None)
        try:
            with patch("sys.argv", ["markdown-vault-mcp", "index"]):
                main()
            assert os.environ.get("FASTMCP_LOG_LEVEL") is None
        finally:
            if old is not None:
                os.environ["FASTMCP_LOG_LEVEL"] = old
            else:
                os.environ.pop("FASTMCP_LOG_LEVEL", None)

    @patch("markdown_vault_mcp.cli._COMMANDS")
    def test_root_handler_added_when_none_exist(self, mock_commands: MagicMock) -> None:
        """A StreamHandler is added to root when it has no handlers."""
        import logging

        mock_handler = MagicMock()
        mock_commands.__getitem__ = MagicMock(return_value=mock_handler)
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            with patch("sys.argv", ["markdown-vault-mcp", "index"]):
                main()
            assert len(root.handlers) >= 1
        finally:
            root.handlers[:] = original_handlers


class TestCmdServe:
    """Test the serve subcommand dispatch."""

    @patch("uvicorn.run")
    @patch("markdown_vault_mcp.mcp_server.build_event_store")
    @patch("markdown_vault_mcp.cli.load_config")
    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_calls_http_app_and_uvicorn(
        self,
        mock_create: MagicMock,
        mock_load_config: MagicMock,
        mock_build_es: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        """_cmd_serve builds ASGI app via http_app() and runs uvicorn for http."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mock_config = MagicMock()
        mock_config.event_store_url = None
        mock_load_config.return_value = mock_config
        mock_event_store = MagicMock()
        mock_build_es.return_value = mock_event_store
        mock_app = MagicMock()
        mock_server.http_app.return_value = mock_app

        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--host", "127.0.0.1", "--port", "9000"]
        )
        _cmd_serve(args)

        mock_server.http_app.assert_called_once_with(
            path="/mcp",
            transport="http",
            event_store=mock_event_store,
        )
        mock_uvicorn_run.assert_called_once_with(
            mock_app,
            host="127.0.0.1",
            port=9000,
            timeout_graceful_shutdown=3,
            lifespan="on",
        )

    @patch("uvicorn.run")
    @patch("markdown_vault_mcp.mcp_server.build_event_store")
    @patch("markdown_vault_mcp.cli.load_config")
    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_custom_path(
        self,
        mock_create: MagicMock,
        mock_load_config: MagicMock,
        mock_build_es: MagicMock,
        _mock_uvicorn_run: MagicMock,
    ) -> None:
        """_cmd_serve passes custom --http-path to http_app()."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mock_config = MagicMock()
        mock_config.event_store_url = None
        mock_load_config.return_value = mock_config
        mock_build_es.return_value = MagicMock()
        mock_server.http_app.return_value = MagicMock()

        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--http-path", "/vault/mcp"]
        )
        _cmd_serve(args)

        mock_server.http_app.assert_called_once()
        call_kwargs = mock_server.http_app.call_args[1]
        assert call_kwargs["path"] == "/vault/mcp"

    @patch("uvicorn.run")
    @patch("markdown_vault_mcp.mcp_server.build_event_store")
    @patch("markdown_vault_mcp.cli.load_config")
    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_custom_path_normalised(
        self,
        mock_create: MagicMock,
        mock_load_config: MagicMock,
        mock_build_es: MagicMock,
        _mock_uvicorn_run: MagicMock,
    ) -> None:
        """_cmd_serve normalises --http-path by adding leading slash and trimming tail."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mock_config = MagicMock()
        mock_config.event_store_url = None
        mock_load_config.return_value = mock_config
        mock_build_es.return_value = MagicMock()
        mock_server.http_app.return_value = MagicMock()

        args = _build_parser().parse_args(
            ["serve", "--transport", "http", "--http-path", "vault/mcp/"]
        )
        _cmd_serve(args)

        mock_server.http_app.assert_called_once()
        call_kwargs = mock_server.http_app.call_args[1]
        assert call_kwargs["path"] == "/vault/mcp"

    @patch("uvicorn.run")
    @patch("markdown_vault_mcp.mcp_server.build_event_store")
    @patch("markdown_vault_mcp.cli.load_config")
    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_path_env_fallback(
        self,
        mock_create: MagicMock,
        mock_load_config: MagicMock,
        mock_build_es: MagicMock,
        _mock_uvicorn_run: MagicMock,
    ) -> None:
        """_cmd_serve uses MARKDOWN_VAULT_MCP_HTTP_PATH when --http-path is omitted."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mock_config = MagicMock()
        mock_config.event_store_url = None
        mock_load_config.return_value = mock_config
        mock_build_es.return_value = MagicMock()
        mock_server.http_app.return_value = MagicMock()

        with patch.dict("os.environ", {"MARKDOWN_VAULT_MCP_HTTP_PATH": "/vault/mcp"}):
            args = _build_parser().parse_args(["serve", "--transport", "http"])
            _cmd_serve(args)

        mock_server.http_app.assert_called_once()
        call_kwargs = mock_server.http_app.call_args[1]
        assert call_kwargs["path"] == "/vault/mcp"

    @patch("uvicorn.run")
    @patch("markdown_vault_mcp.mcp_server.build_event_store")
    @patch("markdown_vault_mcp.cli.load_config")
    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_http_path_cli_overrides_env(
        self,
        mock_create: MagicMock,
        mock_load_config: MagicMock,
        mock_build_es: MagicMock,
        _mock_uvicorn_run: MagicMock,
    ) -> None:
        """_cmd_serve --http-path takes precedence over MARKDOWN_VAULT_MCP_HTTP_PATH."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mock_config = MagicMock()
        mock_config.event_store_url = None
        mock_load_config.return_value = mock_config
        mock_build_es.return_value = MagicMock()
        mock_server.http_app.return_value = MagicMock()

        with patch.dict("os.environ", {"MARKDOWN_VAULT_MCP_HTTP_PATH": "/from-env"}):
            args = _build_parser().parse_args(
                ["serve", "--transport", "http", "--http-path", "/from-cli"]
            )
            _cmd_serve(args)

        mock_server.http_app.assert_called_once()
        call_kwargs = mock_server.http_app.call_args[1]
        assert call_kwargs["path"] == "/from-cli"

    @patch("markdown_vault_mcp.mcp_server.create_server")
    def test_serve_stdio_does_not_pass_host_port(self, mock_create: MagicMock) -> None:
        """_cmd_serve does not pass host/port for stdio transport."""
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        args = _build_parser().parse_args(["serve"])
        _cmd_serve(args)
        mock_server.run.assert_called_once_with(transport="stdio")


class TestCmdIndex:
    """Test the index subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_prints_stats(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 42
        mock_stats.chunks_indexed = 128
        mock_collection.build_index.return_value = mock_stats
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()

        mock_collection.build_index.assert_called_once_with(force=False)
        captured = capsys.readouterr()
        assert "42 documents" in captured.out
        assert "128 chunks" in captured.out
        mock_collection.build_index.assert_called_once_with(force=False)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_valueerror_exits_with_message(
        self,
        mock_build: MagicMock,
    ) -> None:
        mock_build.side_effect = ValueError("MARKDOWN_VAULT_MCP_SOURCE_DIR is required")

        with (
            patch("sys.argv", ["markdown-vault-mcp", "index"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_force_propagates(
        self,
        mock_build: MagicMock,
    ) -> None:
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 10
        mock_stats.chunks_indexed = 30
        mock_collection.build_index.return_value = mock_stats
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index", "--force"]):
            main()

        mock_collection.build_index.assert_called_once_with(force=True)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_builds_embeddings_when_configured(
        self,
        mock_build: MagicMock,
    ) -> None:
        """index command calls build_embeddings() after build_index() when configured."""
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 5
        mock_stats.chunks_indexed = 20
        mock_collection.build_index.return_value = mock_stats
        mock_collection.build_embeddings.return_value = 20
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()

        mock_collection.build_embeddings.assert_called_once_with(force=False)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_skips_embeddings_when_not_configured(
        self,
        mock_build: MagicMock,
    ) -> None:
        """index command does not fail when embeddings are not configured."""
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 5
        mock_stats.chunks_indexed = 20
        mock_collection.build_index.return_value = mock_stats
        mock_collection.build_embeddings.side_effect = ValueError("not configured")
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index"]):
            main()  # must not raise

        mock_collection.build_embeddings.assert_called_once()

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_index_force_propagates_to_embeddings(
        self,
        mock_build: MagicMock,
    ) -> None:
        """--force flag is passed through to build_embeddings."""
        mock_collection = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 5
        mock_stats.chunks_indexed = 20
        mock_collection.build_index.return_value = mock_stats
        mock_collection.build_embeddings.return_value = 20
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "index", "--force"]):
            main()

        mock_collection.build_embeddings.assert_called_once_with(force=True)


class TestCmdSearch:
    """Test the search subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_text_output(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_result = MagicMock()
        mock_result.path = "notes/test.md"
        mock_result.title = "Test Note"
        mock_result.score = 0.9876

        mock_collection = MagicMock()
        mock_collection.search.return_value = [mock_result]
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "test"]):
            main()

        captured = capsys.readouterr()
        assert "notes/test.md" in captured.out
        assert "0.9876" in captured.out
        assert "Test Note" in captured.out

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_json_output(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from markdown_vault_mcp.types import SearchResult

        result = SearchResult(
            path="a.md",
            title="Note A",
            folder="",
            heading=None,
            content="hello",
            score=1.0,
            search_type="keyword",
            frontmatter={},
        )
        mock_collection = MagicMock()
        mock_collection.search.return_value = [result]
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "test", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["path"] == "a.md"
        assert data[0]["score"] == 1.0

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_search_passes_options(self, mock_build: MagicMock) -> None:
        mock_collection = MagicMock()
        mock_collection.search.return_value = []
        mock_build.return_value = mock_collection

        with patch(
            "sys.argv",
            [
                "markdown-vault-mcp",
                "search",
                "query",
                "-n",
                "5",
                "-m",
                "semantic",
                "--folder",
                "Journal",
            ],
        ):
            main()

        mock_collection.search.assert_called_once_with(
            "query", limit=5, mode="semantic", folder="Journal"
        )


class TestCmdServeEdgeCases:
    """Edge-case branches in _cmd_serve."""

    def test_import_error_exits_with_1(self) -> None:
        """_cmd_serve calls sys.exit(1) when FastMCP import fails."""
        import sys

        args = _build_parser().parse_args(["serve"])

        with (
            patch.dict(sys.modules, {"markdown_vault_mcp.mcp_server": None}),
            pytest.raises(SystemExit) as exc_info,
        ):
            _cmd_serve(args)

        assert exc_info.value.code == 1

    def test_non_http_transport_with_custom_host_port_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """--host/--port with non-http transport logs a warning."""
        import logging

        mock_server = MagicMock()

        args = _build_parser().parse_args(
            ["serve", "--transport", "stdio", "--host", "127.0.0.1", "--port", "9999"]
        )

        with (
            patch(
                "markdown_vault_mcp.mcp_server.create_server", return_value=mock_server
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.cli"),
        ):
            _cmd_serve(args)

        assert any(
            "--host" in r.message or "--port" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        mock_server.run.assert_called_once_with(transport="stdio")


class TestBuildCollectionEmbeddingFailure:
    """Graceful degradation when the embedding provider fails to load."""

    def test_embedding_provider_failure_returns_collection(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Collection is still returned even when get_embedding_provider raises."""
        import logging

        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        # Set an embeddings_path so the try-block is entered.
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(tmp_path / "vecs.npy")
        )

        args = _build_parser().parse_args(["index"])

        with (
            patch(
                "markdown_vault_mcp.providers.get_embedding_provider",
                side_effect=RuntimeError("no embedding provider"),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.cli"),
        ):
            collection = _build_collection(args)

        # Collection is returned despite the provider failure.
        from markdown_vault_mcp.collection import Collection

        assert isinstance(collection, Collection)
        assert any("semantic search disabled" in r.message for r in caplog.records)


class TestBuildCollectionConfigFields:
    """`_build_collection` must propagate every field `CollectionConfig.to_collection_kwargs` produces.

    Regression tests for a bug where the CLI path hardcoded a subset of kwargs
    (``source_dir``, ``read_only``, ``index_path``, ``embeddings_path``,
    ``embedding_provider``, ``state_path``, ``indexed_frontmatter_fields``,
    ``required_frontmatter``) and silently dropped the rest — including
    ``exclude_patterns``, ``attachment_extensions``, and
    ``max_attachment_size_mb``. All CLI subcommands (``index``, ``reindex``,
    ``search``) that route through ``_build_collection`` were affected, so
    ``MARKDOWN_VAULT_MCP_EXCLUDE`` was silently ignored on the CLI side even
    though the serve path via ``_server_deps.make_collection_lifespan`` honored
    it correctly.
    """

    def test_exclude_patterns_propagated_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``MARKDOWN_VAULT_MCP_EXCLUDE`` reaches ``Collection._exclude_patterns``."""
        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "**/*.log.md,.obsidian/**")

        args = _build_parser().parse_args(["index"])
        collection = _build_collection(args)

        assert collection._exclude_patterns == ["**/*.log.md", ".obsidian/**"]

    def test_attachment_fields_propagated_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``MARKDOWN_VAULT_MCP_ATTACHMENT_*`` env vars reach the Collection."""
        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png,jpg")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "25")

        args = _build_parser().parse_args(["index"])
        collection = _build_collection(args)

        assert collection._attachment_extensions == ["pdf", "png", "jpg"]
        assert collection._max_attachment_size_mb == 25.0

    def test_exclude_patterns_are_functional_via_cli_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Behavioural regression: Collection built via the CLI actually excludes.

        The bug was that ``_build_collection`` constructed the Collection
        without ``exclude_patterns``, so
        :meth:`~markdown_vault_mcp.collection.Collection._is_path_excluded`
        always returned ``False`` — because ``self._exclude_patterns`` was
        ``None``. Assert the exclusion logic is live end-to-end.
        """
        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "**/*.log.md,.obsidian/**")

        args = _build_parser().parse_args(["index"])
        collection = _build_collection(args)

        # These should be excluded via the newly-propagated patterns.
        assert (
            collection._doc_mgr._is_path_excluded("sessions/2026-04-09/chat.log.md")
            is True
        )
        assert (
            collection._doc_mgr._is_path_excluded(".obsidian/workspace.json.md") is True
        )

        # And these should still pass through unaffected.
        assert collection._doc_mgr._is_path_excluded("notes/alpha.md") is False
        assert (
            collection._doc_mgr._is_path_excluded("decisions/2026-04-09-auth.md")
            is False
        )

    def test_index_path_override_propagated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--index-path`` CLI override reaches the Collection."""
        from markdown_vault_mcp.cli import _build_collection

        vault = tmp_path / "vault"
        vault.mkdir()
        custom_index = tmp_path / "custom.sqlite"

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))

        args = _build_parser().parse_args(["index", "--index-path", str(custom_index)])
        collection = _build_collection(args)

        assert collection._index_path == custom_index


class TestCmdSearchJsonOutput:
    """Verify --json flag in _cmd_search produces valid parseable output."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_json_flag_produces_valid_json(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_search with --json outputs a valid JSON array."""
        from markdown_vault_mcp.types import SearchResult

        results = [
            SearchResult(
                path="notes/alpha.md",
                title="Alpha",
                folder="notes",
                heading=None,
                content="some text",
                score=0.75,
                search_type="keyword",
                frontmatter={},
            ),
            SearchResult(
                path="notes/beta.md",
                title="Beta",
                folder="notes",
                heading="Section",
                content="more text",
                score=0.55,
                search_type="keyword",
                frontmatter={"tag": "x"},
            ),
        ]
        mock_collection = MagicMock()
        mock_collection.search.return_value = results
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "alpha", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["path"] == "notes/alpha.md"
        assert data[0]["score"] == 0.75
        assert data[1]["path"] == "notes/beta.md"

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_json_flag_empty_results(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_search with --json and no results outputs an empty JSON array."""
        mock_collection = MagicMock()
        mock_collection.search.return_value = []
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "search", "nothing", "--json"]):
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == []


class TestCmdReindex:
    """Test the reindex subcommand."""

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_prints_stats(
        self,
        mock_build: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_result = MagicMock()
        mock_result.added = 3
        mock_result.modified = 1
        mock_result.deleted = 2
        mock_result.unchanged = 10

        mock_collection = MagicMock()
        mock_collection.reindex.return_value = mock_result
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()

        captured = capsys.readouterr()
        assert "3 added" in captured.out
        assert "1 modified" in captured.out
        assert "2 deleted" in captured.out
        assert "10 unchanged" in captured.out

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_builds_embeddings_when_configured(
        self,
        mock_build: MagicMock,
    ) -> None:
        """reindex command calls build_embeddings(force=True) when configured."""
        mock_collection = MagicMock()
        mock_result = MagicMock()
        mock_result.added = 1
        mock_result.modified = 0
        mock_result.deleted = 0
        mock_result.unchanged = 5
        mock_collection.reindex.return_value = mock_result
        mock_collection.build_embeddings.return_value = 10
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()

        mock_collection.build_embeddings.assert_called_once_with(force=True)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_skips_embeddings_when_not_configured(
        self,
        mock_build: MagicMock,
    ) -> None:
        """reindex command does not fail when embeddings are not configured."""
        mock_collection = MagicMock()
        mock_result = MagicMock()
        mock_result.added = 0
        mock_result.modified = 0
        mock_result.deleted = 0
        mock_result.unchanged = 5
        mock_collection.reindex.return_value = mock_result
        mock_collection.build_embeddings.side_effect = ValueError("not configured")
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()  # must not raise

        mock_collection.build_embeddings.assert_called_once()

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_uses_force_false_when_no_changes(
        self,
        mock_build: MagicMock,
    ) -> None:
        """reindex skips force rebuild when FTS found no changes."""
        mock_collection = MagicMock()
        mock_result = MagicMock()
        mock_result.added = 0
        mock_result.modified = 0
        mock_result.deleted = 0
        mock_result.unchanged = 10
        mock_collection.reindex.return_value = mock_result
        mock_collection.build_embeddings.return_value = 0
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()

        mock_collection.build_embeddings.assert_called_once_with(force=False)

    @patch("markdown_vault_mcp.cli._build_collection")
    def test_reindex_uses_force_true_when_changes_exist(
        self,
        mock_build: MagicMock,
    ) -> None:
        """reindex forces rebuild when FTS found added/modified/deleted files."""
        mock_collection = MagicMock()
        mock_result = MagicMock()
        mock_result.added = 2
        mock_result.modified = 1
        mock_result.deleted = 0
        mock_result.unchanged = 10
        mock_collection.reindex.return_value = mock_result
        mock_collection.build_embeddings.return_value = 30
        mock_build.return_value = mock_collection

        with patch("sys.argv", ["markdown-vault-mcp", "reindex"]):
            main()

        mock_collection.build_embeddings.assert_called_once_with(force=True)
