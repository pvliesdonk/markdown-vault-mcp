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
    folder_is_designated,
)
from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import append_okf_log_entry, is_okf_reserved_file
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils import folder_of
from markdown_vault_mcp.utils.fs import is_regular_file
from markdown_vault_mcp.utils.links import build_plain_destination, escape_link_text

if TYPE_CHECKING:
    from collections.abc import Iterator

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


def record_event(
    doc_mgr: DocumentManager,
    paths: list[str],
    description: str,
    folders: tuple[str, ...],
    *,
    today: date | None = None,
) -> str:
    """Append one event and return its vault-relative log path.

    Args:
        doc_mgr: Document manager holding the shared write lock.
        paths: Existing concept paths covered by the event.
        description: Single-line explanation, at most 500 characters.
        folders: Designated folder patterns.
        today: Optional event date.

    Returns:
        Path of the log receiving the event.
    """
    if (
        not description.strip()
        or len(description) > 500
        or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in description)
    ):
        raise InvalidRequestError(
            "Event description must be nonempty, at most 500 characters, and contain no control or line separator characters."
        )
    if not paths:
        raise InvalidRequestError("Supply at least one concept path.")
    doc_mgr.ensure_writable()
    with doc_mgr.write_scope():
        canonical = []
        for path in paths:
            doc_mgr._validate_path(path)
            target = canonical_path(path, doc_mgr._source_dir)
            if any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in target):
                raise InvalidRequestError(
                    "Concept paths must not contain control or line separator characters."
                )
            if is_okf_reserved_file(target) or is_okf_reserved_file(path):
                raise InvalidRequestError("Event paths must identify concept notes.")
            if doc_mgr._read_note(target, capped=False) is None:
                raise InvalidRequestError(
                    f"Concept {path!r} was not found; read it before recording the event."
                )
            if target not in canonical:
                canonical.append(target)
        folder = os.path.commonpath([folder_of(p) for p in canonical])
        while (
            folder
            and not folder_is_designated(folder, folders)
            and not is_regular_file(doc_mgr._source_dir / folder / "log.md")
        ):
            folder = folder_of(folder)
        if destination_home(folder, "log.md", doc_mgr._source_dir) is None:
            raise InvalidRequestError(
                "The selected log is an alias; choose a safe log destination."
            )
        log_path = f"{folder}/log.md" if folder else "log.md"
        links = ", ".join(
            f"[{escape_link_text(p)}](/{build_plain_destination(p)})" for p in canonical
        )
        summary = f"{description}: {links}"
        existing = doc_mgr._read_note(log_path, capped=False)
        body = (
            strip_frontmatter_block(existing.content) if existing is not None else None
        )
        updated = append_okf_log_entry(
            body, date=(today or date.today()).isoformat(), summary=summary
        )
        with okf_write_suppressed():
            doc_mgr.replace_body(
                log_path, existing.content if existing is not None else None, updated
            )
        return log_path
