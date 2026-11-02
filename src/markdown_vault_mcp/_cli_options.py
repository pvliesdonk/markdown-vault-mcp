"""Connection and output options every read command shares.

Each alias carries its Typer declaration, so a command spells
``server: Server = None`` and the help text lives in one place. Options whose
help differs per command (``--json``, a command's own predicates) stay with
the command.
"""

from __future__ import annotations

from typing import Annotated

import typer

SourceDir = Annotated[str | None, typer.Option(help="Local vault directory.")]
Server = Annotated[str | None, typer.Option(help="Complete hosted MCP endpoint URL.")]
Local = Annotated[bool, typer.Option(help="Override a configured remote endpoint.")]
Timeout = Annotated[float | None, typer.Option(help="Remote deadline in seconds.")]
Agent = Annotated[bool, typer.Option(help="Return escaped agent records.")]
WaitForPendingWrites = Annotated[
    bool, typer.Option(help="Wait for the remote server's pending writes.")
]
Folder = Annotated[
    str | None, typer.Option(help="Folder subtree; empty selects root notes.")
]
