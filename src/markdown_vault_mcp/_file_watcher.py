"""Filesystem-event watcher for external file changes (issue #558).

Monitors ``source_dir`` with watchdog and coalesces search and observation
events after a quiet debounce window. Used when neither the git periodic-pull loop nor a
push webhook drives reindexing — those mechanisms already trigger reindex
on their own cadence, and mixing a file watcher with git checkout would
cause redundant reindexes and mid-checkout partial scans.

Only mounted when ``MARKDOWN_VAULT_MCP_FILE_WATCHER=true`` (default) AND
git pull is disabled (``GIT_PULL_INTERVAL_S=0`` or not set) AND no webhook
can actually deliver — credentials set *and* an HTTP/SSE transport to mount
their route on, which is what
:func:`markdown_vault_mcp._webhooks.webhook_routes_active` answers (#1263).

Subscription semantics and the dependency floor are recorded in
``docs/design/reference/watchdog-event-filtering.md``.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.bundle_inventory import INTERNAL_DIRECTORY_NAMES
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.content_kind import is_note
from markdown_vault_mcp.utils.fs import (
    _dir_prune_rules,
    _should_prune_dir,
    is_directory,
    is_subtree_excluded,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

try:
    import watchdog.events as _watchdog_events
    from watchdog.events import (
        EVENT_TYPE_CREATED,
        EVENT_TYPE_DELETED,
        EVENT_TYPE_MODIFIED,
        EVENT_TYPE_MOVED,
        DirCreatedEvent,
        DirDeletedEvent,
        DirModifiedEvent,
        DirMovedEvent,
        FileCreatedEvent,
        FileDeletedEvent,
        FileModifiedEvent,
        FileMovedEvent,
        FileSystemEvent,
        FileSystemEventHandler,
    )
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True

    # Filter before native queueing (#2045). DirModifiedEvent retains
    # IN_CLOSE_WRITE; adding FileClosedEvent would also request read-close
    # notifications on watchdog 4.x.
    _MUTATING_EVENT_CLASSES: list[type[FileSystemEvent]] = [
        FileCreatedEvent,
        DirCreatedEvent,
        FileModifiedEvent,
        DirModifiedEvent,
        FileMovedEvent,
        DirMovedEvent,
        FileDeletedEvent,
        DirDeletedEvent,
    ]

    # Attribute classes were added after watchdog 4.x. Keep the minimum version.
    _ATTRIBUTE_EVENT_CLASSES = [
        cls
        for name in ("FileAttribEvent", "DirAttribEvent")
        if (cls := getattr(_watchdog_events, name, None)) is not None
    ]

    # Only these event types represent an actual mutation of the vault.
    # Read-only events (``opened``, ``closed``, ``closed_no_write``, access)
    # must be ignored: ``reindex()`` walks the whole vault read-only, and on
    # Linux that walk emits inotify read events (OPEN / CLOSE_NOWRITE, also on
    # directories) for every entry it touches. Forwarding those to the
    # debounce scheduler would make each reindex re-arm the watcher, which
    # fires another reindex — an endless self-feedback loop that pins the CPU
    # with no real file changes (#720).
    _MUTATING_EVENT_TYPES = frozenset(
        {
            EVENT_TYPE_CREATED,
            EVENT_TYPE_MODIFIED,
            EVENT_TYPE_MOVED,
            EVENT_TYPE_DELETED,
        }
    )

    # The inotify emitter queues DirModifiedEvent(parent) directly after each of
    # these, so only they can leave a derived parent notice pending.
    _PARENT_NOTICE_EVENT_TYPES = frozenset(
        {EVENT_TYPE_CREATED, EVENT_TYPE_DELETED, EVENT_TYPE_MOVED}
    )
except ImportError:
    _WATCHDOG_AVAILABLE = False


def _has_hidden_component(parts: tuple[str, ...]) -> bool:
    """Return True when *parts* should be ignored.

    Two cases:

    - Empty tuple: the ``DirModifiedEvent`` watchdog fires on ``source_dir``
      itself whenever any child is added or deleted.  Filtering it here is
      safe because the concrete file events (``FileCreatedEvent``, etc.) that
      accompany it carry the actual path and will be evaluated separately.
    - Any component starts with a dot: hidden directories such as ``.git/``
      or ``.markdown_vault_mcp/``.

    The argument is ``rel.parts`` from a platform-native ``Path`` object so
    that both POSIX (``/``) and Windows (``\\``) separators are handled
    correctly without an extra string-to-``PurePosixPath`` round-trip.
    """
    if not parts:
        return True
    return any(part.startswith(".") for part in parts)


def _is_hidden_relative_to_root(abs_path: str, watch_root: Path) -> bool:
    """Return True when *abs_path* should be ignored under *watch_root*.

    Hiddenness is evaluated *relative to the watch root the event was delivered
    under*, not relative to ``source_dir``. A deliberately-watched dot-root such
    as ``.claude`` delivers its non-dot descendants (the remainder carries no dot
    component), while genuinely hidden noise below any root (``.git``,
    ``.markdown_vault_mcp``, editor swap dirs) is still dropped, as is the
    ``DirModifiedEvent`` the watchdog fires on the watched dir itself (empty
    remainder).

    When there is a single non-dot watch root equal to ``source_dir`` this
    reduces to the old ``rel = path.relative_to(source_dir)`` behavior exactly.

    Args:
        abs_path: Absolute endpoint path from a watchdog event.
        watch_root: The directory the delivering watch was scheduled on.

    Returns:
        ``True`` when the event should be ignored.
    """
    try:
        rel = Path(abs_path).relative_to(watch_root)
    except ValueError:
        # Not under this root; it belongs to a different watch, so ignore here.
        return True
    return _has_hidden_component(rel.parts)


class _HiddenChangeNotices:
    """Absorb the parent-directory notice that follows an ignored hidden change.

    The inotify emitter queues ``DirModifiedEvent(parent)`` immediately after a
    child's create, delete or move. When that child is hidden the handler already
    ignores it, but the notice names a visible folder, so a hidden scratch
    directory created and removed there would reindex the vault. Only those three
    event types arm a notice, and the notice for that parent is absorbed once.
    Every other event, hidden modifications included, forgets the pending
    notices, so a bare ``DirModifiedEvent`` (the ``IN_CLOSE_WRITE`` signal the
    subscription keeps) still schedules.
    """

    def __init__(self) -> None:
        self._parents: set[str] = set()

    def note_hidden(
        self,
        event: FileSystemEvent,
        paths: Sequence[str],
        *,
        arm_modified: bool = False,
    ) -> None:
        """Arm the parents of an ignored create, delete or move; clear otherwise.

        Args:
            event: The ignored hidden event.
            paths: Its endpoint paths.
            arm_modified: Also arm parents for an excluded modification.
        """
        if event.event_type in _PARENT_NOTICE_EVENT_TYPES or (
            arm_modified and event.event_type == EVENT_TYPE_MODIFIED
        ):
            self._parents = {str(Path(path).parent) for path in paths}
        else:
            self._parents.clear()

    def absorbs(self, event: FileSystemEvent) -> bool:
        """Return whether *event* is the notice derived from a hidden change.

        Args:
            event: A mutating event that was not itself hidden.

        Returns:
            ``True`` once per noted parent for its ``DirModifiedEvent``.
        """
        path = str(event.src_path)
        if (
            event.is_directory
            and event.event_type == EVENT_TYPE_MODIFIED
            and path in self._parents
        ):
            self._parents.discard(path)
            return True
        self._parents.clear()
        return False


def _event_paths(event: FileSystemEvent) -> list[str]:
    """Return the endpoint paths of *event*: source, plus destination on a move.

    Args:
        event: A watchdog filesystem event.

    Returns:
        The paths the event concerns.
    """
    paths = [getattr(event, "src_path", "") or ""]
    if event.event_type == EVENT_TYPE_MOVED:
        paths.append(getattr(event, "dest_path", "") or "")
    return paths


@dataclass(frozen=True)
class _WatchRoot:
    """A directory to schedule an observer watch on.

    Attributes:
        path: Absolute directory to watch.
        recursive: Whether to watch descendants, rather than direct entries only.
        search: Whether events from this root can trigger search reindexing.
    """

    path: Path
    recursive: bool
    search: bool = True


def _resolve_internal_dirs(internal_dirs: Sequence[Path]) -> list[Path]:
    """Resolve *internal_dirs*, dropping any that cannot be resolved.

    Resolving up front lets :func:`_contains_internal_dir` compare against
    realpaths, so a symlinked state dir still matches its watch-root candidate.

    Args:
        internal_dirs: Vault-internal directories to protect from watching.

    Returns:
        The resolvable directories as resolved paths.
    """
    resolved: list[Path] = []
    for d in internal_dirs:
        try:
            resolved.append(d.resolve())
        except (OSError, RuntimeError):
            # Unresolvable dir (see _resolve_or_original for the two cases).
            # Dropping a protected dir fails open on the #830 guard — its child
            # could then be watched — so warn rather than drop silently.
            logger.warning(
                "file_watcher_internal_dir_resolve_failed path=%s action=unprotected",
                d,
                exc_info=True,
            )
            continue
    return resolved


def _contains_internal_dir(child: Path, internal_dirs: frozenset[Path]) -> bool:
    """Return ``True`` if *child* is, or contains, a vault-internal write dir.

    ``internal_dirs`` must already be resolved. A recursive watch on a directory
    that is or contains the state / index / embeddings dir (or ``.git``) would
    deliver the writes ``reindex`` makes into those paths, re-triggering itself;
    such a directory is excluded from search watches so the loop cannot form
    (#830). Observation can still watch its readable, non-internal parts.

    Args:
        child: Candidate top-level watch-root directory.
        internal_dirs: Resolved vault-internal directories to protect.

    Returns:
        ``True`` when *child* should not be watched for search. An unresolvable *child*
        returns ``False`` (a symlink-loop child is already screened out earlier
        by the caller's ``is_dir`` check).
    """
    try:
        child_resolved = child.resolve()
    except (OSError, RuntimeError):
        # Unresolvable child (see _resolve_or_original for the two cases); it
        # cannot be matched against the resolved internal dirs, so classify it as
        # non-internal. A symlink-loop child is screened by is_dir() upstream, so
        # the realistic trigger here is OSError; log so it is not silent.
        logger.debug(
            "file_watcher_child_resolve_failed path=%s action=treat_as_non_internal",
            child,
            exc_info=True,
        )
        return False
    return any(
        d == child_resolved or d.is_relative_to(child_resolved) for d in internal_dirs
    )


def _resolve_or_original(path: Path) -> Path:
    """Resolve *path* to its realpath, falling back to *path* on ``OSError`` /
    ``RuntimeError``.

    A watch root must be scheduled on its resolved form so it matches watchdog's
    realpath-prefixed FSEvents delivery (see :func:`_derive_watch_roots`).
    ``Path.resolve`` (``strict=False``) resolves best-effort and does *not* raise
    on a broken symlink or a missing / unreadable intermediate component — it
    returns the partially-resolved path. Two failure modes remain, and this guard
    catches both: a **symlink loop** raises ``RuntimeError`` on Python < 3.13
    (3.13+ returns the partially-resolved path instead), and a pathological path
    can raise ``OSError`` on some platforms / filesystems.
    On either we keep the unresolved path — the pre-resolution behavior, correct on
    inotify / ReadDirectoryChangesW — rather than dropping the watch or crashing
    ``start()``, and log at WARNING so the degraded case is visible.

    Args:
        path: Watch path (``source_dir`` or a top-level child) to resolve.

    Returns:
        The resolved path, or *path* unchanged when resolution raises
        ``OSError`` / ``RuntimeError``.
    """
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        logger.warning(
            "file_watcher_watch_path_resolve_failed path=%s action=use_unresolved",
            path,
            exc_info=True,
        )
        return path


def _tree_roots(root: Path) -> list[_WatchRoot]:
    """Split observation roots only at ancestors of internal directories."""

    def visit(directory: Path) -> tuple[bool, list[_WatchRoot]]:
        children: list[_WatchRoot] = []
        split = False
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name in INTERNAL_DIRECTORY_NAMES:
                        split = True
                    else:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                nested, roots = visit(Path(entry.path))
                                split |= nested
                                children.extend(roots)
                        except OSError:
                            split = True
                            logger.warning(
                                "file_watcher_child_unreadable path=%s",
                                entry.path,
                                exc_info=True,
                            )
        except OSError:
            logger.warning(
                "file_watcher_tree_unreadable path=%s", directory, exc_info=True
            )
            return True, []
        if not split:
            return False, [_WatchRoot(directory, True, False)]
        return True, [_WatchRoot(directory, False, False), *children]

    return visit(root)[1]


def _derive_watch_roots(
    source_dir: Path,
    exclude_patterns: Sequence[str] | None,
    *,
    root_floor: bool = True,
    internal_dirs: Sequence[Path] = (),
    observe: bool = False,
) -> list[_WatchRoot]:
    """Compute the set of watch roots to schedule for *source_dir*.

    Each immediate child directory of ``source_dir`` that the exclude patterns
    do not fully prune becomes a recursive watch root, so events deep inside a
    deliberately-watched dot-root (e.g. ``.claude/projects/x/memory/n.md``) are
    delivered. A child that is, or contains, one of ``internal_dirs`` (the
    vault's own state / index / embeddings dir or ``.git``) is skipped for
    search to prevent a self-feedback loop (#830). With ``observe=True``,
    excluded and internal-holding children are retained as observation-only.
    When ``root_floor`` is True
    (default) the ``source_dir`` itself is appended last as a non-recursive
    floor watch so root-level ``*.md`` changes still trigger.

    When ``root_floor`` is False the floor is omitted, so no watch is rooted at
    ``source_dir`` (root-level files are then only picked up by scans). This is
    honored even on the enumeration-failure path, which returns ``[]`` rather
    than re-adding the floor. Silently re-adding it there would defeat the
    zero-``source_dir``-rooted-FSEvents guarantee exactly where the caller cannot
    observe it. The WARNING still logs.

    Every returned root is resolved with :meth:`Path.resolve` where it can be
    (a root whose path cannot be resolved — a symlink loop, or a rare
    ``OSError`` — is kept unresolved with a WARNING via
    :func:`_resolve_or_original` rather than dropped, degrading to the
    pre-resolution behavior). watchdog's
    FSEvents emitter (macOS) resolves the scheduled watch path with
    ``os.path.realpath`` and delivers events with realpath-prefixed
    ``src_path``s, so a watch scheduled on a symlinked child (farm layouts) or
    on a ``source_dir`` under a symlinked prefix (``/var`` vs ``/private/var``)
    would filter every event against a root that is never a prefix of the
    delivered paths — ``relative_to`` raises and each event is silently
    dropped. Scheduling and filtering on the resolved root keeps the two in
    agreement on every platform: inotify and ReadDirectoryChangesW deliver
    events prefixed with the path as scheduled, which is then the resolved
    path too. Prune rules still match the child's name *inside* ``source_dir``
    (the vault-visible name), not its resolved target's basename.

    Reuses :func:`~markdown_vault_mcp.utils.fs._dir_prune_rules` and
    :func:`~markdown_vault_mcp.utils.fs._should_prune_dir` as the single source
    of truth for prune rules, matching ``iter_markdown_files``.

    Args:
        source_dir: Root directory of the vault.
        exclude_patterns: Exclude glob patterns; used only to derive directory
            prune rules. ``None`` or empty prunes nothing.
        root_floor: Whether to append the non-recursive ``source_dir`` floor
            watch. ``False`` omits it on every path, including the fallback.
        internal_dirs: Vault-internal directories (state / index / embeddings
            dir, ``.git``) excluded from search watches, so writes ``reindex``
            makes into them do not re-trigger search. A top-level child that
            is or contains one of these is skipped for search, or kept for
            observation only when *observe*.
        observe: Keep excluded and internal-holding children as
            ``search=False`` roots.

    Returns:
        Resolved watch roots to schedule, the non-recursive resolved
        ``source_dir`` root last when ``root_floor`` is True. On an unreadable
        or vanished ``source_dir``, the single non-recursive resolved
        ``source_dir`` root when ``root_floor`` is True, or ``[]`` when it is
        False.
    """
    anchored, anydepth = _dir_prune_rules(exclude_patterns or ())
    protected = frozenset(_resolve_internal_dirs(internal_dirs))
    source_dir = _resolve_or_original(source_dir)
    roots: list[_WatchRoot] = []
    try:
        children = sorted(source_dir.iterdir())
    except OSError:
        logger.warning(
            "file_watcher_enumerate_failed source_dir=%s action=%s",
            source_dir,
            "watch_root_only" if root_floor else "watch_nothing",
        )
        return [_WatchRoot(source_dir, recursive=False)] if root_floor else []

    for child in children:
        # is_dir() follows symlinks on all versions; iter_markdown_files walks
        # with followlinks only on 3.13+. So watched roots and indexed files
        # agree on 3.13+; on 3.11/3.12 a symlinked top-level dir may be watched
        # though its files are not indexed (harmless: an extra watch).
        # Best-effort by design: a child the server cannot stat is not
        # watched, and the indexing walk logs it as unreadable (#835, #1625).
        try:
            if not is_directory(child):
                continue
            search = not _should_prune_dir(
                child.name, anchored, anydepth
            ) and not _contains_internal_dir(child, protected)
            if not search and (not observe or stat.S_ISLNK(child.lstat().st_mode)):
                continue
            if observe and child.name in INTERNAL_DIRECTORY_NAMES:
                continue
            roots.append(
                _WatchRoot(_resolve_or_original(child), recursive=True, search=search)
            )
        except OSError:
            logger.warning(
                "file_watcher_child_unreadable path=%s", child, exc_info=True
            )

    if root_floor:
        roots.append(_WatchRoot(source_dir, recursive=False))
    return roots


_MAX_LOGGED_ROOTS = 12


def _format_root_names(roots: Sequence[_WatchRoot]) -> str:
    """Format watch-root basenames for the ``start()`` INFO line.

    Non-recursive watches, including the ``source_dir`` floor and internal
    ancestors, are marked ``<name>/(root)``. The list is truncated to the first :data:`_MAX_LOGGED_ROOTS` names
    with a ``(+N more)`` suffix when longer.

    Args:
        roots: Scheduled watch roots, in schedule order.

    Returns:
        A comma-separated, possibly-truncated list of root names.
    """
    names = [
        root.path.name if root.recursive else f"{root.path.name}/(root)"
        for root in roots
    ]
    shown = names[:_MAX_LOGGED_ROOTS]
    if len(names) > _MAX_LOGGED_ROOTS:
        shown.append(f"(+{len(names) - _MAX_LOGGED_ROOTS} more)")
    return ", ".join(shown)


def should_start_file_watcher(
    file_watcher_enabled: bool,
    git_pull_active: bool,
    webhook_active: bool,
) -> bool:
    """Return True when the file watcher should be started.

    The watcher is mutually exclusive with the git pull loop and with a live
    push webhook: those mechanisms trigger reindex on their own cadence, and
    running the file watcher alongside them would cause mid-checkout partial
    scans when git modifies the working tree. Which host sends the webhook
    does not matter to this gate, so it takes the resolved boolean rather
    than a provider's credential.

    The gate stands down only for a mechanism that is actually going to drive
    reindexing. A webhook credential alone is not one: under stdio no route is
    mounted, so no delivery can arrive and the watcher is what keeps external
    edits visible (#1263).

    Args:
        file_watcher_enabled: Value of ``FILE_WATCHER`` config field.
        git_pull_active: Whether the periodic git pull loop will actually
            run.  This is *not* ``GIT_PULL_INTERVAL_S > 0`` alone — the
            interval defaults to 600 even on non-git vaults, but the pull
            loop only runs when a git strategy is configured.  Pass the
            resolved ``git_pull_interval_s`` from ``to_vault_instances()``
            (which is 0 unless ``git_repo_url``/``git_token`` is set),
            compared ``> 0``.
        webhook_active: Whether a push webhook can actually deliver — its
            credentials are set *and* the transport mounts the route.  Pass
            ``webhook_routes_active(config, transport)`` from ``_webhooks``,
            which covers the GitHub secret and both GitLab tokens; passing
            ``config.sync.webhook_configured`` alone silences the watcher on
            a transport that serves no webhook (#1263).

    Returns:
        ``True`` when the watcher should be started.
    """
    git_active = git_pull_active or webhook_active
    return file_watcher_enabled and not git_active


class VaultFileWatcher:
    """Watch a vault directory for external file changes and call *on_change*.

    Debounces rapid bursts of filesystem events into a single callback.
    Search never watches the vault's internal write dirs (``internal_dirs``),
    so git operations and the state-file write ``reindex`` makes on every run
    do not trigger a self-feedback reindex loop. Observation may watch notes
    beside them; it drops internal names and the sweep enforces artifact
    policy.  User dot-roots (e.g. ``.claude/``) are still watched recursively.

    A ``_stopped`` flag prevents watchdog events delivered *during* ``stop()``
    from resurrecting the debounce timer or invoking ``on_change`` after the
    watcher has been stopped.

    Args:
        source_dir: Root directory to watch.  Each non-excluded immediate child
            directory is watched recursively and *source_dir* itself is watched
            non-recursively (see :func:`_derive_watch_roots`).
        on_change: Zero-argument callable invoked after the debounce window.
        debounce_s: Seconds of quiet after the last event before calling
            *on_change*.  Default 2 seconds.
        exclude_patterns: Exclude glob patterns used to skip scheduling watches
            on fully-excluded top-level directories.  ``None`` watches every
            top-level directory recursively.
        root_floor: Whether to schedule the non-recursive ``source_dir`` floor
            watch (default ``True``).  ``False`` omits it, so no watch is rooted
            at ``source_dir`` and root-level files rely on scans (see
            :func:`_derive_watch_roots`).
        internal_dirs: Vault-internal directories (state / index / embeddings
            dir, ``.git``) whose top-level children search never watches, so
            ``reindex``'s writes into them do not re-trigger it (see
            :func:`_derive_watch_roots`).
        on_observation: Optional callback for inventory-only changes, subscribed
            without search exclusions and coalesced with search events.
    """

    def __init__(
        self,
        source_dir: Path,
        on_change: Callable[[], None],
        debounce_s: float = 2.0,
        exclude_patterns: Sequence[str] | None = None,
        root_floor: bool = True,
        internal_dirs: Sequence[Path] = (),
        on_observation: Callable[[], None] | None = None,
    ) -> None:
        self._source_dir = source_dir
        self._on_change = on_change
        self._debounce_s = debounce_s
        self._exclude_patterns = exclude_patterns
        self._root_floor = root_floor
        self._internal_dirs = tuple(internal_dirs)
        self._on_observation = on_observation
        self._observer: Any = None
        self._timer: threading.Timer | None = None
        self._search_pending = False
        self._watches: dict[Path, tuple[_WatchRoot, Any]] = {}
        self._lock = threading.Lock()
        self._stopped = False

    def _schedule_observation(self) -> None:
        self._queue(search=False)

    def _schedule(self) -> None:
        """Reset the debounce timer — no-op after stop()."""
        self._queue(search=True)

    def _queue(self, *, search: bool) -> None:
        with self._lock:
            if self._stopped:
                return
            self._search_pending |= search
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._timer = None
            search, self._search_pending = self._search_pending, False
        callback = self._on_change if search else self._on_observation
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.error("file_watcher_on_change_raised", exc_info=True)

    def _search_event_allowed(self, event: FileSystemEvent) -> bool:
        source = _resolve_or_original(self._source_dir)
        for endpoint in _event_paths(event):
            try:
                relative = Path(endpoint).relative_to(source).as_posix()
            except ValueError:
                return True  # Preserve search for symlinked watch roots.
            excluded = (
                is_subtree_excluded(relative, self._exclude_patterns or ())
                if event.is_directory
                else is_path_excluded(relative, self._exclude_patterns)
            )
            if not excluded:
                return True
        return False

    def _register(self, root: _WatchRoot) -> bool:
        with self._lock:
            observer = self._observer
            if self._stopped or observer is None or root.path in self._watches:
                return False
        handler = _VaultEventHandler(
            self._schedule,
            root.path,
            self._search_event_allowed if self._on_observation else None,
            observe=self._schedule_observation if self._on_observation else None,
            root_info=root,
            sync=self._sync if self._on_observation else None,
        )
        try:
            watch = observer.schedule(
                handler,
                str(root.path),
                recursive=root.recursive,
                event_filter=(
                    _MUTATING_EVENT_CLASSES + _ATTRIBUTE_EVENT_CLASSES
                    if self._on_observation
                    else _MUTATING_EVENT_CLASSES
                ),
            )
        except Exception:
            logger.warning(
                "file_watcher_schedule_failed root=%s recursive=%s",
                root.path,
                root.recursive,
                exc_info=True,
            )
            return False
        with self._lock:
            if not self._stopped and self._observer is observer:
                self._watches[root.path] = (root, watch)
                return True
        with suppress(KeyError):
            observer.unschedule(watch)
        return False

    def _retire(self, root: Path) -> bool:
        with self._lock:
            observer = self._observer
            selected = [
                (path, value)
                for path, value in self._watches.items()
                if path.is_relative_to(root)
            ]
            for path, _ in selected:
                self._watches.pop(path, None)
        if observer is None:
            return False
        for _, (_, watch) in selected:
            with suppress(KeyError):
                observer.unschedule(watch)
        return bool(selected)

    def _sync(self, parent: Path, rebuild: bool = False) -> bool:
        """Reconcile observation subscriptions without holding the timer lock."""
        with self._lock:
            if self._stopped or self._observer is None:
                return False
        if rebuild:
            try:
                present = is_directory(parent)
            except OSError:
                logger.warning(
                    "file_watcher_child_unreadable path=%s", parent, exc_info=True
                )
                return False
            roots = _tree_roots(parent) if present else []
            if present and not roots:
                return False
            changed = self._retire(parent)
            for root in roots:
                changed = self._register(root) or changed
            return changed
        try:
            children = list(parent.iterdir())
        except OSError:
            logger.warning(
                "file_watcher_enumerate_failed source_dir=%s action=keep_existing",
                parent,
                exc_info=True,
            )
            return False
        changed = self._retire_missing(parent, set(children))
        for child in children:
            changed = self._adopt_child(child) or changed
        return changed

    def _retire_missing(self, parent: Path, present: set[Path]) -> bool:
        changed = False
        with self._lock:
            existing = list(self._watches.items())
        for path, _ in existing:
            if path.is_relative_to(parent) and path != parent:
                child = parent / path.relative_to(parent).parts[0]
                if child not in present:
                    changed = self._retire(child) or changed
        return changed

    def _adopt_child(self, child: Path) -> bool:
        if child.name in INTERNAL_DIRECTORY_NAMES:
            return False
        try:
            if not is_directory(child) or stat.S_ISLNK(child.lstat().st_mode):
                return False
        except OSError:
            logger.warning(
                "file_watcher_child_unreadable path=%s", child, exc_info=True
            )
            return False
        with self._lock:
            covered = any(path.is_relative_to(child) for path in self._watches)
        if covered:
            return False
        changed = False
        for root in _tree_roots(child):
            changed = self._register(root) or changed
        return changed

    def start(self) -> None:
        """Start watching *source_dir*.

        No-op when watchdog is not installed.  Safe to call on a stopped
        watcher — resets the stopped flag and starts a fresh observer.
        A no-op if the observer is already running (double-call guard).
        """
        if not _WATCHDOG_AVAILABLE:
            logger.warning("file_watcher_watchdog_missing")
            return

        with self._lock:
            if self._observer is not None:
                return  # already running
            self._stopped = False
            self._watches.clear()
            observer = Observer()
            self._observer = observer
        roots = _derive_watch_roots(
            self._source_dir,
            self._exclude_patterns,
            root_floor=self._root_floor,
            internal_dirs=self._internal_dirs,
            observe=self._on_observation is not None,
        )
        scheduled: list[_WatchRoot] = []
        for root in sorted(roots, key=lambda root: not root.search):
            pieces = _tree_roots(root.path) if not root.search else [root]
            for piece in pieces:
                if self._register(piece):
                    scheduled.append(piece)

        if roots and not scheduled:
            # Every derived root failed to schedule: the observer will run but
            # watch nothing, so no edit will ever trigger a reindex. Surface it
            # above the per-root WARNINGs and the INFO summary so the degraded
            # state is visible, not buried (#835). Distinct from the legitimate
            # "no roots at all" case (root_floor off, no children), where roots
            # is empty and this does not fire.
            logger.error(
                "file_watcher_all_roots_failed roots=%d fallback=scan_only "
                "source_dir=%s",
                len(roots),
                self._source_dir,
            )

        try:
            observer.start()
        except Exception:
            with self._lock:
                self._observer = None
            raise

        recursive_count = sum(1 for root in scheduled if root.recursive)
        logger.info(
            "file_watcher_watching roots=%d recursive=%d floor=%s under=%s "
            "debounce_s=%s roots=%s",
            len(scheduled),
            recursive_count,
            "on" if self._root_floor else "off",
            self._source_dir,
            self._debounce_s,
            _format_root_names(scheduled),
        )

    def stop(self) -> None:
        """Stop watching and cancel any pending debounce timer."""
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._search_pending = False
            observer, self._observer = self._observer, None

        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5.0)
            except Exception:
                logger.warning("file_watcher_stop_failed", exc_info=True)


if _WATCHDOG_AVAILABLE:

    class _VaultEventHandler(FileSystemEventHandler):
        """Forward mutating filesystem events to the search or observation scheduler.

        Read-only events (``opened`` / ``closed`` / ``closed_no_write``) are
        dropped before the hidden-path check so the read-only vault walk that
        ``reindex()`` performs cannot re-trigger itself (#720).

        Hiddenness is evaluated relative to *watch_root*, the specific root this
        handler was scheduled on, so a watched dot-root delivers its non-dot
        descendants while hidden noise below any root is still dropped.
        """

        # The existing callbacks and root context are separate handler inputs.
        def __init__(  # noqa: PLR0913, RUF100
            self,
            schedule: Callable[[], None],
            watch_root: Path,
            allowed: Callable[[FileSystemEvent], bool] | None = None,
            *,
            observe: Callable[[], None] | None = None,
            root_info: _WatchRoot | None = None,
            sync: Callable[[Path, bool], bool] | None = None,
        ) -> None:
            super().__init__()
            self._schedule = schedule
            self._watch_root = watch_root
            self._allowed = allowed
            self._observe = observe
            self._root = root_info or _WatchRoot(watch_root, True)
            self._sync = sync
            self._notices = _HiddenChangeNotices()

        def _concerns_vault(self, event: FileSystemEvent) -> bool:
            """Return whether a mutating event is a visible, non-derived change."""
            paths = _event_paths(event)
            hidden = all(
                _is_hidden_relative_to_root(path, self._watch_root) for path in paths
            )
            excluded = self._allowed is not None and not self._allowed(event)
            if hidden or excluded:
                self._notices.note_hidden(
                    event, paths, arm_modified=excluded and not hidden
                )
                return False
            return not self._notices.absorbs(event)

        def _observation_event(self, event: FileSystemEvent) -> bool:
            paths = _event_paths(event)
            relative: list[tuple[str, tuple[str, ...]]] = []
            for path in paths:
                try:
                    relative.append(
                        (path, Path(path).relative_to(self._watch_root).parts)
                    )
                except ValueError:
                    continue
            if event.is_directory and self._sync is not None:
                internal = any(
                    any(part in INTERNAL_DIRECTORY_NAMES for part in parts)
                    for _, parts in relative
                )
                if (
                    internal
                    and not self._root.search
                    and self._root.recursive
                    and event.event_type in {EVENT_TYPE_CREATED, EVENT_TYPE_MOVED}
                ):
                    return self._sync(self._watch_root, True)
                own = any(not parts for _, parts in relative)
                if (
                    not self._root.search
                    and self._root.recursive
                    and event.event_type == "attrib"
                    and not internal
                ):
                    self._sync(self._watch_root, True)
                if own and event.event_type in {EVENT_TYPE_DELETED, EVENT_TYPE_MOVED}:
                    return self._sync(self._watch_root, True)
                direct = any(len(parts) == 1 for _, parts in relative)
                if (
                    not self._root.recursive
                    and (own or direct)
                    and self._sync(self._watch_root, False)
                ):
                    return True
            return any(
                not any(part in INTERNAL_DIRECTORY_NAMES for part in parts)
                and (
                    (
                        event.is_directory
                        and (parts or self._root.recursive or not self._root.search)
                    )
                    or (not event.is_directory and bool(parts) and is_note(path))
                )
                for path, parts in relative
            )

        def on_any_event(self, event: FileSystemEvent) -> None:
            """Schedule a mutating event when an endpoint belongs to this root.

            Moves consider both endpoints; other mutations consider only source.
            The parent notice watchdog derives from a hidden change is absorbed.
            A change search does not take is scheduled for observation.

            Args:
                event: The filesystem event delivered under this watch root.
            """
            if (
                event.event_type not in _MUTATING_EVENT_TYPES
                and event.event_type != "attrib"
            ):
                return
            search = (
                event.event_type in _MUTATING_EVENT_TYPES
                and self._root.search
                and self._concerns_vault(event)
            )
            observe = self._observe is not None and self._observation_event(event)
            if search:
                self._schedule()
            elif observe and self._observe is not None:
                self._observe()

else:

    class _VaultEventHandler:  # type: ignore[no-redef]
        """Placeholder when watchdog is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
