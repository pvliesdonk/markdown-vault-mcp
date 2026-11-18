"""Bounded, index-independent observations of an explicitly selected bundle."""

from __future__ import annotations

import errno
import os
import stat
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import yaml
from frontmatter.default_handlers import YAMLHandler

from markdown_vault_mcp.scanner import parse_frontmatter
from markdown_vault_mcp.stores._legacy import matrix_sequence
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.content_kind import is_allowed_artifact, is_note
from markdown_vault_mcp.utils.fs import (
    open_child_directory,
    open_directory_nofollow,
    read_regular_nofollow,
)
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

ScopeState = Literal["inspected", "missing", "unavailable"]
EntryState = Literal["inspected", "missing", "not_inspected", "unavailable"]
Role = Literal[
    "navigation",
    "history",
    "concept_candidate",
    "support",
    "supporting",
    "directory",
    "unknown",
]
ParseState = Literal["not_inspected", "valid", "invalid_encoding", "malformed_metadata"]
TypeState = Literal["not_inspected", "absent", "invalid", "present"]
SearchState = Literal[
    "not_evaluated",
    "searchable",
    "non_admitted",
    "unparseable",
    "index_excluded",
    "not_indexed_yet",
]
Enumeration = Literal["complete", "entry_limit", "unavailable", "not_inspected"]


