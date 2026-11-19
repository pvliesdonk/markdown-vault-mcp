"""Record one editorial event in the selected OKF history."""

from __future__ import annotations

import os
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from markdown_it import MarkdownIt

from markdown_vault_mcp._okf_convention import destination_home, log_anchor
from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.exceptions import DocumentUnreadableError, InvalidRequestError
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers.document import stored_body
from markdown_vault_mcp.okf import append_okf_log_entry, is_okf_reserved_file
from markdown_vault_mcp.scanner import _unfenced_lines
from markdown_vault_mcp.utils import folder_of
from markdown_vault_mcp.utils.links import build_plain_destination, escape_link_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

    from markdown_vault_mcp.managers.document import DocumentManager

_UNSAFE_LAYOUT = "Log layout cannot take an event safely (check `log.md` for an unclosed code fence or raw HTML block); unchanged."

_INACTIVE = {None: "logging is off", "okf_inactive": "no OKF bundle is active"}

_deferred: ContextVar[bool | Callable[[], bool]] = ContextVar(
    "okf_deferred_event", default=False
)


@contextmanager
def deferred_event(active: Callable[[], bool] | None = None) -> Iterator[None]:
    """Suppress automatic write bullets while retaining index maintenance.

    Args:
        active: Optional predicate controlling suppression for the current write.

    Yields:
        Control with the deferred event policy bound.
    """
    token = _deferred.set(active if active is not None else True)
    try:
        yield
    finally:
        _deferred.reset(token)


def event_deferred() -> bool:
    """Return whether automatic write bullets are deferred in this context."""
    value = _deferred.get()
    return value() if callable(value) else value


def _literal(text: str) -> str:
    return "".join("\\" + c if c in "*_`<&~" else c for c in escape_link_text(text))


def _label(path: str) -> str:
    escaped = escape_link_text(path)
    out = []
    for i, char in enumerate(escaped):
        intraword = (
            char == "_"
            and i > 0
            and i + 1 < len(escaped)
            and escaped[i - 1].isalnum()
            and escaped[i + 1].isalnum()
        )
        out.append(
            "\\" + char if char in "*`<~&" or (char == "_" and not intraword) else char
        )
    return "".join(out)


def _description(text: str) -> str:
    lead = "**Deprecation**"
    if text.startswith(lead) and (len(text) == len(lead) or text[len(lead)] in " :"):
        return lead + _literal(text[len(lead) :])
    return re.sub(r"^(\d+(?=[.)])|(?=[>#+-]))", r"\1\\", _literal(text))


def _unsafe(text: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Cs", "Zl", "Zp"} for c in text)


def _insert_preserving(body: str, updated: str, bullet: str, event_date: str) -> str:
    old_lines = body.splitlines(keepends=True)
    old = body.splitlines()
    new = updated.splitlines()
    visible = list(_unfenced_lines([*old, ""]))
    if not visible or visible[-1][0] != len(old):
        raise InvalidRequestError(_UNSAFE_LAYOUT)
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(old), len(new)) - prefix
        and old[-1 - suffix] == new[-1 - suffix]
    ):
        suffix += 1
    unmatched = old[prefix : len(old) - suffix if suffix else len(old)]
    if any(line.strip() for line in unmatched):
        raise InvalidRequestError(_UNSAFE_LAYOUT)
    prefix += len(unmatched)
    new_prefix = prefix - len(unmatched)
    inserted = new[new_prefix : len(new) - suffix if suffix else len(new)]
    eol = "\r\n" if "\r\n" in body else "\n"
    if (
        prefix
        and old_lines[prefix - 1]
        and not old_lines[prefix - 1].endswith(("\n", "\r"))
    ):
        old_lines[prefix - 1] += eol
    result = (
        "".join(old_lines[:prefix])
        + "".join(line + eol for line in inserted)
        + "".join(old_lines[prefix:])
    )
    bullet_index = prefix + inserted.index(bullet)
    tokens = MarkdownIt("commonmark").parse(result)
    has_item = any(
        t.type == "list_item_open" and t.map and t.map[0] == bullet_index
        for t in tokens
    )
    has_text = any(
        t.type == "inline"
        and t.map
        and t.map[0] == bullet_index
        and t.content == bullet[2:]
        and t.children
        and any(c.type == "link_open" for c in t.children)
        for t in tokens
    )
    headings = [
        (token.tag, tokens[i + 1].content)
        for i, token in enumerate(tokens[:-1])
        if token.type == "heading_open"
        and token.level == 0
        and token.tag in {"h1", "h2"}
        and token.map
        and token.map[0] < bullet_index
    ]
    if (
        not has_item
        or not has_text
        or not headings
        or headings[-1] != ("h2", event_date)
    ):
        raise InvalidRequestError(_UNSAFE_LAYOUT)
    return result


