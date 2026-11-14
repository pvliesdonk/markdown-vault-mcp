"""Typed CLI diagnostics on stderr, independent of successful result payloads."""

from __future__ import annotations

import json
from typing import Literal

import typer

from markdown_vault_mcp._cli_client import ClientError
from markdown_vault_mcp._cli_output import terminal_text
from markdown_vault_mcp._read_payloads import ReadPayloadError
from markdown_vault_mcp.exceptions import ConfigurationError, InvalidRequestError


def diagnostic(error: Exception) -> ClientError:
    """Adapt a caught typed failure without interpreting its prose.

    Args:
        error: Failure already handled by the command's existing boundary.

    Returns:
        Diagnostic preserving the boundary's numeric status and safe message.
    """
    if isinstance(error, ClientError):
        return error
    if isinstance(error, ConfigurationError):
        return ClientError(
            str(error),
            1,
            kind="config",
            reason="configuration_error",
            hint="Check the local vault configuration and its required settings.",
        )
    if isinstance(error, ReadPayloadError):
        return ClientError(
            str(error),
            error.code,
            reason="invalid_payload" if error.code == 1 else "invalid_request",
            hint="Check the result format and server compatibility."
            if error.code == 1
            else "Check this command's --help for supported arguments.",
        )
    if isinstance(error, InvalidRequestError):
        return ClientError(
            str(error),
            1,
            kind="usage",
            reason="invalid_request",
            hint="Check this command's --help and correct the request.",
        )
    return ClientError(
        str(error),
        1,
        reason="operation_failed",
        hint="Check the vault state and server logs before trying again.",
    )


def render_error(
    command: str, error: ClientError, output: Literal["text", "json", "agent"]
) -> None:
    """Render one diagnostic in the explicitly selected mode on stderr.

    Args:
        command: Command path below the executable name.
        error: Safe typed diagnostic and optional recovery guidance.
        output: Plain text, a JSON error object or escaped agent lines.
    """
    if output == "json":
        data: dict[str, str | int] = {
            "kind": error.kind,
            "code": error.reason,
            "message": str(error),
            "exit_code": error.code,
        }
        if error.hint:
            data["hint"] = error.hint
        typer.echo(json.dumps({"error": data}, ensure_ascii=True), err=True)
        return
    message = terminal_text(str(error))
    if output == "agent":
        typer.echo(
            f"ERR {terminal_text(command)} {terminal_text(error.kind)} code={terminal_text(error.reason)}: {message}",
            err=True,
        )
    else:
        typer.echo(f"ERROR: {message}", err=True)
    if error.hint:
        prefix = "Hint" if output == "agent" else "hint"
        typer.echo(f"{prefix}: {terminal_text(error.hint)}", err=True)
