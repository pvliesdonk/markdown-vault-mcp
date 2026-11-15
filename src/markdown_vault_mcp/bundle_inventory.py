"""Bounded, index-independent observations of an explicitly selected bundle."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import yaml
from frontmatter.default_handlers import YAMLHandler

from markdown_vault_mcp.scanner import parse_frontmatter
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.content_kind import is_allowed_artifact, is_note
from markdown_vault_mcp.utils.fs import (
    is_subtree_excluded,
    open_directory_nofollow,
    read_regular_nofollow,
)
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

ScopeState = Literal["inspected", "missing", "excluded", "unavailable"]
EntryState = Literal["inspected", "missing", "not_inspected", "unavailable"]
Role = Literal[
    "navigation", "history", "concept_candidate", "supporting", "directory", "unknown"
]
ParseState = Literal["not_inspected", "valid", "invalid_encoding", "malformed_metadata"]
TypeState = Literal["not_inspected", "absent", "invalid", "present"]
Enumeration = Literal["complete", "entry_limit", "unavailable", "not_inspected"]


def _components(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or "\x00" in path or "\\" in path:
        raise ValueError("Scope must be a relative POSIX path")
    parts = tuple(path.split("/")) if path else ()
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Scope must contain only plain relative components")
    return parts


@dataclass(frozen=True)
class BundleInspectionRequest:
    """Select one immediate directory; the bundle root is always explicit.

    Attributes:
        root: Vault-relative bundle root; an explicit empty string selects the vault.
        directory: Directory relative to that root, empty for its immediate children.
        entry_limit: Maximum directory entries visited, including omitted entries (1-1000).
    """

    root: str
    directory: str = ""
    entry_limit: int = 100

    def __post_init__(self) -> None:
        _components(self.root)
        _components(self.directory)
        if type(self.entry_limit) is not int or not 1 <= self.entry_limit <= 1000:
            raise ValueError("entry_limit must be an integer between 1 and 1000")


@dataclass(frozen=True)
class BundleEntry:
    """A visible immediate child, without its body, metadata values or link target.

    Attributes:
        path: Original vault-relative spelling.
        role: Filename/physical role only, never conformance or write authority.
        state: Whether content was inspected, absent, refused or deliberately unread.
        reason: Stable reason for an uninspected or unavailable entry.
        parse_state: Outcome of decoding and parsing complete Markdown bytes.
        type_state: Presence of a nonempty string ``type`` field, not vocabulary validation.
    """

    path: str
    role: Role = "unknown"
    state: EntryState = "not_inspected"
    reason: str | None = None
    parse_state: ParseState = "not_inspected"
    type_state: TypeState = "not_inspected"


@dataclass(frozen=True)
class BundleInspection:
    """One bounded live observation; completeness never describes a whole bundle.

    Attributes:
        request: Explicit selected root and relative directory.
        state: Selected directory availability under current policy.
        enumeration: Completeness of immediate policy-visible enumeration only.
        entries: Sorted bounded observations; a capped subset has no stable ordering promise.
        search_state: Always ``not_evaluated``; parsing says nothing about admission.
        interpretation: Selection by the caller, without global OKF mode detection.
    """

    request: BundleInspectionRequest
    state: ScopeState
    enumeration: Enumeration = "not_inspected"
    entries: tuple[BundleEntry, ...] = ()
    search_state: Literal["not_evaluated"] = "not_evaluated"
    interpretation: Literal["caller_selected"] = "caller_selected"


def _missing(exc: OSError) -> bool:
    return exc.errno in {errno.ENOENT, errno.ENOTDIR}


def _role(name: str) -> Role:
    if name == "index.md":
        return "navigation"
    if name == "log.md":
        return "history"
    return "concept_candidate" if is_note(name) else "supporting"


def _metadata_too_complex(text: str) -> bool:
    """Bound YAML work before construction, using the existing delimiter handler."""
    handler = YAMLHandler()
    stripped = text.strip()
    if not handler.detect(stripped):
        return False
    try:
        header, _body = handler.split(stripped)
    except ValueError:
        # An unclosed block is ordinary text to the existing parser.
        return False
    depth = 0
    for ordinal, event in enumerate(yaml.parse(header, Loader=yaml.SafeLoader)):
        if ordinal >= 4096 or isinstance(event, yaml.events.AliasEvent):
            return True
        if isinstance(event, yaml.events.CollectionStartEvent):
            depth += 1
        elif isinstance(event, yaml.events.CollectionEndEvent):
            depth -= 1
        if depth > 32:
            return True
    return False


def _parsed(path: str, role: Role, raw: bytes) -> BundleEntry:
    try:
        text = decode_utf8(raw)
        if _metadata_too_complex(text):
            return BundleEntry(path, role, reason="metadata_complexity")
        metadata = parse_frontmatter(text).metadata
    except UnicodeDecodeError:
        return BundleEntry(path, role, "inspected", parse_state="invalid_encoding")
    except (RecursionError, OverflowError):
        return BundleEntry(path, role, reason="metadata_complexity")
    except (yaml.YAMLError, TypeError, ValueError, AttributeError, LookupError):
        # Known scalar/tag conversion failures describe the input, not I/O.
        return BundleEntry(path, role, "inspected", parse_state="malformed_metadata")
    except Exception:
        # Like scanner's internal_error, this is distinct from malformed input.
        return BundleEntry(path, role, "unavailable", reason="unexpected_parser_error")
    type_state: TypeState = "absent"
    if "type" in metadata:
        value = metadata["type"]
        type_state = (
            "present" if isinstance(value, str) and value.strip() else "invalid"
        )
    return BundleEntry(
        path, role, "inspected", parse_state="valid", type_state=type_state
    )


class BundleInspector:
    """Inspect only disk state using the document manager's effective policy.

    Args:
        source: Configured vault root.
        excludes: Effective vault-relative exclude patterns.
        extensions: Existing artifact allowlist.
        note_limit: Configured full-note byte cap (zero disables only that cap).
    """

    def __init__(
        self,
        source: Path,
        excludes: Sequence[str],
        extensions: frozenset[str],
        note_limit: int,
    ) -> None:
        self._source = source
        self._excludes = excludes
        self._extensions = extensions
        self._note_limit = min(65536, note_limit) if note_limit > 0 else 65536

    def _excluded(self, path: str) -> bool:
        return any(
            part.startswith(".") for part in path.split("/") if part
        ) or is_path_excluded(path, self._excludes)

    def inspect(self, request: BundleInspectionRequest) -> BundleInspection:
        """Observe one directory without reading excluded candidates or any index.

        Args:
            request: Explicit validated selection and entry bound.

        Returns:
            Bounded observations with independent scope and enumeration outcomes.
        """
        parts = _components(request.root) + _components(request.directory)
        # Subtree proofs apply to descendants; exact directory exclusions do not.
        scope = "/".join(parts)
        if self._excluded(scope) or is_subtree_excluded(scope, self._excludes):
            return BundleInspection(request, "excluded")
        try:
            with open_directory_nofollow(self._source, parts) as parent:
                return self._enumerate(parent, request, scope)
        except OSError as exc:
            return BundleInspection(
                request, "missing" if _missing(exc) else "unavailable"
            )

    def _enumerate(
        self, parent: int, request: BundleInspectionRequest, scope: str
    ) -> BundleInspection:
        entries: list[BundleEntry] = []
        remaining = 1024 * 1024
        enumeration: Enumeration = "complete"
        try:
            with os.scandir(parent) as children:
                for ordinal, child in enumerate(children):
                    if ordinal == request.entry_limit:
                        enumeration = "entry_limit"
                        break
                    path = f"{scope}/{child.name}" if scope else child.name
                    if self._excluded(path):
                        continue
                    entry, consumed = self._observe(parent, child.name, path, remaining)
                    remaining -= consumed
                    if entry is not None:
                        entries.append(entry)
        except OSError:
            enumeration = "unavailable"
        return BundleInspection(
            request,
            "inspected",
            enumeration,
            tuple(sorted(entries, key=lambda item: item.path)),
        )

    def _observe(
        self, parent: int, name: str, path: str, remaining: int
    ) -> tuple[BundleEntry | None, int]:
        try:
            mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
            return self._classify(parent, path, mode, remaining)
        except OSError as exc:
            if not self._visible_file(path):
                # It might be a visible directory; omit its unknown name but
                # refuse a complete enumeration when visibility is unknowable.
                raise
            state: EntryState = "missing" if _missing(exc) else "unavailable"
            return BundleEntry(path, state=state, reason="io_error"), 0

    def _visible_file(self, path: str) -> bool:
        return is_note(path) or is_allowed_artifact(path, self._extensions)

    def _classify(
        self, parent: int, path: str, mode: int, remaining: int
    ) -> tuple[BundleEntry | None, int]:
        if stat.S_ISDIR(mode):
            if is_subtree_excluded(path, self._excludes):
                return None, 0
            return BundleEntry(path, "directory", reason="directory"), 0
        if not self._visible_file(path):
            return None, 0
        if not stat.S_ISREG(mode):
            return BundleEntry(path, state="unavailable", reason="nonregular"), 0
        if not is_note(path):
            return BundleEntry(path, "supporting", reason="supporting"), 0
        return self._read_note(parent, path, remaining)

    def _read_note(
        self, parent: int, path: str, remaining: int
    ) -> tuple[BundleEntry, int]:
        name = path.rsplit("/", 1)[-1]
        role = _role(name)
        if remaining == 0:
            return BundleEntry(path, role, reason="byte_budget"), 0
        limit = min(self._note_limit + 1, remaining)
        try:
            raw = read_regular_nofollow(parent, name, limit)
        except OSError as exc:
            state: EntryState = "missing" if _missing(exc) else "unavailable"
            # Reserve the whole attempted read after errors: partial consumption
            # is unknowable, but must still count against the aggregate cap.
            return BundleEntry(path, role, state, "io_error"), limit
        # An exact aggregate-boundary read cannot prove EOF without another byte.
        if len(raw) > self._note_limit or len(raw) == remaining:
            reason = "note_limit" if len(raw) > self._note_limit else "byte_budget"
            return BundleEntry(path, role, reason=reason), len(raw)
        return _parsed(path, role, raw), len(raw)
