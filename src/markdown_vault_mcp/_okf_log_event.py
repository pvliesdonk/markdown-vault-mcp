"""Record one editorial event in the selected OKF history."""

from __future__ import annotations

import os
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_convention import (
    canonical_path,
    destination_home,
    log_anchor,
)
from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.exceptions import DocumentUnreadableError, InvalidRequestError
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers._write_kernel import MISSING_ETAG
from markdown_vault_mcp.managers.document import stored_body
from markdown_vault_mcp.okf import append_okf_log_entry, is_okf_reserved_file
from markdown_vault_mcp.scanner import _unfenced_lines
from markdown_vault_mcp.utils import folder_of
from markdown_vault_mcp.utils.links import build_plain_destination

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

    from markdown_vault_mcp.managers.document import DocumentManager

_deferred: ContextVar[bool] = ContextVar("okf_deferred_event", default=False)


@contextmanager
def deferred_event() -> Iterator[None]:
    """Suppress automatic write bullets in this context while retaining index maintenance."""
    token = _deferred.set(True)
    try:
        yield
    finally:
        _deferred.reset(token)


def event_deferred() -> bool:
    """Return whether automatic write bullets are deferred in this context."""
    return _deferred.get()


def _literal(text: str) -> str:
    return "".join("\\" + c if c in "\\[]<~`" else c for c in text)


def _unsafe(text: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Cs", "Zl", "Zp"} for c in text)


def _insert_preserving(body: str, updated: str, bullet: str) -> str:
    old_lines = body.splitlines(keepends=True)
    old = body.splitlines()
    new = updated.splitlines()
    visible = list(_unfenced_lines([*old, ""]))
    if not visible or visible[-1][0] != len(old):
        raise InvalidRequestError("Log layout cannot take an event safely; unchanged.")
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(old), len(new)) - prefix
        and old[-1 - suffix] == new[-1 - suffix]
    ):
        suffix += 1
    if prefix + suffix != len(old):
        raise InvalidRequestError("Log layout cannot take an event safely; unchanged.")
    inserted = new[prefix : len(new) - suffix if suffix else len(new)]
    visible_new = dict(_unfenced_lines([*new, ""]))
    if not any(
        new[i] == bullet and i in visible_new
        for i in range(prefix, prefix + len(inserted))
    ):
        raise InvalidRequestError("Log layout cannot take an event safely; unchanged.")
    eol = "\r\n" if "\r\n" in body else "\n"
    if (
        prefix
        and old_lines[prefix - 1]
        and not old_lines[prefix - 1].endswith(("\n", "\r"))
    ):
        old_lines[prefix - 1] += eol
    return (
        "".join(old_lines[:prefix])
        + "".join(line + eol for line in inserted)
        + "".join(old_lines[prefix:])
    )


def _validate_input(
    paths: list[str], description: str, proposed_by: str | None
) -> None:
    if (
        not description.strip()
        or len(description.strip()) > 500
        or _unsafe(description)
    ):
        raise InvalidRequestError(
            "Event description must be nonempty, at most 500 characters, and contain no control or line separator characters."
        )
    if proposed_by is not None and (
        not proposed_by.strip()
        or len(proposed_by.strip()) > 100
        or _unsafe(proposed_by)
    ):
        raise InvalidRequestError(
            "Proposed by must be nonempty single-line text, at most 100 characters."
        )
    if not paths:
        raise InvalidRequestError("Supply at least one concept path.")
    if any(_unsafe(path) for path in paths):
        raise InvalidRequestError("Concept paths must be single-line text.")


def _canonical_paths(doc_mgr: DocumentManager, paths: list[str]) -> list[str]:
    canonical = []
    for path in paths:
        doc_mgr._validate_path(path)
        target = canonical_path(path, doc_mgr._source_dir)
        if _unsafe(target):
            raise InvalidRequestError(
                "Concept paths must not contain control or line separator characters."
            )
        if is_okf_reserved_file(target) or is_okf_reserved_file(path):
            raise InvalidRequestError("Event paths must identify concept notes.")
        guarded = doc_mgr._read_guard(target)
        if guarded is None:
            raise InvalidRequestError(
                f"Concept {path!r} was not found; read it before recording the event."
            )
        try:
            with guarded[0].open("rb"):
                pass
        except OSError as exc:
            raise DocumentUnreadableError(path, str(exc)) from exc
        if target not in canonical:
            canonical.append(target)
    return canonical


def record_event(
    doc_mgr: DocumentManager,
    paths: list[str],
    description: str,
    policy: tuple[tuple[str, ...], Callable[[], dict[str, Any]]],
    proposed_by: str | None = None,
    *,
    today: date | None = None,
) -> str:
    """Append one event and return its vault-relative log path.

    Args:
        doc_mgr: Document manager holding the shared write lock.
        paths: Existing concept paths covered by the event.
        description: Single-line explanation, at most 500 characters.
        policy: Designated folders and effective log capability callback.
        proposed_by: Optional proposal attribution.
        today: Optional event date.

    Returns:
        Path of the log receiving the event.
    """
    _validate_input(paths, description, proposed_by)
    folders, capability_report = policy
    report = capability_report()
    if not report["effective"]:
        raise InvalidRequestError(
            f"OKF event logging is not active: {report['reason']}."
        )
    doc_mgr.ensure_writable()
    with doc_mgr.write_scope():
        canonical = _canonical_paths(doc_mgr, paths)
        common = os.path.commonpath([folder_of(p) for p in canonical])
        folder = log_anchor(common, folders, doc_mgr._source_dir)
        if destination_home(folder, "log.md", doc_mgr._source_dir) is None:
            raise InvalidRequestError(
                "The selected log is an alias; choose a safe log destination."
            )
        log_path = f"{folder}/log.md" if folder else "log.md"
        links = ", ".join(
            f"[{_literal(p)}](/{build_plain_destination(p)})" for p in canonical
        )
        summary = f"{_literal(description.strip())} — {links}"
        if proposed_by is not None:
            summary += f" (proposed by {_literal(proposed_by.strip())})"
        raw = doc_mgr._read_snapshot(log_path)
        etag = compute_etag(raw.encode("utf-8")) if raw is not None else MISSING_ETAG
        body = stored_body(raw).decode("utf-8") if raw is not None else None
        updated = append_okf_log_entry(
            body, date=(today or date.today()).isoformat(), summary=summary
        )
        if body is not None:
            updated = _insert_preserving(body, updated, f"- {summary}")
        with okf_write_suppressed():
            doc_mgr.replace_body(log_path, raw, updated, if_match=etag)
        return log_path
