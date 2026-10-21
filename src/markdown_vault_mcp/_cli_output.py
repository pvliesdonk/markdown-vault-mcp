"""Consistent read output with explicit freshness and terminal-safe records."""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer

from markdown_vault_mcp._read_payloads import ReadPayloadError

if TYPE_CHECKING:
    from markdown_vault_mcp._cli_remote import RemoteReply


def terminal_text(value: str) -> str:
    """Escape terminal controls while retaining printable Unicode.

    Args:
        value: Untrusted note text.

    Returns:
        A single printable line, including escapes for Unicode separators.
    """
    return "".join(
        char.encode("unicode_escape").decode("ascii")
        if unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
        else char
        for char in value
    )


def render_reply(
    command: str, reply: RemoteReply, output: Literal["text", "json", "agent"]
) -> None:
    """Render a read without changing its structured payload.

    Args:
        command: Read command name for the agent header.
        reply: Original payload and any observed freshness/mode metadata.
        output: Human text, legacy JSON, or an agent header plus JSON records.
    """
    try:
        _render(command, reply, output)
        sys.stdout.flush()
    except BrokenPipeError:
        # Prevent the interpreter's final flush from raising the same error.
        with Path(os.devnull).open("w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())


def _render(command: str, reply: RemoteReply, output: str) -> None:
    if output in {"json", "agent"}:
        _check_json(reply.data)
    observed = reply.meta.get("index_stale")
    stale = observed if isinstance(observed, bool) else "unknown"
    if output != "agent" and stale is True:
        typer.echo("WARNING: search index may be stale.", err=True)
    if output == "json":
        typer.echo(json.dumps(reply.data, indent=2))
        return
    items = reply.data if isinstance(reply.data, list) else [reply.data]
    if output == "agent":
        _agent_records(command, reply, items, stale)
        return
    if command == "search":
        _search_text(items)
        return
    typer.echo(terminal_text(json.dumps(reply.data, ensure_ascii=False)))


def _check_json(data: Any) -> None:
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise ReadPayloadError("Read payload is not JSON-compatible.", 1) from None


def _agent_records(
    command: str, reply: RemoteReply, items: list[Any], stale: bool | str
) -> None:
    actual = sorted(
        {
            hit["search_type"]
            for hit in items
            if isinstance(hit, dict)
            and hit.get("search_type") in ("keyword", "semantic", "hybrid")
        }
    )
    requested = terminal_text(str(reply.meta.get("requested_mode", "unknown")))
    typer.echo(
        f"OK {command} count={len(items)} requested_mode={requested} actual_mode={','.join(actual) or 'unknown'} index_stale={str(stale).lower()}"
    )
    for item in items:
        typer.echo(_json_record(item))


def _search_text(items: list[Any]) -> None:
    for hit in items:
        typer.echo(f"  {terminal_text(hit['path'])} ({hit['score']:.4f})")
        if hit.get("title"):
            typer.echo(f"    {terminal_text(hit['title'])}")


def _json_record(value: object) -> str:
    return "".join(
        json.dumps(char)[1:-1]
        if unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
        else char
        for char in json.dumps(value, ensure_ascii=False)
    )