def _validate_input(
    paths: list[str],
    description: str,
    proposed_by: str | None,
    *,
    grouped: bool = False,
) -> None:
    if (
        not description.strip()
        or (
            len(description.encode("utf-8")) > 4096
            if grouped
            else len(description.strip()) > 500
        )
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


def _require_exact_case(
    source_dir: Path, path: str, names: dict[Path, set[str]]
) -> None:
    parent = source_dir
    for part in Path(path).parts:
        try:
            if parent not in names:
                with os.scandir(parent) as entries:
                    names[parent] = {
                        unicodedata.normalize("NFC", entry.name) for entry in entries
                    }
            found = unicodedata.normalize("NFC", part) in names[parent]
        except OSError as exc:
            raise DocumentUnreadableError(path, str(exc)) from exc
        if not found:
            raise InvalidRequestError(
                f"Concept path {path!r} must use its exact on-disk case; read it first."
            )
        parent = parent / part


def _canonical_paths(doc_mgr: DocumentManager, paths: list[str]) -> list[str]:
    canonical = []
    names: dict[Path, set[str]] = {}
    root = doc_mgr._source_dir.resolve()
    for path in paths:
        target = doc_mgr._validate_path(path).relative_to(root).as_posix()
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
        _require_exact_case(doc_mgr._source_dir, target, names)
        if target not in canonical:
            canonical.append(target)
    return canonical


def record_event(  # noqa: PLR0913, RUF100 - deterministic date override for events
    doc_mgr: DocumentManager,
    paths: list[str],
    description: str,
    policy: tuple[tuple[str, ...], Callable[[], dict[str, Any]]],
    proposed_by: str | None = None,
    *,
    today: date | None = None,
    grouped: bool = False,
) -> str:
    """Append one event and return its vault-relative log path.

    Args:
        doc_mgr: Document manager holding the shared write lock.
        paths: Existing concept paths covered by the event.
        description: Single-line explanation, at most 500 characters.
        policy: Designated folders and effective log capability callback.
        proposed_by: Optional proposal attribution.
        today: Optional event date.
        grouped: Accept group descriptions and already validated touched paths.

    Returns:
        Path of the log receiving the event.
    """
    _validate_input(paths, description, proposed_by, grouped=grouped)
    folders, capability_report = policy
    report = capability_report()
    if not report["effective"]:
        raise InvalidRequestError(
            f"OKF event logging is not active: {_INACTIVE.get(report['reason'], report['reason'])}."
        )
    doc_mgr.ensure_writable()
    with doc_mgr.write_scope():
        canonical = (
            [
                doc_mgr._validate_path(p)
                .relative_to(doc_mgr._source_dir.resolve())
                .as_posix()
                for p in paths
            ]
            if grouped
            else _canonical_paths(doc_mgr, paths)
        )
        common = os.path.commonpath([folder_of(p) for p in canonical])
        folder = log_anchor(common, folders, doc_mgr._source_dir)
        if destination_home(folder, "log.md", doc_mgr._source_dir) is None:
            raise InvalidRequestError(
                "The selected log is an alias; choose a safe log destination."
            )
        log_path = f"{folder}/log.md" if folder else "log.md"
        # Filesystem names are URL path data, not Markdown escapes or fragments.
        links = ", ".join(
            f"[{_label(p)}](/{build_plain_destination(quote(p, safe='/'))})"
            for p in canonical
        )
        summary = f"{_description(description.strip())} — {links}"
        if proposed_by is not None:
            summary += f" (proposed by {_literal(proposed_by.strip())})"
        raw = doc_mgr._read_snapshot(log_path)
        etag = compute_etag(raw.encode("utf-8")) if raw is not None else None
        body = stored_body(raw).decode("utf-8") if raw is not None else None
        event_date = (today or date.today()).isoformat()
        updated = append_okf_log_entry(body, date=event_date, summary=summary)
        updated = _insert_preserving(body or "", updated, f"- {summary}", event_date)
        with okf_write_suppressed():
            doc_mgr.replace_body(log_path, raw, updated, if_match=etag)
        return log_path


def record_group_events(
    doc_mgr: DocumentManager,
    paths: set[Path],
    description: str,
    policy: tuple[tuple[str, ...], Callable[[], dict[str, Any]]],
    today: date,
) -> list[Path]:
    """Record one group event per selected log, including removed note paths.

    Args:
        doc_mgr: Document manager for log writes.
        paths: Absolute paths touched by the group.
        description: Group description.
        policy: Designated folders and effective logging callback.
        today: Date captured when the group closed.

    Returns:
        Absolute paths of the logs written.
    """
    folders, report = policy
    if not report()["effective"]:
        return []
    root = doc_mgr._source_dir.resolve()
    grouped: dict[str, list[str]] = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if not relative.endswith(".md") or is_okf_reserved_file(relative):
            continue
        folder = log_anchor(folder_of(relative), folders, doc_mgr._source_dir)
        grouped.setdefault(folder, []).append(relative)
    logs = []
    for paths_for_log in grouped.values():
        log = record_event(
            doc_mgr, paths_for_log, description, policy, today=today, grouped=True
        )
        logs.append(root / log)
    return logs
