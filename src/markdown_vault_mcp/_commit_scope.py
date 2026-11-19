"""Per-tool-call commit scoping for the write-callback dispatcher.

A vault write commits per file. That is correct for a single ``write``, and
wrong for anything that touches many: ``okf_convert_links`` over a real vault
produced **2,595 commits for one logical migration**, and every concurrent MCP
write queued behind 2,595 sequential ``git commit`` invocations until the call
timed out.

The server middleware supplies the missing boundary. It binds a scope for the
duration of each tool call; the dispatcher snapshots that
scope when a write is fired and groups the call's writes into **one** commit.
A call that touched many files is named after the tool that caused them; a call
that touched exactly one keeps that file's own path as the subject. A folder
move instead names its source and destination. Secondary
writes (OKF maintenance, a rename's backlink rewrites) are marked separately so
they can share that note's commit without replacing its path subject with a
count.

The scope travels in a :class:`~contextvars.ContextVar`. ``fire`` captures it
on the request's ``to_thread`` worker, whose copied context carries the
variable. The dispatcher binds only the captured folder roots while invoking
a batch callback; its worker does not inherit the request context (#1218).

Grouping is keyed by scope token rather than by queue position. The queue is
FIFO, but a shared server interleaves concurrent tool calls, and position-based
grouping would fold two unrelated calls into one commit — the failure this
boundary exists to prevent.
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_counter = itertools.count()


@dataclass(slots=True)
class CommitScope:
    """One tool invocation's commit boundary.

    Attributes:
        token: Process-unique id distinguishing concurrent invocations. Two
            calls to the same tool get different tokens, so their writes are
            never merged into one commit.
        tool_name: The MCP tool that opened the scope. Becomes the commit
            subject when the call wrote more than one file; a single-file
            call commits under that file's own path instead. Folder moves
            name their source and destination.
        folder_move: Validated absolute roots, set before folder callbacks fire.
    """

    token: int
    tool_name: str
    folder_move: tuple[Path, Path] | None = None


_scope_var: ContextVar[CommitScope | None] = ContextVar(
    "markdown_vault_mcp_commit_scope", default=None
)


@contextmanager
def bound_commit_scope(tool_name: str) -> Iterator[CommitScope]:
    """Bind a fresh commit scope for the duration, resetting it afterward.

    Args:
        tool_name: Name of the tool being invoked.

    Yields:
        The bound scope, so a caller can enqueue its end marker.
    """
    scope = CommitScope(token=next(_counter), tool_name=tool_name)
    var_token = _scope_var.set(scope)
    try:
        yield scope
    finally:
        _scope_var.reset(var_token)


_folder_move_var: ContextVar[tuple[Path, Path] | None] = ContextVar(
    "markdown_vault_mcp_folder_move", default=None
)


@contextmanager
def bound_folder_move(roots: tuple[Path, Path] | None) -> Iterator[None]:
    """Bind folder roots for a batch callback and restore the prior context.

    Args:
        roots: Validated source and destination directories, or ``None``.

    Yields:
        Control while the roots are bound.
    """
    token = _folder_move_var.set(roots)
    try:
        yield
    finally:
        _folder_move_var.reset(token)


def current_folder_move() -> tuple[Path, Path] | None:
    """Return the folder roots bound for the current batch callback."""
    return _folder_move_var.get()


def current_commit_scope() -> CommitScope | None:
    """Return the scope bound to the current context, or ``None``.

    ``None`` means the write has no owning tool call — a background or
    startup write — and must commit on its own, exactly as before.
    """
    return _scope_var.get()


_auxiliary_var: ContextVar[bool] = ContextVar("auxiliary_commit_write", default=False)


@contextmanager
def auxiliary_commit_writes() -> Iterator[None]:
    """Mark secondary writes for commit subjects, without excluding their files."""
    token = _auxiliary_var.set(True)
    try:
        yield
    finally:
        _auxiliary_var.reset(token)


def is_auxiliary_commit_write() -> bool:
    """Return whether this callback is fired by secondary maintenance."""
    return _auxiliary_var.get()


def new_commit_scope(tool_name: str) -> CommitScope:
    """Create an unbound, process-unique scope for a persistent group."""
    return CommitScope(token=next(_counter), tool_name=tool_name)


_group_var: ContextVar[CommitScope | None] = ContextVar("commit_group", default=None)


@contextmanager
def bound_commit_group(scope: CommitScope) -> Iterator[None]:
    """Bind a persistent group scope for the current write context."""
    token = _group_var.set(scope)
    try:
        yield
    finally:
        _group_var.reset(token)


def current_commit_group() -> CommitScope | None:
    """Return the group scope bound to this context, if any."""
    return _group_var.get()