def _components(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or "\x00" in path or "\\" in path:
        raise ValueError("Scope must be a relative POSIX path")
    parts = tuple(path.split("/")) if path else ()
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Scope must contain only plain relative components")
    return parts


@dataclass(frozen=True)
class InspectionPolicy:
    """Configured conventions filename and server-owned state locations.

    Args:
        conventions_file: Filename used for per-folder conventions, if enabled.
        state_paths: Change-tracker JSON files (exactly these paths, no sidecars)
            or directories holding server state.
        embeddings_path: Configured vector sidecar base, if any.
        index_path: SQLite keyword index; its ``.lock`` and journal files are owned.
    """

    conventions_file: str | None = "_conventions.md"
    state_paths: tuple[Path, ...] = ()
    embeddings_path: Path | None = None
    index_path: Path | None = None


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
class BundleTreeRequest(BundleInspectionRequest):
    """Select a subtree page, resuming after a vault-relative cursor path.

    Attributes:
        entry_limit: Maximum returned rows per page (1-1000), not names visited.
        cursor: Last returned path, strictly below the selected scope.
    """

    cursor: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.cursor is not None:
            scope = _components(self.root) + _components(self.directory)
            cursor = _components(self.cursor)
            if len(cursor) <= len(scope) or cursor[: len(scope)] != scope:
                raise ValueError("cursor must be strictly below the selected scope")


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
        visibility: Whether the path matches an effective index exclusion.
        hidden_path: Whether any vault-relative component begins with a dot.
        search_state: Last observed index admission, or not_evaluated.
    """

    path: str
    role: Role = "unknown"
    state: EntryState = "not_inspected"
    reason: str | None = None
    parse_state: ParseState = "not_inspected"
    type_state: TypeState = "not_inspected"
    visibility: Literal["visible", "index_excluded"] = "visible"
    hidden_path: bool = False
    search_state: SearchState = "not_evaluated"


@dataclass(frozen=True)
class BundleInspection:
    """One bounded live observation of a directory or subtree page.

    Attributes:
        request: Explicit selected root and relative directory.
        state: Selected directory availability under current policy.
        enumeration: Completeness of policy-visible enumeration.
        entries: Bounded observations; tree pages follow component order.
        search_state: Legacy scope field, always ``not_evaluated``; per-entry
            search results appear on each entry.
        interpretation: Selection by the caller, without global OKF mode detection.
        next_cursor: Last returned tree path when another page may remain.
    """

    request: BundleInspectionRequest
    state: ScopeState
    enumeration: Enumeration = "not_inspected"
    entries: tuple[BundleEntry, ...] = ()
    search_state: Literal["not_evaluated"] = "not_evaluated"
    interpretation: Literal["caller_selected"] = "caller_selected"
    next_cursor: str | None = None


def project_search_state(
    inspection: BundleInspection,
    ready: bool,
    indexed: Callable[[str], bool],
    tombstone: Callable[[str], str | None],
) -> BundleInspection:
    """Project existing index observations onto inspected Markdown entries.

    Args:
        inspection: Bounded live inventory.
        ready: Whether the index is queryable.
        indexed: Read-only live membership lookup.
        tombstone: Read-only lookup of the recorded skip category.

    Returns:
        Inventory annotated with search state; no index work is scheduled.
    """
    if not ready:
        return inspection
    entries = []
    for entry in inspection.entries:
        state: SearchState = "not_evaluated"
        safe = entry.state == "inspected" or (
            entry.state == "not_inspected"
            and entry.reason in {"note_limit", "byte_budget", "metadata_complexity"}
        )
        if safe and entry.role in {
            "navigation",
            "history",
            "concept_candidate",
            "support",
        }:
            if entry.visibility == "index_excluded":
                state = "index_excluded"
            elif indexed(entry.path):
                state = "searchable"
            else:
                category = tombstone(entry.path)
                if category == "missing_frontmatter":
                    state = "non_admitted"
                elif category in {"encoding_error", "parse_error"}:
                    state = "unparseable"
                elif category is None:
                    state = "not_indexed_yet"
        entries.append(replace(entry, search_state=state))
    return replace(inspection, entries=tuple(entries))


def _missing(exc: OSError) -> bool:
    return exc.errno in {errno.ENOENT, errno.ENOTDIR}


class _HiddenSymlinkError(OSError):
    """A hidden link has an unknowable target kind without following it."""


def _role(name: str, conventions_file: str | None) -> Role:
    if name == "index.md":
        return "navigation"
    if name == "log.md":
        return "history"
    if name == conventions_file:
        return "support"
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


def _same_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return False


def _normalized_name(name: str) -> str:
    return unicodedata.normalize("NFD", name).casefold()


def _vector_matrix(candidate: Path, base: Path) -> bool:
    name = _normalized_name(candidate.name)
    for prefix in (".m", ".v2.m"):
        stem = _normalized_name(base.with_suffix(prefix).name)
        if not (name.startswith(stem) and name.endswith(".npy")):
            continue
        sequence = matrix_sequence(name, stem)
        if sequence is not None and _same_file(
            candidate, base.with_suffix(f"{prefix}{sequence}.npy")
        ):
            return True
    return False


# ``{index}.lock`` (sqlite_keyword) and SQLite's own journal files; the JSON
# change tracker writes none of these, so tracker siblings stay readable.
_INDEX_SIDECARS = ("", "-wal", "-shm", "-journal", ".lock")


def _vector_sidecars(base: Path) -> set[Path]:
    paths = {
        base.with_suffix(suffix)
        for suffix in (".lock", ".npy", ".json", ".v2.json", ".sqlite3")
    }
    sqlite = base.with_suffix(".sqlite3")
    paths.update(
        sqlite.with_name(sqlite.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )
    return paths


class BundleInspector:
    """Inspect only disk state using the document manager's effective policy.

    Args:
        source: Configured vault root.
        excludes: Effective vault-relative exclude patterns.
        extensions: Existing artifact allowlist.
        note_limit: Configured full-note byte cap (zero disables only that cap).
        policy: Conventions and server state boundaries for inspection.
    """

    def __init__(
        self,
        source: Path,
        excludes: Sequence[str],
        extensions: frozenset[str],
        note_limit: int,
        policy: InspectionPolicy = InspectionPolicy(),
    ) -> None:
        self._source = source
        self._policy = policy
        self._excludes = excludes
        self._extensions = extensions
        self._note_limit = min(65536, note_limit) if note_limit > 0 else 65536

    def _prepare_policy(self, root: Path) -> None:
        self._absolute_source = root
        policy = self._policy
        configured: list[tuple[Path, tuple[str, ...]]] = [
            (path, ("",)) for path in policy.state_paths
        ]
        if policy.index_path is not None:
            configured.append((policy.index_path, _INDEX_SIDECARS))
        files: set[Path] = set()
        directories: set[Path] = set()
        unavailable: set[Path] = set()
        for path, sidecars in configured:
            owned, protected, unstatable = self._state_boundary(path, root, sidecars)
            files |= owned
            directories |= protected
            unavailable |= unstatable
        self._vector_bases = (
            set(self._policy_spellings(policy.embeddings_path, root))
            if policy.embeddings_path is not None
            else set()
        )
        for base in self._vector_bases:
            files.update(_vector_sidecars(base))
        self._unavailable_policy_paths = unavailable
        self._internal_paths = files
        self._internal_directories = directories

    def _state_boundary(
        self, configured: Path, root: Path, sidecars: tuple[str, ...]
    ) -> tuple[set[Path], set[Path], set[Path]]:
        """Return the owned files, protected directories and unstatable paths."""
        if str(configured) == ":memory:":
            return set(), set(), set()
        path, spelled = self._policy_spellings(configured, root)
        names = {
            p.with_name(p.name + suffix) for p in (path, spelled) for suffix in sidecars
        }
        try:
            mode = path.stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            mode = 0
        except OSError:
            return names, {path, spelled}, {path, spelled}
        if stat.S_ISDIR(mode):
            return set(), {path, spelled}, set()
        return names, set(), set()

    def _policy_spellings(self, configured: Path, root: Path) -> tuple[Path, Path]:
        try:
            return configured.resolve(), configured.parent.resolve() / configured.name
        except (OSError, RuntimeError):
            # Resolve the nearest reachable ancestor without following the failed path.
            absolute = type(configured)(os.path.abspath(configured))  # noqa: PTH100 - lexical fallback must not resolve
            ancestor = absolute.parent
            while ancestor != ancestor.parent:
                try:
                    resolved = ancestor.resolve(strict=True)
                    candidate = resolved / absolute.relative_to(ancestor)
                    return candidate, candidate
                except (OSError, RuntimeError):
                    ancestor = ancestor.parent
            source = type(configured)(os.path.abspath(self._source))  # noqa: PTH100 - lexical fallback must not resolve
            if absolute.is_relative_to(source):
                absolute = root / absolute.relative_to(source)
            return absolute, absolute

    def _internal(self, path: str) -> bool:
        parts = path.split("/")
        if any(part in {".git", ".markdown_vault_mcp"} for part in parts):
            return True
        candidate = self._absolute_source.joinpath(*parts)
        for internal_name in (".git", ".markdown_vault_mcp"):
            if _normalized_name(candidate.name) == internal_name and _same_file(
                candidate, candidate.parent / internal_name
            ):
                return True
        if candidate in self._internal_paths or any(
            candidate == internal or candidate.is_relative_to(internal)
            for internal in self._internal_directories
        ):
            return True
        if any(
            _same_file(candidate, owned)
            for owned in self._internal_paths | self._internal_directories
        ):
            return True
        return any(_vector_matrix(candidate, base) for base in self._vector_bases)

    def inspect(self, request: BundleInspectionRequest) -> BundleInspection:
        """Observe one directory independently of index admission.

        Args:
            request: Explicit validated selection and entry bound.

        Returns:
            Bounded observations with independent scope and enumeration outcomes.
        """
        parts = _components(request.root) + _components(request.directory)
        scope = "/".join(parts)
        try:
            root = self._source.resolve(strict=True)
            self._prepare_policy(root)
        except (OSError, RuntimeError):
            return BundleInspection(request, "unavailable")
        try:
            if any(
                self._internal("/".join(parts[:i])) for i in range(0, len(parts) + 1)
            ):
                return BundleInspection(request, "unavailable")
            with open_directory_nofollow(root, parts, source_resolved=True) as parent:
                if isinstance(request, BundleTreeRequest):
                    return self._walk_page(parent, request, scope)
                return self._enumerate(parent, request, scope)
        except OSError as exc:
            return BundleInspection(
                request, "missing" if _missing(exc) else "unavailable"
            )

    def _walk_page(
        self, parent: int, request: BundleTreeRequest, scope: str
    ) -> BundleInspection:
        walker = _TreeWalk(self, request)
        walker.walk(parent, scope, 1)
        if walker.stopped and walker.enumeration == "complete":
            walker.enumeration = "entry_limit"
        return BundleInspection(
            request,
            "inspected",
            walker.enumeration,
            tuple(walker.entries),
            next_cursor=walker.entries[-1].path
            if walker.stopped and walker.entries
            else None,
        )

    def _labelled(self, entry: BundleEntry, path: str) -> BundleEntry:
        return replace(
            entry,
            visibility="index_excluded"
            if is_path_excluded(path, self._excludes)
            else "visible",
            hidden_path=any(part.startswith(".") for part in path.split("/")),
        )

    def _enumerate(
        self, parent: int, request: BundleInspectionRequest, scope: str
    ) -> BundleInspection:
        entries: list[BundleEntry] = []
        remaining = 1024 * 1024
        selected = self._absolute_source.joinpath(*scope.split("/"))
        enumeration: Enumeration = (
            "unavailable"
            if any(path.parent == selected for path in self._unavailable_policy_paths)
            else "complete"
        )
        try:
            with os.scandir(parent) as children:
                for ordinal, child in enumerate(children):
                    if ordinal == request.entry_limit:
                        if enumeration == "complete":
                            enumeration = "entry_limit"
                        break
                    path = f"{scope}/{child.name}" if scope else child.name
                    if self._internal(path):
                        continue
                    try:
                        entry, consumed = self._observe(
                            parent, child.name, path, remaining
                        )
                    except OSError as exc:
                        if not isinstance(
                            exc, _HiddenSymlinkError
                        ) and exc.errno not in {
                            errno.EACCES,
                            errno.EPERM,
                        }:
                            raise
                        enumeration = "unavailable"
                        continue
                    remaining -= consumed
                    if entry is not None:
                        entries.append(self._labelled(entry, path))
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
            if exc.errno in {errno.EACCES, errno.EPERM}:
                raise
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
        if stat.S_ISLNK(mode):
            visible = self._visible_file(path) or "." not in path.rsplit("/", 1)[-1]
            if not visible:
                raise _HiddenSymlinkError()
            return BundleEntry(path, state="unavailable", reason="symlink"), 0
        if stat.S_ISDIR(mode):
            name = path.rsplit("/", 1)[-1]
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            os.close(descriptor)
            return BundleEntry(path, "directory", reason="directory"), 0
        if not self._visible_file(path):
            return None, 0
        if not stat.S_ISREG(mode):
            return BundleEntry(path, state="unavailable", reason="nonregular"), 0
        if not is_note(path):
            read_regular_nofollow(parent, path.rsplit("/", 1)[-1], 0)
            return BundleEntry(path, "supporting", reason="supporting"), 0
        return self._read_note(parent, path, remaining)

    def _read_note(
        self, parent: int, path: str, remaining: int
    ) -> tuple[BundleEntry, int]:
        name = path.rsplit("/", 1)[-1]
        role = _role(name, self._policy.conventions_file)
        if remaining == 0:
            read_regular_nofollow(parent, name, 0)
            return BundleEntry(path, role, reason="byte_budget"), 0
        limit = min(self._note_limit + 1, remaining)
        try:
            raw = read_regular_nofollow(parent, name, limit)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EPERM}:
                raise
            state: EntryState = "missing" if _missing(exc) else "unavailable"
            # Reserve the whole attempted read after errors: partial consumption
            # is unknowable, but must still count against the aggregate cap.
            return BundleEntry(path, role, state, "io_error"), limit
        # An exact aggregate-boundary read cannot prove EOF without another byte.
        if len(raw) > self._note_limit or len(raw) == remaining:
            reason = "note_limit" if len(raw) > self._note_limit else "byte_budget"
            return BundleEntry(path, role, reason=reason), len(raw)
        return _parsed(path, role, raw), len(raw)


class _TreeWalk:
    """Hold page state while traversing descriptor-relative directories."""

    def __init__(self, inspector: BundleInspector, request: BundleTreeRequest) -> None:
        self.inspector = inspector
        self.request = request
        self.cursor = _components(request.cursor) if request.cursor is not None else ()
        self.entries: list[BundleEntry] = []
        self.remaining = 1024 * 1024
        self.enumeration: Enumeration = "complete"
        self.stopped = False

    def walk(self, fd: int, directory: str, depth: int) -> None:
        selected = self.inspector._absolute_source.joinpath(*_components(directory))
        if any(
            path.parent == selected for path in self.inspector._unavailable_policy_paths
        ):
            self.enumeration = "unavailable"
        try:
            with os.scandir(fd) as children:
                names = sorted(child.name for child in children)
        except OSError:
            self.enumeration = "unavailable"
            return
        directory_parts = _components(directory)
        if self.cursor[: len(directory_parts)] == directory_parts and len(
            self.cursor
        ) > len(directory_parts):
            names = names[bisect_left(names, self.cursor[len(directory_parts)]) :]
        for name in names:
            if self.stopped:
                return
            if "\\" in name:
                self.enumeration = "unavailable"
                continue
            path = f"{directory}/{name}" if directory else name
            if not self.inspector._internal(path):
                self.visit(fd, path, depth)

    def visit(self, fd: int, path: str, depth: int) -> None:
        parts = _components(path)
        before = bool(self.cursor) and parts <= self.cursor
        if before:
            if parts != self.cursor or self._cursor_is_directory(fd, parts[-1]):
                self.descend(fd, path, depth, resume=True)
            return
        if (
            self.entries
            and is_note(path)
            and self.remaining < self.inspector._note_limit + 1
        ):
            self.stopped = True
            return
        try:
            entry, consumed = self.inspector._observe(
                fd, parts[-1], path, self.remaining
            )
        except OSError:
            self.enumeration = "unavailable"
            return
        self.remaining -= consumed
        if entry is None:
            return
        if entry.role == "directory" and depth >= 32:
            entry = replace(entry, state="unavailable", reason="depth_limit")
            self.enumeration = "unavailable"
        self.entries.append(self.inspector._labelled(entry, path))
        if len(self.entries) == self.request.entry_limit:
            self.stopped = True
        elif entry.role == "directory" and entry.reason == "directory":
            self.descend(fd, path, depth, resume=False)

    def _cursor_is_directory(self, fd: int, name: str) -> bool:
        try:
            return stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                self.enumeration = "unavailable"
            return False

    def descend(self, fd: int, path: str, depth: int, *, resume: bool) -> None:
        try:
            child = open_child_directory(fd, path.rsplit("/", 1)[-1])
        except OSError as exc:
            if not resume or exc.errno != errno.ENOENT:
                self.enumeration = "unavailable"
            return
        try:
            if depth >= 32:
                self.enumeration = "unavailable"
            else:
                self.walk(child, path, depth + 1)
        finally:
            os.close(child)
