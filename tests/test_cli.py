"""Tests for cli.py — typer app, subcommand dispatch, and domain regressions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from markdown_vault_mcp.cli import _ENV_PREFIX, _build_vault, app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from typer.testing import Result

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help / structure
# ---------------------------------------------------------------------------


def test_help_lists_all_subcommands() -> None:
    """`--help` lists all four subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("serve", "index", "search", "reindex"):
        assert cmd in result.output


def test_no_args_exits_nonzero() -> None:
    """Bare invocation exits with code 2 (missing required COMMAND argument)."""
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "serve" in result.output


def test_quiet_http_loggers_pins_warning_at_default_visible_at_debug() -> None:
    """httpx/httpcore emit a per-request INFO line that floods logs during
    embedding builds (#792). ``quiet_http_loggers`` — called by the domain
    commands and by ``make_server``'s DOMAIN-WIRING, since the template-owned
    ``_root`` callback and ``make_server`` scaffold must stay byte-identical to
    the skeleton — pins them to WARNING when the root level is not DEBUG (quiet)
    and to NOTSET at ``-v`` (root DEBUG governs, visible)."""
    import logging

    from markdown_vault_mcp._http_logging import quiet_http_loggers

    root = logging.getLogger()
    httpx_log = logging.getLogger("httpx")
    httpcore_log = logging.getLogger("httpcore")
    saved = (root.level, httpx_log.level, httpcore_log.level)
    try:
        root.setLevel(logging.INFO)
        quiet_http_loggers()
        assert httpx_log.level == logging.WARNING
        assert httpcore_log.level == logging.WARNING

        root.setLevel(logging.DEBUG)
        quiet_http_loggers()
        assert httpx_log.level == logging.NOTSET
        assert httpcore_log.level == logging.NOTSET
    finally:
        root.setLevel(saved[0])
        httpx_log.setLevel(saved[1])
        httpcore_log.setLevel(saved[2])


def test_make_server_quiets_httpx_on_serve_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The serve path (CLI ``serve`` → ``make_server``) quiets httpx/httpcore too
    (#792) — ``make_server``'s DOMAIN-WIRING calls ``quiet_http_loggers`` so the
    background embedding build doesn't flood at the default level."""
    import logging

    from markdown_vault_mcp.server import make_server

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(tmp_path))
    root = logging.getLogger()
    httpx_log = logging.getLogger("httpx")
    httpcore_log = logging.getLogger("httpcore")
    saved = (root.level, httpx_log.level, httpcore_log.level)
    try:
        root.setLevel(logging.INFO)
        httpx_log.setLevel(logging.NOTSET)  # flood-prone default
        httpcore_log.setLevel(logging.NOTSET)
        make_server(transport="stdio")
        assert httpx_log.level == logging.WARNING
        assert httpcore_log.level == logging.WARNING
    finally:
        root.setLevel(saved[0])
        httpx_log.setLevel(saved[1])
        httpcore_log.setLevel(saved[2])


