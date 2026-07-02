"""Filesystem-event watcher for external file changes (issue #558).

Monitors ``source_dir`` with watchdog and calls ``on_change()`` after a
quiet debounce window.  Used when neither the git periodic-pull loop nor
the GitHub webhook is configured — those mechanisms already trigger reindex
on their own cadence, and mixing a file watcher with git checkout would
cause redundant reindexes and mid-checkout partial scans.

Only mounted when ``MARKDOWN_VAULT_MCP_FILE_WATCHER=true`` (default) AND
git pull is disabled (``GIT_PULL_INTERVAL_S=0`` or not set) AND no webhook
secret is configured.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.utils.fs import _dir_prune_rules, _should_prune_dir

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

try:
    from watchdog.events import (
        EVENT_TYPE_CREATED,
        EVENT_TYPE_DELETED,
        EVENT_TYPE_MODIFIED,
        EVENT_TYPE_MOVED,
        FileSystemEvent,
        FileSystemEventHandler,
    )
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True

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
    ``.markdown_vault_mcp``, editor swap dirs) is still dropped, and the
    ``DirModifiedEvent`` watchdog fires on the watched dir itself (empty
    remainder) is dropped.

    When there is a single non-dot watch root equal to ``source_dir`` this
    reduces to the old ``rel = path.relative_to(source_dir)`` behavior exactly.

    Args:
        abs_path: Absolute ``src_path`` from a watchdog event.
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


@dataclass(frozen=True)
class _WatchRoot:
    """A directory to schedule an observer watch on.

    Attributes:
        path: Absolute directory to watch.
        recursive: ``True`` for kept top-level children (watched recursively),
            ``False`` for the ``source_dir`` floor watch (direct entries only).
    """

    path: Path
    recursive: bool


def _derive_watch_roots(
    source_dir: Path,
    exclude_patterns: Sequence[str] | None,
    *,
    root_floor: bool = True,
) -> list[_WatchRoot]:
    """Compute the set of watch roots to schedule for *source_dir*.

    Each immediate child directory of ``source_dir`` that the exclude patterns
    do not fully prune becomes a recursive watch root, so events deep inside a
    deliberately-watched dot-root (e.g. ``.claude/projects/x/memory/n.md``) are
    delivered. When ``root_floor`` is True (default) the ``source_dir`` itself is
    appended last as a non-recursive floor watch so root-level ``*.md`` changes
    still trigger.

    When ``root_floor`` is False the floor is omitted, so no watch is rooted at
    ``source_dir`` (root-level files are then only picked up by scans). This is
    honored even on the enumeration-failure path, which returns ``[]`` rather
    than re-adding the floor. Silently re-adding it there would defeat the
    zero-``source_dir``-rooted-FSEvents guarantee exactly where the caller cannot
    observe it. The WARNING still logs.

    Reuses :func:`~markdown_vault_mcp.utils.fs._dir_prune_rules` and
    :func:`~markdown_vault_mcp.utils.fs._should_prune_dir` as the single source
    of truth for prune rules, matching ``iter_markdown_files``.

    Args:
        source_dir: Root directory of the vault.
        exclude_patterns: Exclude glob patterns; used only to derive directory
            prune rules. ``None`` or empty prunes nothing.
        root_floor: Whether to append the non-recursive ``source_dir`` floor
            watch. ``False`` omits it on every path, including the fallback.

    Returns:
        Watch roots to schedule, the non-recursive ``source_dir`` root last when
        ``root_floor`` is True. On an unreadable or vanished ``source_dir``, the
        single non-recursive ``source_dir`` root when ``root_floor`` is True, or
        ``[]`` when it is False.
    """
    anchored, anydepth = _dir_prune_rules(exclude_patterns or ())
    roots: list[_WatchRoot] = []
    try:
        children = sorted(source_dir.iterdir())
    except OSError:
        logger.warning(
            "file_watcher: could not enumerate source_dir=%s; %s",
            source_dir,
            "watching root only" if root_floor else "watching nothing",
        )
        return [_WatchRoot(source_dir, recursive=False)] if root_floor else []

    for child in children:
        # is_dir() follows symlinks, matching the 3.13+ followlinks semantics
        # iter_markdown_files uses so watched roots and indexed files agree.
        if not child.is_dir():
            continue
        if _should_prune_dir(child.name, anchored, anydepth):
            continue
        roots.append(_WatchRoot(child, recursive=True))

    if root_floor:
        roots.append(_WatchRoot(source_dir, recursive=False))
    return roots


_MAX_LOGGED_ROOTS = 12


def _format_root_names(roots: Sequence[_WatchRoot]) -> str:
    """Format watch-root basenames for the ``start()`` INFO line.

    The non-recursive ``source_dir`` floor watch is marked ``<name>/(root)`` so
    the "new top-level directories need a restart" limitation is observable in
    the log. The list is truncated to the first :data:`_MAX_LOGGED_ROOTS` names
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
    github_webhook_secret: str | None,
) -> bool:
    """Return True when the file watcher should be started.

    The watcher is mutually exclusive with the git pull loop and GitHub
    webhook: those mechanisms trigger reindex on their own cadence, and
    running the file watcher alongside them would cause mid-checkout partial
    scans when git modifies the working tree.

    Args:
        file_watcher_enabled: Value of ``FILE_WATCHER`` config field.
        git_pull_active: Whether the periodic git pull loop will actually
            run.  This is *not* ``GIT_PULL_INTERVAL_S > 0`` alone — the
            interval defaults to 600 even on non-git vaults, but the pull
            loop only runs when a git strategy is configured.  Pass the
            resolved ``git_pull_interval_s`` from ``to_vault_kwargs()``
            (which is 0 unless ``git_repo_url``/``git_token`` is set),
            compared ``> 0``.
        github_webhook_secret: Value of ``GITHUB_WEBHOOK_SECRET`` config field.

    Returns:
        ``True`` when the watcher should be started.
    """
    git_active = git_pull_active or bool(github_webhook_secret)
    return file_watcher_enabled and not git_active


