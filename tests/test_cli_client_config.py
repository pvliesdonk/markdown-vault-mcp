"""Client selection must not evaluate server settings or leak credentials."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep developer client profiles out of resolver tests."""
    for name in ("SERVER_URL", "CLIENT_CONFIG", "CLIENT_TOKEN"):
        monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{name}", raising=False)
    monkeypatch.delenv("TEST_VAULT_ACCESS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_url_precedence_and_local_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "markdown-vault-mcp/client.toml"
    config.parent.mkdir()
    config.write_text(
        '[remote]\nurl="https://profile.example/proxy/mcp"\ntimeout_seconds=9\n'
    )
    assert resolve_settings(ClientOptions()).url == "https://profile.example/proxy/mcp"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_URL", "https://env.example/mcp")
    assert resolve_settings(ClientOptions()).url == "https://env.example/mcp"
    settings = resolve_settings(
        ClientOptions(server="https://flag.example/custom", timeout=2)
    )
    assert (settings.url, settings.timeout) == ("https://flag.example/custom", 2)
    assert resolve_settings(ClientOptions(local=True)).url is None
    with pytest.raises(ClientError):
        resolve_settings(ClientOptions(server="https://flag.example/mcp", local=True))


@pytest.mark.parametrize("token", ["secret-value", " secret-value "])
def test_credentials_are_remote_only_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_TOKEN", "")
    assert resolve_settings(ClientOptions()).url is None
    assert (
        resolve_settings(ClientOptions(server="https://example.com/mcp")).token is None
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_TOKEN", token)
    settings = resolve_settings(ClientOptions(server="https://example.com/mcp"))
    assert settings.token == "secret-value"
    assert "secret-value" not in repr(settings)


def test_empty_xdg_uses_home_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.chdir(tmp_path)
    for directory, host in ((tmp_path, "cwd"), (tmp_path / "home/.config", "home")):
        config = directory / "markdown-vault-mcp/client.toml"
        config.parent.mkdir(parents=True)
        config.write_text(f'[remote]\nurl="https://{host}.example/mcp"\n')
    assert resolve_settings(ClientOptions()).url == "https://home.example/mcp"


def test_selected_named_token_is_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "client.toml"
    config.write_text(
        '[remote]\nurl="https://example.com/mcp"\ntoken_env="TEST_VAULT_ACCESS"\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(config))
    with pytest.raises(ClientError):
        resolve_settings(ClientOptions())
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_TOKEN", "override")
    assert resolve_settings(ClientOptions()).token == "override"


def test_empty_direct_token_uses_selected_named_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "client.toml"
    config.write_text(
        '[remote]\nurl="https://example.com/mcp"\ntoken_env="TEST_VAULT_ACCESS"\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(config))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_TOKEN", "")
    monkeypatch.setenv("TEST_VAULT_ACCESS", "named-token")

    assert resolve_settings(ClientOptions()).token == "named-token"


@pytest.mark.parametrize(
    "contents",
    [
        '[remote]\ntoken="secret"',
        "[remote]\ntimeout_seconds=true",
        "broken = [",
        "[other]\na=1",
        "[remote]\nurl=42",
    ],
)
def test_invalid_profile_fails_without_echoing_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str
) -> None:
    config = tmp_path / "client.toml"
    config.write_text(contents)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(config))
    with pytest.raises(ClientError) as error:
        resolve_settings(ClientOptions())
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/vault",
        "https://user:secret@host/mcp",
        "https://host/mcp?token=secret",
        "https://host/#secret",
        "https:///missing",
        "https://host:bad/mcp",
    ],
)
def test_unsafe_urls_fail_without_echoing_url(url: str) -> None:
    with pytest.raises(ClientError) as error:
        resolve_settings(ClientOptions(server=url))
    assert "secret" not in str(error.value)


@settings(deadline=None)
@given(st.floats())
def test_timeout_accepts_exactly_positive_finite_values(seconds: float) -> None:
    options = ClientOptions(server="https://example.com/mcp", timeout=seconds)
    if seconds > 0 and math.isfinite(seconds):
        assert resolve_settings(options).timeout == seconds
    else:
        with pytest.raises(ClientError):
            resolve_settings(options)


def test_explicit_missing_profile_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(tmp_path / "missing.toml")
    )
    with pytest.raises(ClientError):
        resolve_settings(ClientOptions())


def test_huge_toml_timeout_is_a_client_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "client.toml"
    config.write_text("[remote]\ntimeout_seconds=" + "1" + "0" * 400 + "\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(config))
    with pytest.raises(ClientError):
        resolve_settings(ClientOptions())


def test_url_rejects_del_control_character() -> None:
    with pytest.raises(ClientError):
        resolve_settings(ClientOptions(server="https://host/mcp\x7f"))