def test_serve_help_exits_zero() -> None:
    """`serve --help` documents the transport flag."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "stdio" in result.output


# NOTE: these are smoke tests — they assert only that ``<cmd> --help`` exits 0
# (the command is registered and its typer.Options/Arguments are well-formed).
# We do NOT assert on the rendered help text: typer renders help via rich, whose
# wrapping/colour is terminal-width-dependent, so a long option name like
# ``--source-dir`` wraps inside the help box at CI's 80 columns. Option/argument
# behaviour is covered by the functional tests below.
def test_index_help_exits_zero() -> None:
    assert runner.invoke(app, ["index", "--help"]).exit_code == 0


def test_search_help_exits_zero() -> None:
    assert runner.invoke(app, ["search", "--help"]).exit_code == 0


def test_reindex_help_exits_zero() -> None:
    assert runner.invoke(app, ["reindex", "--help"]).exit_code == 0


# ---------------------------------------------------------------------------
# serve — patch targets:
#   make_server  → markdown_vault_mcp.server.make_server  (function-local import)
#   build_event_store → markdown_vault_mcp.cli.build_event_store (top-level import)
#   uvicorn.run  → uvicorn.run  (imported inside serve as `import uvicorn`)
# ---------------------------------------------------------------------------


def _invoke_http_serve(
    extra_args: list[str] | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> tuple[Result, dict[str, object]]:
    """Invoke ``serve --transport http`` with all side effects patched.

    Returns the CliRunner result and the kwargs captured by the fake uvicorn.run.
    ``host`` and ``port`` are wired into the mock config so tests that rely on
    env-var fallbacks see the expected values without needing SOURCE_DIR set.
    """
    captured: dict[str, object] = {}

    def fake_uvicorn_run(_asgi_app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()

    mock_config = MagicMock()
    mock_config.server.host = host
    mock_config.server.port = port

    with (
        patch("uvicorn.run", side_effect=fake_uvicorn_run),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = mock_config
        args = ["serve", "--transport", "http"] + (extra_args or [])
        result = runner.invoke(app, args)

    return result, captured


def test_serve_http_runs_uvicorn() -> None:
    """``serve --transport http`` calls uvicorn.run."""
    result, captured = _invoke_http_serve(["--host", "127.0.0.1", "--port", "9001"])
    assert result.exit_code == 0, result.output
    assert captured.get("port") == 9001
    assert captured.get("host") == "127.0.0.1"


def test_serve_http_reads_host_port_from_env() -> None:
    """``serve --transport http`` uses HOST/PORT from env when no CLI flags given.

    The serve command falls back to ``config.server.host/port`` when no CLI flags
    are passed.  We wire those values into the mock config so the env-var path
    (ProjectConfig.from_env reading MARKDOWN_VAULT_MCP_HOST/PORT) is exercised
    without needing SOURCE_DIR to be set.
    """
    # The mock_config in _invoke_http_serve is what from_env() returns.
    # Pass the expected host/port so mock_config.server.host/port carry them.
    result, captured = _invoke_http_serve(host="192.168.1.50", port=9090)
    assert result.exit_code == 0, result.output
    assert captured.get("host") == "192.168.1.50"
    assert captured.get("port") == 9090


def test_serve_http_cli_flags_override_env() -> None:
    """Explicit ``--host``/``--port`` CLI flags override env-var defaults.

    When CLI flags are given they take precedence over config.server.host/port.
    The mock config carries a different host/port to prove the CLI wins.
    """
    result, captured = _invoke_http_serve(
        ["--host", "10.0.0.1", "--port", "7777"],
        host="192.168.1.50",
        port=9090,
    )
    assert result.exit_code == 0, result.output
    assert captured.get("host") == "10.0.0.1"
    assert captured.get("port") == 7777


def _fake_config(host: str = "127.0.0.1", port: int = 8000) -> MagicMock:
    """Return a mock ProjectConfig with server.host/port set."""
    cfg = MagicMock()
    cfg.server.host = host
    cfg.server.port = port
    return cfg


def test_serve_http_default_path_is_mcp() -> None:
    """``serve --transport http`` defaults to /mcp when no path flags/env set."""
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(app, ["serve", "--transport", "http"])
    assert result.exit_code == 0, result.output
    call_kwargs = fake_server.http_app.call_args[1]
    assert call_kwargs["path"] == "/mcp"
    # serve is the template's verbatim and calls http_app WITHOUT transport=
    # (FastMCP's http_app defaults to streamable-http). Pin that template-driven
    # contract so a future merge that re-adds transport= is caught here.
    assert "transport" not in call_kwargs


def test_serve_http_custom_path() -> None:
    """``--http-path`` is passed through normalised to http_app()."""
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(
            app, ["serve", "--transport", "http", "--http-path", "/vault/mcp"]
        )
    assert result.exit_code == 0, result.output
    call_kwargs = fake_server.http_app.call_args[1]
    assert call_kwargs["path"] == "/vault/mcp"


def test_serve_http_path_normalised() -> None:
    """``serve`` normalises --http-path (adds leading slash, trims trailing)."""
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(
            app, ["serve", "--transport", "http", "--http-path", "vault/mcp/"]
        )
    assert result.exit_code == 0, result.output
    call_kwargs = fake_server.http_app.call_args[1]
    assert call_kwargs["path"] == "/vault/mcp"


def test_serve_http_path_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``serve`` uses MARKDOWN_VAULT_MCP_HTTP_PATH when --http-path is omitted."""
    monkeypatch.setenv(f"{_ENV_PREFIX}_HTTP_PATH", "/vault/mcp")
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(app, ["serve", "--transport", "http"])
    assert result.exit_code == 0, result.output
    call_kwargs = fake_server.http_app.call_args[1]
    assert call_kwargs["path"] == "/vault/mcp"