class VaultFileWatcher:
    """Watch a vault directory for external file changes and call *on_change*.

    Debounces rapid bursts of filesystem events into a single callback.
    Changes inside hidden directories (e.g. ``.git/``, ``.markdown_vault_mcp/``)
    are silently ignored so git operations and state-file writes do not
    trigger spurious reindexes.

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
    """

    def __init__(
        self,
        source_dir: Path,
        on_change: Callable[[], None],
        debounce_s: float = 2.0,
        exclude_patterns: Sequence[str] | None = None,
        root_floor: bool = True,
    ) -> None:
        self._source_dir = source_dir
        self._on_change = on_change
        self._debounce_s = debounce_s
        self._exclude_patterns = exclude_patterns
        self._root_floor = root_floor
        self._observer: Any = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def _schedule(self) -> None:
        """Reset the debounce timer — no-op after stop()."""
        with self._lock:
            if self._stopped:
                return
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
        try:
            self._on_change()
        except Exception:
            logger.error("file_watcher: on_change callback raised", exc_info=True)

    def start(self) -> None:
        """Start watching *source_dir*.

        No-op when watchdog is not installed.  Safe to call on a stopped
        watcher — resets the stopped flag and starts a fresh observer.
        A no-op if the observer is already running (double-call guard).
        """
        if not _WATCHDOG_AVAILABLE:
            logger.warning(
                "file_watcher: watchdog not installed; external file changes "
                "will not trigger automatic reindex. "
                "Install watchdog: pip install 'markdown-vault-mcp[file-watcher]'"
            )
            return

        with self._lock:
            if self._observer is not None:
                return  # already running
            self._stopped = False

        roots = _derive_watch_roots(
            self._source_dir, self._exclude_patterns, root_floor=self._root_floor
        )
        observer = Observer()
        scheduled: list[_WatchRoot] = []
        for root in roots:
            handler = _VaultEventHandler(self._schedule, root.path)
            try:
                observer.schedule(handler, str(root.path), recursive=root.recursive)
            except Exception:
                # A single root can fail (e.g. watchdog cannot watch a symlinked
                # child's target); keep the rest, notably the source_dir floor.
                logger.warning(
                    "file_watcher: could not schedule watch on root=%s recursive=%s",
                    root.path,
                    root.recursive,
                    exc_info=True,
                )
                continue
            scheduled.append(root)
        # Assign before start() so stop() can always see and stop it,
        # even if it races into the narrow window between schedule() and start().
        with self._lock:
            self._observer = observer
        try:
            observer.start()
        except Exception:
            with self._lock:
                self._observer = None
            raise

        recursive_count = sum(1 for root in scheduled if root.recursive)
        logger.info(
            "file_watcher: watching roots=%d (recursive=%d) floor=%s under=%s "
            "debounce_s=%s roots=[%s]",
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
            observer, self._observer = self._observer, None

        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5.0)
            except Exception:
                logger.warning("file_watcher: error stopping observer", exc_info=True)


if _WATCHDOG_AVAILABLE:

    class _VaultEventHandler(FileSystemEventHandler):
        """Forward mutating, non-hidden filesystem events to the scheduler.

        Read-only events (``opened`` / ``closed`` / ``closed_no_write``) are
        dropped before the hidden-path check so the read-only vault walk that
        ``reindex()`` performs cannot re-trigger itself (#720).

        Hiddenness is evaluated relative to *watch_root*, the specific root this
        handler was scheduled on, so a watched dot-root delivers its non-dot
        descendants while hidden noise below any root is still dropped.
        """

        def __init__(self, schedule: Callable[[], None], watch_root: Path) -> None:
            super().__init__()
            self._schedule = schedule
            self._watch_root = watch_root

        def on_any_event(self, event: FileSystemEvent) -> None:
            if event.event_type not in _MUTATING_EVENT_TYPES:
                return
            path = getattr(event, "src_path", "") or ""
            if _is_hidden_relative_to_root(path, self._watch_root):
                return
            self._schedule()

else:

    class _VaultEventHandler:  # type: ignore[no-redef]
        """Placeholder when watchdog is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
