"""Tests for the bearer-tokens TOML loader.

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_auth_bearer_tokens_file.py`` (#2277).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from markdown_vault_mcp._auth import build_bearer_auth
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError


def _write_tokens(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "tokens.toml"
    path.write_text(content, encoding="utf-8")
    return path


class TestBearerTokensFileLoader:
    def test_returns_verifier_with_mapped_subjects(self, tmp_path: Path):
        path = _write_tokens(
            tmp_path,
            (
                "[tokens]\n"
                '"alice-token" = "user:alice@example.com"\n'
                '"ci-bot-token" = "service:ci-bot"\n'
            ),
        )
        auth = build_bearer_auth(ServerConfig(bearer_tokens_file=path))
        assert auth is not None
        alice = auth.tokens["alice-token"]
        bot = auth.tokens["ci-bot-token"]
        assert alice["client_id"] == "user:alice@example.com"
        assert alice["scopes"] == ["read", "write"]
        assert bot["client_id"] == "service:ci-bot"

    def test_missing_file_raises_configuration_error(self, tmp_path: Path):
        with pytest.raises(ConfigurationError, match="not found"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=tmp_path / "nope.toml"))

    def test_tilde_path_expands_at_load_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Direct construction with a ``~``-prefixed path keeps the tilde
        # literal on the dataclass; the loader normalises it via
        # ``Path.expanduser`` so the file is found.
        _write_tokens(tmp_path, '[tokens]\n"k1" = "user:alice"\n')
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = ServerConfig(bearer_tokens_file=Path("~/tokens.toml"))
        # Field stays literal — the loader is the expansion site.
        assert cfg.bearer_tokens_file is not None
        assert str(cfg.bearer_tokens_file) == "~/tokens.toml"
        # ``expanduser`` against the patched ``$HOME`` resolves to the
        # same file the loader will touch.  This is the meaningful
        # regression guard: removing ``expanduser`` from the loader
        # would make ``build_bearer_auth`` raise "file not found".
        assert cfg.bearer_tokens_file.expanduser() == tmp_path / "tokens.toml"
        auth = build_bearer_auth(cfg)
        assert auth is not None
        assert auth.tokens["k1"]["client_id"] == "user:alice"

    def test_path_is_directory_raises_configuration_error(self, tmp_path: Path):
        directory = tmp_path / "tokens.toml"
        directory.mkdir()
        with pytest.raises(ConfigurationError, match="not a regular file"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=directory))

    def test_non_utf8_file_raises_configuration_error(self, tmp_path: Path):
        path = tmp_path / "tokens.toml"
        path.write_bytes(b'[tokens]\n\xff\xfe = "alice"\n')
        with pytest.raises(ConfigurationError, match="could not be read"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_malformed_toml_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens\n"x" = "y"')
        with pytest.raises(ConfigurationError, match="parse"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_blank_file_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, "")
        with pytest.raises(ConfigurationError, match="empty"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_missing_tokens_table_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[other]\nkey = "v"\n')
        with pytest.raises(ConfigurationError, match="\\[tokens\\]"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_non_string_subject_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens]\n"x" = 42\n')
        with pytest.raises(ConfigurationError, match="string"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_empty_subject_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens]\n"x" = ""\n')
        with pytest.raises(ConfigurationError, match="empty"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_empty_tokens_table_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, "[tokens]\n")
        with pytest.raises(ConfigurationError, match="non-empty"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_whitespace_only_subject_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens]\n"x" = "   "\n')
        with pytest.raises(ConfigurationError, match="empty"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_empty_token_key_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens]\n"" = "user:alice"\n')
        with pytest.raises(ConfigurationError, match="empty or whitespace"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_whitespace_only_token_key_raises_configuration_error(self, tmp_path: Path):
        path = _write_tokens(tmp_path, '[tokens]\n"   " = "user:alice"\n')
        with pytest.raises(ConfigurationError, match="empty or whitespace"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_nested_subtable_raises_clear_diagnostic(self, tmp_path: Path):
        # Common operator slip: writing `[tokens.foo]` instead of
        # `[tokens]\n"foo" = "..."` produces a nested-table structure;
        # the loader should surface a clear "nested table" diagnostic
        # rather than the misleading "subject must be a string".
        path = _write_tokens(tmp_path, '[tokens.foo]\nbar = "baz"\n')
        with pytest.raises(ConfigurationError, match="nested table"):
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))

    def test_error_messages_do_not_leak_token_value(self, tmp_path: Path):
        # Defensive: error messages must never embed the raw token (the
        # KEY in the [tokens] map), so an operator pasting a startup
        # exception into a bug report doesn't leak credentials.
        secret_token = "ghp_secretvalue_xxx_must_not_appear"
        path = _write_tokens(tmp_path, f'[tokens]\n"{secret_token}" = ""\n')
        with pytest.raises(ConfigurationError) as excinfo:
            build_bearer_auth(ServerConfig(bearer_tokens_file=path))
        assert secret_token not in str(excinfo.value)


class TestBearerTokensFilePrecedence:
    def test_file_takes_precedence_over_single_token(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        path = _write_tokens(tmp_path, '[tokens]\n"file-token" = "user:via-file"\n')
        with caplog.at_level(logging.WARNING):
            auth = build_bearer_auth(
                ServerConfig(
                    bearer_token="single-token",
                    bearer_tokens_file=path,
                )
            )
        assert auth is not None
        # Single token must NOT appear; only the file's tokens are loaded.
        assert "single-token" not in auth.tokens
        assert auth.tokens["file-token"]["client_id"] == "user:via-file"
        # WARNING surfaced.
        assert any(
            "bearer_tokens_file" in r.message
            and "bearer_token" in r.message
            and r.levelname == "WARNING"
            for r in caplog.records
        )


class TestBearerSingleDefaultSubject:
    def test_default_subject_is_bearer_anon(self):
        auth = build_bearer_auth(ServerConfig(bearer_token="t"))
        assert auth is not None
        assert auth.tokens["t"]["client_id"] == "bearer-anon"

    def test_custom_default_subject(self):
        auth = build_bearer_auth(
            ServerConfig(bearer_token="t", bearer_default_subject="service:x")
        )
        assert auth is not None
        assert auth.tokens["t"]["client_id"] == "service:x"

    def test_empty_default_subject_falls_back(self):
        # Belt-and-suspenders: direct construction with an empty
        # bearer_default_subject must not produce an empty client_id.
        auth = build_bearer_auth(
            ServerConfig(bearer_token="t", bearer_default_subject="")
        )
        assert auth is not None
        assert auth.tokens["t"]["client_id"] == "bearer-anon"

    def test_whitespace_only_default_subject_falls_back(self):
        auth = build_bearer_auth(
            ServerConfig(bearer_token="t", bearer_default_subject="   ")
        )
        assert auth is not None
        assert auth.tokens["t"]["client_id"] == "bearer-anon"


def test_refused_stat_is_a_configuration_error(tmp_path, monkeypatch) -> None:
    """A path the process cannot stat is reported, not read as missing (#1625)."""
    from markdown_vault_mcp import _auth

    def _refuse(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(_auth, "is_regular_file", _refuse)
    with pytest.raises(ConfigurationError, match="could not be read"):
        _auth._load_bearer_tokens(tmp_path / "tokens.toml")
