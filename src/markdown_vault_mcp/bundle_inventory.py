"""Bounded bundle observations with an optional index-state projection."""

from __future__ import annotations

import codecs
import errno
import hashlib
import os
import stat
import sys
import unicodedata
from bisect import bisect_left
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import frontmatter
import yaml
from frontmatter.default_handlers import YAMLHandler

from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block
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
    from typing import BinaryIO

    from markdown_vault_mcp.git.query import GitExportSnapshot

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
RemovalHistory = Literal["not_inspected", "available", "unavailable"]


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
        snapshot: Optional committed tree supplying the inventory.
    """

    cursor: str | None = None
    snapshot: GitExportSnapshot | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.cursor is not None:
            scope = _components(self.root) + _components(self.directory)
            cursor = _components(self.cursor)
            if len(cursor) <= len(scope) or cursor[: len(scope)] != scope:
                raise ValueError("cursor must be strictly below the selected scope")


@dataclass(frozen=True)
class BundleChildrenRequest(BundleTreeRequest):
    """Select immediate children by cursor without inspecting note metadata.

    Notes retain filename roles and report uninspected metadata with the
    ``byte_budget`` reason. Access and internal-path policies still apply.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.cursor is not None:
            scope = _components(self.root) + _components(self.directory)
            if len(_components(self.cursor)) != len(scope) + 1:
                raise ValueError("cursor must name an immediate child")


@dataclass(frozen=True)
class BundleEntry:
    """A visible immediate child, without its body, metadata values or link target.

    Attributes:
        path: Original vault-relative spelling.
        role: Filename/physical role only, never conformance or write authority.
        state: Whether content was inspected, absent, refused or deliberately unread.
        reason: Stable reason for an uninspected or unavailable entry, including
            ``hidden_symlink`` for a link outside the file allowlist.
        parse_state: Outcome of decoding and parsing complete Markdown bytes.
        type_state: Presence of a nonempty string ``type`` field, not vocabulary validation.
        visibility: Whether the path matches an effective index exclusion.
        hidden_path: Whether any vault-relative component begins with a dot.
        search_state: Last observed index admission, or not_evaluated.
        presence: Whether a locally observed path is present or removed.
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
    presence: Literal["present", "removed"] = "present"


@dataclass(frozen=True)
class BundleInspection:
    """One bounded observation of a directory or subtree page.

    Attributes:
        request: Explicit selected root and relative directory.
        state: Selected directory availability under current policy.
        enumeration: Completeness of policy-visible enumeration.
        entries: Bounded observations; live tree pages follow component order,
            snapshot pages follow sorted full-path string order.
        search_state: Legacy scope field, always ``not_evaluated``; per-entry
            search results appear on each entry.
        interpretation: Selection by the caller, without global OKF mode detection.
        next_cursor: Last returned tree path when another page may remain.
        uncommitted: Policy-visible changed paths observed for a Git snapshot.

        removal_history: Ledger availability; set only by okf_inventory.
    """

    request: BundleInspectionRequest
    state: ScopeState
    enumeration: Enumeration = "not_inspected"
    entries: tuple[BundleEntry, ...] = ()
    search_state: Literal["not_evaluated"] = "not_evaluated"
    interpretation: Literal["caller_selected"] = "caller_selected"
    next_cursor: str | None = None
    uncommitted: tuple[str, ...] = ()

    removal_history: RemovalHistory = "not_inspected"


_SKIP_STATE: dict[str, SearchState] = {
    "missing_frontmatter": "non_admitted",
    "parse_error": "unparseable",
    "encoding_error": "unparseable",
    "internal_error": "not_evaluated",
}


def _search_state(
    entry: BundleEntry, row_state: Callable[[str], tuple[bool, str | None]]
) -> SearchState:
    if entry.state != "inspected" and not (
        entry.state == "not_inspected"
        and entry.reason in {"note_limit", "byte_budget", "metadata_complexity"}
    ):
        return "not_evaluated"
    exists, category = row_state(entry.path)
    if exists and category is None:
        return "searchable"
    if exists:
        return _SKIP_STATE.get(category or "", "not_evaluated")
    return (
        "index_excluded" if entry.visibility == "index_excluded" else "not_indexed_yet"
    )


def project_search_state(
    inspection: BundleInspection, row_state: Callable[[str], tuple[bool, str | None]]
) -> BundleInspection:
    """Project read-only index observations onto safely observed entries.

    Args:
        inspection: Bounded live inventory.
        row_state: Single-read index disposition lookup.

    Returns:
        Inventory annotated with search state; no index work is scheduled.
    """
    return replace(
        inspection,
        entries=tuple(
            replace(entry, search_state=_search_state(entry, row_state))
            for entry in inspection.entries
        ),
    )


def _missing(exc: OSError) -> bool:
    return exc.errno in {errno.ENOENT, errno.ENOTDIR}


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


def _snapshot_note(
    snapshot: GitExportSnapshot, path: str, role: Role, note_limit: int, remaining: int
) -> tuple[BundleEntry, int]:
    size = snapshot.size(path)
    if size > note_limit:
        consumed = min(size, note_limit + 1, remaining)
        return BundleEntry(path, role, reason="note_limit"), consumed
    try:
        raw = snapshot.read(path)
        return _parsed(path, role, raw), len(raw)
    except ValueError:
        from markdown_vault_mcp.git.query import is_lfs_pointer

        if not is_lfs_pointer(snapshot._read_object(path)):
            raise
        return BundleEntry(path, role, reason="lfs_unavailable"), 0


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


def _possible_header(prefix: bytearray, *, eof: bool) -> bool:
    """Reject a first line that cannot become a supported opener."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="surrogateescape")
    text = decoder.decode(prefix, final=eof).lstrip()
    if not text:
        return not eof
    line, separator, _ = text.partition("\n")
    if frontmatter.detect_format(line, frontmatter.handlers) is not None:
        return True
    if separator or eof or line != line.rstrip():
        return False
    return any(
        delimiter
        and (
            delimiter.startswith(line)
            or (set(delimiter) == {"-"} and set(line) == {"-"})
        )
        for handler in frontmatter.handlers
        if (delimiter := handler.START_DELIMITER)
    )


