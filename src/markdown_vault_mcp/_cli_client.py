"""Nonsecret client profiles, resolved before any local vault construction."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastmcp_pvl_core import env

_PREFIX = "MARKDOWN_VAULT_MCP"


class ClientError(ValueError):
    """A safe, actionable client diagnostic with a CLI exit status."""

    def __init__(self, message: str, code: int = 2) -> None:
        """Store the public diagnostic and exit status."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ClientOptions:
    """Explicit per-command connection choices."""

    server: str | None = None
    local: bool = False
    timeout: float | None = None


@dataclass(frozen=True)
class ClientSettings:
    """Resolved connection; credentials never appear in representations."""

    url: str | None
    timeout: float = 30
    token: str | None = field(default=None, repr=False)


def _profile() -> dict[str, object]:
    selected = env(_PREFIX, "CLIENT_CONFIG")
    path = (
        Path(selected)
        if selected is not None
        else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "markdown-vault-mcp/client.toml"
    )
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        if selected is None:
            return {}
        raise ClientError("Selected client configuration does not exist.") from None
    except (OSError, ValueError):
        raise ClientError("Cannot read client TOML configuration.") from None
    remote = data.get("remote", {})
    if (
        set(data) - {"remote"}
        or not isinstance(remote, dict)
        or set(remote) - {"url", "token_env", "timeout_seconds"}
    ):
        raise ClientError(
            "Client TOML accepts only [remote] url, token_env, timeout_seconds."
        )
    for key in ("url", "token_env"):
        if key in remote and (
            not isinstance(remote[key], str) or not remote[key].strip()
        ):
            raise ClientError(f"Client TOML {key} must be a nonempty string.")
    if "timeout_seconds" in remote:
        _timeout(remote["timeout_seconds"])
    return remote


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ClientError("Timeout must be a positive finite number of seconds.")
    try:
        result = float(value)
    except OverflowError:
        raise ClientError(
            "Timeout must be a positive finite number of seconds."
        ) from None
    if not math.isfinite(result) or result <= 0:
        raise ClientError("Timeout must be a positive finite number of seconds.")
    return result


def _url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ClientError(
            "Server URL must be HTTP(S) with a host and no credentials, query or fragment."
        )
    return value


def resolve_settings(options: ClientOptions) -> ClientSettings:
    """Select local or remote settings without reading server configuration.

    Args:
        options: Explicit command choices, overriding environment and profile.

    Returns:
        Connection settings, with no credential lookup for local operation.

    Raises:
        ClientError: Invalid profile, selection, URL, timeout or selected token.
    """
    if options.local and options.server is not None:
        raise ClientError("--local conflicts with --server.")
    if options.local:
        return ClientSettings(
            None, _timeout(options.timeout if options.timeout is not None else 30)
        )
    profile = _profile()
    url = (
        options.server
        if options.server is not None
        else env(_PREFIX, "SERVER_URL") or profile.get("url")
    )
    timeout = _timeout(
        options.timeout
        if options.timeout is not None
        else profile.get("timeout_seconds", 30)
    )
    if url is None:
        return ClientSettings(None, timeout)
    url = _url(str(url))
    token = env(_PREFIX, "CLIENT_TOKEN")
    if token is None and "token_env" in profile:
        token = os.environ.get(str(profile["token_env"]), "")
    if token is not None and (not token.strip() or any(c in token for c in "\r\n")):
        raise ClientError(
            "Selected client token environment variable must contain a nonempty bearer token."
        )
    return ClientSettings(url, timeout, token.strip() if token is not None else None)
