"""Per-tool-call commit scoping for the write-callback dispatcher.

A vault write commits per file. That is correct for a single ``write``, and
wrong for anything that touches many: ``okf_convert_links`` over a real vault
produced **2,595 commits for one logical migration**, and every concurrent MCP
write queued behind 2,595 sequential ``git commit`` invocations until the call
timed out.

The server middleware supplies the missing boundary. It
binds a scope for the duration of each tool call; the dispatcher snapshots that
scope when a write is fired and groups the call's writes into **one** commit.
A call that touched many files is named after the tool that caused them; a call
that touched exactly one keeps that file's own path as the subject. Secondary
writes (OKF maintenance, a rename's backlink rewrites) are marked separately so
they can share that note's commit without replacing its path subject with a
count.

The scope travels in a :class:`~contextvars.ContextVar` and is read in
``fire``, never on the dispatcher's worker thread — the same constraint that
governs :func:`~markdown_vault_mcp._identity.current_principal` (#1218). ``fire``
runs on the request's ``to_thread`` worker, whose copied context carries the
variable; the worker thread has no request context at all, so reading there
would silently fall back to "no scope" and reinstate per-file commits.

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

_counter = itertools.count()


@dataclass(frozen=True, slots=True)
class CommitScope:
    """One tool invocation's commit boundary.

    Attributes:
        token: Process-unique id distinguishing concurrent invocations. Two
            calls to the same tool get different tokens, so their writes are
            never merged into one commit.
        tool_name: The MCP tool that opened the scope. Becomes the commit
            subject when the call wrote more than one file; a single-file
            call commits under that file's own path instead.
    """

    token: int
    tool_name: str


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