def test_serve_http_path_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI --http-path takes precedence over MARKDOWN_VAULT_MCP_HTTP_PATH."""
    monkeypatch.setenv(f"{_ENV_PREFIX}_HTTP_PATH", "/from-env")
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(
            app, ["serve", "--transport", "http", "--http-path", "/from-cli"]
        )
    assert result.exit_code == 0, result.output
    call_kwargs = fake_server.http_app.call_args[1]
    assert call_kwargs["path"] == "/from-cli"


def test_serve_stdio_runs_server_run() -> None:
    """``serve`` (default stdio) calls server.run(transport='stdio')."""
    fake_server = MagicMock()
    with (
        patch("markdown_vault_mcp.server.make_server", return_value=fake_server),
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg_cls,
    ):
        mock_cfg_cls.from_env.return_value = _fake_config()
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    fake_server.run.assert_called_once_with(transport="stdio")


def test_serve_http_reads_config_once() -> None:
    """#609: the HTTP path reads config once and passes it to both make_server
    and build_event_store rather than parsing env twice."""
    fake_server = MagicMock()
    fake_server.http_app.return_value = MagicMock()
    with (
        patch("uvicorn.run"),
        patch(
            "markdown_vault_mcp.server.make_server", return_value=fake_server
        ) as mock_ms,
        patch(
            "markdown_vault_mcp.cli.build_event_store", return_value=MagicMock()
        ) as mock_bes,
        patch("markdown_vault_mcp.cli.ProjectConfig") as mock_cfg,
    ):
        mock_config = _fake_config()
        mock_cfg.from_env.return_value = mock_config
        result = runner.invoke(app, ["serve", "--transport", "http"])
    assert result.exit_code == 0, result.output
    mock_cfg.from_env.assert_called_once()
    assert mock_ms.call_args.kwargs.get("config") is mock_config
    mock_bes.assert_called_once_with(_ENV_PREFIX, mock_config.server)


def test_serve_http_lifespan_and_graceful_shutdown() -> None:
    """``serve --transport http`` passes lifespan='on' and timeout_graceful_shutdown=3."""
    result, captured = _invoke_http_serve()
    assert result.exit_code == 0, result.output
    assert captured.get("lifespan") == "on"
    assert captured.get("timeout_graceful_shutdown") == 3


@patch("markdown_vault_mcp.server.make_server")
@patch("markdown_vault_mcp.cli.ProjectConfig")
def test_serve_stdio_passes_config_to_make_server(
    mock_cfg: MagicMock, mock_make_server: MagicMock
) -> None:
    """stdio serve reads config once and hands it to make_server (no double read)."""
    mock_config = mock_cfg.from_env.return_value
    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code == 0, result.output
    mock_cfg.from_env.assert_called_once()
    assert mock_make_server.call_args.kwargs.get("config") is mock_config
    mock_make_server.return_value.run.assert_called_once_with(transport="stdio")


def test_serve_legacy_path_alias_accepted() -> None:
    """``--path`` alias for ``--http-path`` still works (kept for Dockerfiles/units, #401)."""
    result, _captured = _invoke_http_serve(["--path", "/legacy/mcp"])
    assert result.exit_code == 0, result.output


def test_build_vault_valueerror_exits_nonzero() -> None:
    """A command whose _build_vault raises ValueError exits non-zero (shell $? contract)."""
    with patch(
        "markdown_vault_mcp.cli._build_vault",
        side_effect=ValueError("SOURCE_DIR not set"),
    ):
        result = runner.invoke(app, ["index"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# _build_vault field propagation (domain regressions)
# ---------------------------------------------------------------------------


def test_build_vault_exclude_patterns_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MARKDOWN_VAULT_MCP_EXCLUDE reaches Vault._exclude_patterns."""
    from markdown_vault_mcp.vault import Vault

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv(f"{_ENV_PREFIX}_EXCLUDE", "**/*.log.md,.obsidian/**")

    result = _build_vault()
    assert isinstance(result, Vault)
    # Configured patterns plus the derived folder-conventions excludes.
    assert result._exclude_patterns == [
        "**/*.log.md",
        ".obsidian/**",
        "_conventions.md",
        "**/_conventions.md",
    ]


def test_build_vault_attachment_fields_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MARKDOWN_VAULT_MCP_ATTACHMENT_* env vars reach the Vault."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv(f"{_ENV_PREFIX}_ATTACHMENT_EXTENSIONS", "pdf,png,jpg")
    monkeypatch.setenv(f"{_ENV_PREFIX}_MAX_ATTACHMENT_SIZE_MB", "25")

    result = _build_vault()
    assert result._attachment_extensions == ("pdf", "png", "jpg")
    assert result._max_attachment_size_mb == 25.0


def test_build_vault_index_path_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``index_path`` kwarg override reaches the Vault._index_path."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    custom_index = tmp_path / "custom.sqlite"
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(vault_dir))

    result = _build_vault(index_path=str(custom_index))
    assert result._index_path == custom_index


def test_build_vault_exclude_patterns_are_functional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural regression: Vault built via _build_vault actually excludes.

    Previously the CLI path dropped exclude_patterns so
    Vault._is_path_excluded always returned False.
    """
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv(f"{_ENV_PREFIX}_EXCLUDE", "**/*.log.md,.obsidian/**")

    vault = _build_vault()
    assert vault._doc_mgr._is_path_excluded("sessions/2026-04-09/chat.log.md") is True
    assert vault._doc_mgr._is_path_excluded(".obsidian/workspace.json.md") is True
    assert vault._doc_mgr._is_path_excluded("notes/alpha.md") is False
    assert vault._doc_mgr._is_path_excluded("decisions/2026-04-09-auth.md") is False


# ---------------------------------------------------------------------------
# index command
# ---------------------------------------------------------------------------


def test_index_prints_stats() -> None:
    """``index`` prints indexed documents and chunks."""
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 42
    mock_stats.chunks_indexed = 128
    mock_vault.index.build_index.return_value = mock_stats

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["index"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_index.assert_called_once_with(force=False)
    assert "42 documents" in result.output
    assert "128 chunks" in result.output


def test_index_force_propagates() -> None:
    """``--force`` is forwarded to build_index and build_embeddings."""
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 10
    mock_stats.chunks_indexed = 30
    mock_vault.index.build_index.return_value = mock_stats
    mock_vault.index.build_embeddings.return_value = 30

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["index", "--force"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_index.assert_called_once_with(force=True)
    mock_vault.index.build_embeddings.assert_called_once_with(force=True)


def test_index_builds_embeddings_when_configured() -> None:
    """``index`` calls build_embeddings() after build_index() when configured."""
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 5
    mock_stats.chunks_indexed = 20
    mock_vault.index.build_index.return_value = mock_stats
    mock_vault.index.build_embeddings.return_value = 20

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["index"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_embeddings.assert_called_once_with(force=False)
    assert "20 chunks" in result.output


def test_index_swallows_embeddings_not_configured_error() -> None:
    """Embedding graceful-degradation: EmbeddingsNotConfiguredError exits 0 (#774)."""
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 5
    mock_stats.chunks_indexed = 20
    mock_vault.index.build_index.return_value = mock_stats
    mock_vault.index.build_embeddings.side_effect = EmbeddingsNotConfiguredError(
        "not configured"
    )

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["index"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_embeddings.assert_called_once()
    assert "5 documents" in result.output


def test_index_surfaces_internal_valueerror_from_embeddings() -> None:
    """An internal ValueError (not EmbeddingsNotConfiguredError) surfaces (#774)."""
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 5
    mock_stats.chunks_indexed = 20
    mock_vault.index.build_index.return_value = mock_stats
    mock_vault.index.build_embeddings.side_effect = ValueError(
        "Failed to load vector index after _load_vectors()"
    )

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["index"])

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


def test_search_text_output() -> None:
    """``search`` prints path, score, and title in plain mode."""
    mock_result = MagicMock()
    mock_result.path = "notes/test.md"
    mock_result.title = "Test Note"
    mock_result.score = 0.9876

    mock_vault = MagicMock()
    mock_vault.reader.search.return_value = [mock_result]

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["search", "test"])

    assert result.exit_code == 0, result.output
    assert "notes/test.md" in result.output
    assert "0.9876" in result.output
    assert "Test Note" in result.output


def test_search_json_output() -> None:
    """``search --json`` emits a valid JSON array."""
    from markdown_vault_mcp.types import SearchResult

    sr = SearchResult(
        path="a.md",
        title="Note A",
        folder="",
        heading=None,
        content="hello",
        score=1.0,
        search_type="keyword",
        frontmatter={},
    )
    mock_vault = MagicMock()
    mock_vault.reader.search.return_value = [sr]

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["search", "test", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["path"] == "a.md"
    assert data[0]["score"] == 1.0


def test_search_json_multiple_results() -> None:
    """``search --json`` with multiple results emits them all."""
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
    mock_vault = MagicMock()
    mock_vault.reader.search.return_value = results

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["search", "alpha", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["path"] == "notes/alpha.md"
    assert data[1]["path"] == "notes/beta.md"


def test_search_json_empty_results() -> None:
    """``search --json`` with no results outputs an empty JSON array."""
    mock_vault = MagicMock()
    mock_vault.reader.search.return_value = []

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["search", "nothing", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == []


def test_search_passes_options() -> None:
    """``search`` forwards -n/-m/--folder to vault.reader.search."""
    mock_vault = MagicMock()
    mock_vault.reader.search.return_value = []

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(
            app,
            ["search", "query", "-n", "5", "-m", "semantic", "--folder", "Journal"],
        )

    assert result.exit_code == 0, result.output
    mock_vault.reader.search.assert_called_once_with(
        "query", limit=5, mode="semantic", folder="Journal"
    )


# ---------------------------------------------------------------------------
# reindex command
# ---------------------------------------------------------------------------


def test_reindex_prints_stats() -> None:
    """``reindex`` prints all five counters."""
    mock_result = MagicMock()
    mock_result.added = 3
    mock_result.modified = 1
    mock_result.deleted = 2
    mock_result.unchanged = 10
    mock_result.skipped = 4

    mock_vault = MagicMock()
    mock_vault.index.reindex.return_value = mock_result

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    assert "3 added" in result.output
    assert "1 modified" in result.output
    assert "2 deleted" in result.output
    assert "10 unchanged" in result.output
    assert "4 skipped" in result.output


def test_reindex_calls_build_index_first() -> None:
    """``reindex`` calls build_index() before reindex() (#525 contract)."""
    mock_vault = MagicMock()
    mock_result = MagicMock()
    mock_result.added = mock_result.modified = mock_result.deleted = 0
    mock_result.unchanged = mock_result.skipped = 0
    mock_vault.index.reindex.return_value = mock_result

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    # build_index must be called before reindex
    call_names = [c[0] for c in mock_vault.index.method_calls]
    assert call_names.index("build_index") < call_names.index("reindex")


def test_reindex_builds_embeddings_when_configured() -> None:
    """``reindex`` calls build_embeddings() (no force) when configured."""
    mock_vault = MagicMock()
    mock_result = MagicMock()
    mock_result.added = 1
    mock_result.modified = mock_result.deleted = mock_result.skipped = 0
    mock_result.unchanged = 5
    mock_vault.index.reindex.return_value = mock_result
    mock_vault.index.build_embeddings.return_value = 10

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_embeddings.assert_called_once_with()


def test_reindex_swallows_embeddings_not_configured_error() -> None:
    """Embedding graceful-degradation: EmbeddingsNotConfiguredError exits 0 (#774)."""
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    mock_vault = MagicMock()
    mock_result = MagicMock()
    mock_result.added = mock_result.modified = mock_result.deleted = (
        mock_result.skipped
    ) = 0
    mock_result.unchanged = 5
    mock_vault.index.reindex.return_value = mock_result
    mock_vault.index.build_embeddings.side_effect = EmbeddingsNotConfiguredError(
        "not configured"
    )

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    mock_vault.index.build_embeddings.assert_called_once()


def test_reindex_surfaces_internal_valueerror_from_embeddings() -> None:
    """An internal ValueError (not EmbeddingsNotConfiguredError) surfaces (#774)."""
    mock_vault = MagicMock()
    mock_result = MagicMock()
    mock_result.added = mock_result.modified = mock_result.deleted = (
        mock_result.skipped
    ) = 0
    mock_result.unchanged = 5
    mock_vault.index.reindex.return_value = mock_result
    mock_vault.index.build_embeddings.side_effect = ValueError(
        "Failed to load vector index after _load_vectors()"
    )

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0


def test_reindex_never_forces_embedding_rebuild() -> None:
    """``reindex`` always calls build_embeddings() without force (#665)."""
    mock_vault = MagicMock()
    mock_result = MagicMock()
    mock_result.added = 2
    mock_result.modified = 1
    mock_result.deleted = mock_result.skipped = 0
    mock_result.unchanged = 10
    mock_vault.index.reindex.return_value = mock_result
    mock_vault.index.build_embeddings.return_value = 4

    with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
        result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    # Must be called with no arguments (no force=True)
    mock_vault.index.build_embeddings.assert_called_once_with()


def test_reindex_against_real_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``reindex`` on a real (unbuilt) Vault must succeed.

    Regression for a crash where reindex() was called before build_index()
    (bucket-4 readiness contract, issue #525). Mock-based tests miss this
    because they replace Vault wholesale.
    """
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "a.md").write_text("# A\n\nhello\n")
    (vault_dir / "b.md").write_text("# B\n\nworld\n")

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv(f"{_ENV_PREFIX}_STATE_PATH", str(tmp_path / "state.json"))

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0, result.output
    assert "Reindex" in result.output


# ---------------------------------------------------------------------------
# verbose / logging root-callback
# ---------------------------------------------------------------------------


def test_verbose_enables_debug_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """``-v`` sets ``FASTMCP_LOG_LEVEL=DEBUG`` in the process environment."""
    import os

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", "/tmp/vault")
    saved = os.environ.pop("FASTMCP_LOG_LEVEL", None)
    try:
        mock_vault = MagicMock()
        mock_stats = MagicMock()
        mock_stats.documents_indexed = 0
        mock_stats.chunks_indexed = 0
        mock_vault.index.build_index.return_value = mock_stats
        with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
            runner.invoke(app, ["-v", "index"])
        assert os.environ.get("FASTMCP_LOG_LEVEL") == "DEBUG"
    finally:
        if saved is not None:
            os.environ["FASTMCP_LOG_LEVEL"] = saved
        else:
            os.environ.pop("FASTMCP_LOG_LEVEL", None)


def test_default_level_pins_httpx_httpcore_to_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without -v, the CLI pins httpx/httpcore to WARNING so their per-request
    INFO lines don't flood normal embedding builds (#792) — the exact production
    path, exercised through Typer rather than a direct _root call."""
    import logging

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(tmp_path))
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 0
    mock_stats.chunks_indexed = 0
    mock_vault.index.build_index.return_value = mock_stats

    httpx_log = logging.getLogger("httpx")
    httpcore_log = logging.getLogger("httpcore")
    saved = (httpx_log.level, httpcore_log.level)
    # Pre-set the flood-prone state (NOTSET → inherits the root level) so the
    # assertion proves the default CLI path actively pins WARNING.
    httpx_log.setLevel(logging.NOTSET)
    httpcore_log.setLevel(logging.NOTSET)
    try:
        with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
            runner.invoke(app, ["index"])
        assert httpx_log.level == logging.WARNING
        assert httpcore_log.level == logging.WARNING
    finally:
        httpx_log.setLevel(saved[0])
        httpcore_log.setLevel(saved[1])


def test_verbose_resets_httpx_httpcore_to_follow_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-v` resets httpx/httpcore to NOTSET so the root DEBUG level governs and
    they become visible; it no longer pins them to WARNING (#792)."""
    import logging

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", "/tmp/vault")
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 0
    mock_stats.chunks_indexed = 0
    mock_vault.index.build_index.return_value = mock_stats

    httpx_log = logging.getLogger("httpx")
    httpcore_log = logging.getLogger("httpcore")
    saved = (httpx_log.level, httpcore_log.level)
    # Pre-set WARNING so the assertion proves -v actively resets it, not that
    # the logger merely started unset.
    httpx_log.setLevel(logging.WARNING)
    httpcore_log.setLevel(logging.WARNING)
    try:
        with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
            runner.invoke(app, ["-v", "index"])
        assert httpx_log.level == logging.NOTSET
        assert httpcore_log.level == logging.NOTSET
    finally:
        httpx_log.setLevel(saved[0])
        httpcore_log.setLevel(saved[1])


def test_root_handler_added_when_none_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A StreamHandler is added to root when it has no handlers."""
    import logging

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", "/tmp/vault")
    mock_vault = MagicMock()
    mock_stats = MagicMock()
    mock_stats.documents_indexed = 0
    mock_stats.chunks_indexed = 0
    mock_vault.index.build_index.return_value = mock_stats

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    root.handlers.clear()
    try:
        with patch("markdown_vault_mcp.cli._build_vault", return_value=mock_vault):
            runner.invoke(app, ["index"])
        assert len(root.handlers) >= 1
    finally:
        root.handlers[:] = original_handlers