def _observation_digests(stream: BinaryIO) -> tuple[str, str | None]:
    """Hash all bytes and the frontmatter block, buffering only a candidate header."""
    content = hashlib.sha256()
    prefix = bytearray()
    size = 65536
    header_digest = None
    while True:
        while len(prefix) < size:
            chunk = stream.read(min(65536, size - len(prefix)))
            if not chunk:
                break
            prefix.extend(chunk)
            content.update(chunk)
        eof = len(prefix) < size
        if not _possible_header(prefix, eof=eof):
            break
        cut = bytes(prefix) if eof else bytes(prefix[: prefix.rfind(b"\n") + 1])
        text = cut.decode("utf-8-sig", errors="surrogateescape")
        body = strip_frontmatter_block(text)
        if body != text:
            header = text[: len(text) - len(body)].strip()
            with suppress(UnicodeEncodeError):
                header_digest = hashlib.sha256(header.encode("utf-8")).hexdigest()
            break
        if eof:
            break
        size *= 2
    while chunk := stream.read(65536):
        content.update(chunk)
    return content.hexdigest(), header_digest


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

    @staticmethod
    def _check_snapshot_access(
        root: Path,
        parts: tuple[str, ...],
        limit: int = 0,
        *,
        allow_symlink: bool = False,
    ) -> bytes | None:
        """Check existing components without following symlinks; absent is allowed."""
        try:
            with open_directory_nofollow(
                root, parts[:-1], source_resolved=True
            ) as parent:
                mode = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False).st_mode
                if allow_symlink and stat.S_ISLNK(mode):
                    return None
                if not stat.S_ISREG(mode):
                    reason = "symlink" if stat.S_ISLNK(mode) else "nonregular"
                    raise OSError(errno.ENOTSUP, reason)
                return read_regular_nofollow(parent, parts[-1], limit)
        except FileNotFoundError:
            # A removed directory has no live permissions to bypass; its nearest
            # existing ancestors were opened without following links.
            return None

    def inspect(self, request: BundleInspectionRequest) -> BundleInspection:
        """Observe a directory or snapshot subtree under current policy.

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
            if isinstance(request, BundleTreeRequest) and request.snapshot is not None:
                return self._snapshot_page(request, scope, request.snapshot)
            with open_directory_nofollow(root, parts, source_resolved=True) as parent:
                if isinstance(request, BundleTreeRequest):
                    return self._walk_page(parent, request, scope)
                return self._enumerate(parent, request, scope)
        except OSError as exc:
            return BundleInspection(
                request, "missing" if _missing(exc) else "unavailable"
            )

    def _snapshot_page(
        self, request: BundleTreeRequest, scope: str, snapshot: GitExportSnapshot
    ) -> BundleInspection:
        if scope and not any(path.startswith(scope + "/") for path in snapshot.entries):
            with open_directory_nofollow(
                self._absolute_source, _components(scope), source_resolved=True
            ):
                pass
        entries: list[BundleEntry] = []
        remaining = 1024 * 1024
        selected = self._absolute_source.joinpath(*_components(scope))
        if any(
            path.parent.is_relative_to(selected)
            for path in self._unavailable_policy_paths
        ):
            return BundleInspection(request, "inspected", "unavailable")
        enumeration: Enumeration = "complete"
        for path, (mode, _) in snapshot.ordered:
            if scope and not path.startswith(scope + "/"):
                continue
            if request.cursor is not None and path <= request.cursor:
                continue
            if entries and is_note(path) and remaining < self._note_limit + 1:
                enumeration = "entry_limit"
                break
            entry, consumed = self._snapshot_entry(snapshot, path, mode, remaining)
            if entry is None:
                continue
            remaining -= consumed
            entries.append(self._labelled(entry, path))
            if len(entries) == request.entry_limit:
                enumeration = "entry_limit"
                break
        return BundleInspection(
            request,
            "inspected",
            enumeration,
            tuple(entries),
            next_cursor=entries[-1].path
            if entries and enumeration == "entry_limit"
            else None,
            uncommitted=self._snapshot_dirty(snapshot, scope),
        )

    def _snapshot_entry(
        self, snapshot: GitExportSnapshot, path: str, mode: str, remaining: int
    ) -> tuple[BundleEntry | None, int]:
        parts = _components(path)
        if any(self._internal("/".join(parts[:i])) for i in range(1, len(parts) + 1)):
            return None, 0
        if len(parts) > 32:
            raise OSError("Snapshot traversal depth exceeded")
        if mode not in {"100644", "100755"}:
            reason = "submodule" if mode == "160000" else "symlink"
            return BundleEntry(path, state="unavailable", reason=reason), 0
        if not self._visible_file(path):
            return None, 0
        try:
            self._check_snapshot_access(self._absolute_source, parts)
        except OSError as exc:
            if exc.errno != errno.ENOTSUP:
                raise
            return BundleEntry(path, state="unavailable", reason=exc.strerror), 0
        if not is_note(path):
            return BundleEntry(path, "supporting", reason="supporting"), 0
        role = _role(parts[-1], self._policy.conventions_file)
        return _snapshot_note(snapshot, path, role, self._note_limit, remaining)

    def _flagged_clean(
        self, snapshot: GitExportSnapshot, path: str, raw: bytes | None
    ) -> bool:
        """Whether an index-flagged path matches its committed blob.

        Raises:
            ValueError: If Git cannot normalize the working bytes; the path is
                then neither clean nor changed, so the export refuses.
        """
        from markdown_vault_mcp.git.query import _git_snapshot_hash

        if raw is None:
            return snapshot.flagged[path]
        digest = _git_snapshot_hash(snapshot, self._absolute_source, path, raw)
        return digest == snapshot.entries[path][1]

    # Scope, policy, and no-follow checks apply to files and reported directories.
    def _snapshot_dirty(  # noqa: C901, RUF100
        self, snapshot: GitExportSnapshot, scope: str
    ) -> tuple[str, ...]:
        visible = []
        for path in sorted(snapshot.changed | snapshot.flagged.keys()):
            if scope and not (
                path.startswith(scope + "/")
                or (path.endswith("/") and scope.startswith(path))
            ):
                continue
            try:
                parts = _components(path.rstrip("/"))
            except ValueError:
                continue
            try:
                if (not path.endswith("/") and not self._visible_file(path)) or any(
                    self._internal("/".join(parts[:i]))
                    for i in range(1, len(parts) + 1)
                ):
                    continue
                if path.endswith("/"):
                    with open_directory_nofollow(
                        self._absolute_source, parts, source_resolved=True
                    ):
                        pass
                elif path in snapshot.changed:
                    self._check_snapshot_access(
                        self._absolute_source, parts, allow_symlink=True
                    )
                elif path in snapshot.entries:
                    raw = self._check_snapshot_access(
                        self._absolute_source, parts, sys.maxsize
                    )
                    if self._flagged_clean(snapshot, path, raw):
                        continue
                else:
                    continue
            except OSError:
                continue
            visible.append(
                scope + "/" if path.endswith("/") and scope.startswith(path) else path
            )
        return tuple(sorted(set(visible)))

    def observe_files(
        self, visit: Callable[[str, Role, str, str | None], None]
    ) -> set[str]:
        """Visit readable Markdown files and return paths with unknown visibility.

        Args:
            visit: Consume each path, role, content digest and header digest.

        Returns:
            Blocked paths or subtrees; an empty string denotes the root.
        """
        blocked: set[str] = set()
        try:
            root = self._source.resolve(strict=True)
            self._prepare_policy(root)
            if self._internal(""):
                return {""}
            with open_directory_nofollow(root, (), source_resolved=True) as fd:
                self._observe_tree(fd, "", visit, blocked)
        except (OSError, RuntimeError):
            blocked.add("")
        return blocked

    def observe_path(
        self, path: str, visit: Callable[[str, Role, str, str | None], None]
    ) -> str:
        """Observe a single inventory path, returning present, missing or blocked.

        Args:
            path: Vault-relative Markdown path.
            visit: Consumer of the observed digests.

        Returns:
            The visibility outcome; blocked paths cannot prove absence.
        """
        return self._probe_path(
            path,
            lambda fd, blocked: self._observe_note(fd, path, visit, blocked),
        )

    def path_presence(self, path: str) -> str:
        """Report whether a path is present, missing or blocked, reading no content.

        Args:
            path: Vault-relative Markdown path.

        Returns:
            The visibility outcome; blocked paths cannot prove absence.
        """
        return self._probe_path(
            path,
            lambda fd, _blocked: read_regular_nofollow(fd, path.rsplit("/", 1)[-1], 0),
        )

    def _probe_path(self, path: str, observe: Callable[[int, set[str]], object]) -> str:
        try:
            root = self._source.resolve(strict=True)
        except (OSError, RuntimeError):
            return "blocked"
        try:
            parts = _components(path)
            if not parts or not is_note(path):
                raise ValueError("Not an observation path")
            self._prepare_policy(root)
            if any(self._internal("/".join(parts[:i])) for i in range(len(parts) + 1)):
                return "blocked"
            blocked: set[str] = set()
            with open_directory_nofollow(root, parts[:-1], source_resolved=True) as fd:
                observe(fd, blocked)
            return "blocked" if blocked else "present"
        except OSError as exc:
            return "missing" if _missing(exc) else "blocked"
        except (ValueError, RuntimeError):
            return "blocked"

    def _observe_tree(
        self,
        fd: int,
        directory: str,
        visit: Callable[[str, Role, str, str | None], None],
        blocked: set[str],
    ) -> None:
        try:
            with os.scandir(fd) as children:
                names = [child.name for child in children]
        except OSError:
            blocked.add(directory)
            return
        for name in names:
            path = f"{directory}/{name}" if directory else name
            if "\\" in name:
                blocked.add(path)
            elif not self._internal(path):
                try:
                    self._observe_child(fd, path, visit, blocked)
                except (OSError, RecursionError):
                    blocked.add(path)

    def _observe_child(
        self,
        fd: int,
        path: str,
        visit: Callable[[str, Role, str, str | None], None],
        blocked: set[str],
    ) -> None:
        name = path.rsplit("/", 1)[-1]
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = open_child_directory(fd, name)
            try:
                self._observe_tree(child, path, visit, blocked)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and is_note(path):
            self._observe_note(fd, path, visit, blocked)
        elif stat.S_ISLNK(info.st_mode) or is_note(path):
            blocked.add(path)

    def _observe_note(
        self,
        fd: int,
        path: str,
        visit: Callable[[str, Role, str, str | None], None],
        blocked: set[str],
    ) -> None:
        name = path.rsplit("/", 1)[-1]
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                blocked.add(path)
                return
            digest, frontmatter = _observation_digests(stream)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            blocked.add(path)
            return
        visit(path, _role(name, self._policy.conventions_file), digest, frontmatter)

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
                        if exc.errno not in {
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
            reason = "symlink" if visible else "hidden_symlink"
            return BundleEntry(path, state="unavailable", reason=reason), 0
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
        self.children_only = isinstance(request, BundleChildrenRequest)
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
                fd, parts[-1], path, 0 if self.children_only else self.remaining
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
        if self.children_only:
            return
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
