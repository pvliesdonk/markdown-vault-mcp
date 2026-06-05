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
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
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
        source_dir: Root directory to watch recursively.
        on_change: Zero-argument callable invoked after the debounce window.
        debounce_s: Seconds of quiet after the last event before calling
            *on_change*.  Default 2 seconds.
    """

    def __init__(
        self,
        source_dir: Path,
        on_change: Callable[[], None],
        debounce_s: float = 2.0,
    ) -> None:
        self._source_dir = source_dir
        self._on_change = on_change
        self._debounce_s = debounce_s
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

        handler = _VaultEventHandler(self._schedule, self._source_dir)
        observer = Observer()
        observer.schedule(handler, str(self._source_dir), recursive=True)
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

        logger.info(
            "file_watcher: watching source_dir=%s debounce_s=%s",
            self._source_dir,
            self._debounce_s,
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
        """Forward non-hidden filesystem events to the debounce scheduler."""

        def __init__(self, schedule: Callable[[], None], source_dir: Path) -> None:
            super().__init__()
            self._schedule = schedule
            self._source_dir = source_dir

        def on_any_event(self, event: FileSystemEvent) -> None:
            path = getattr(event, "src_path", "") or ""
            try:
                rel = Path(path).relative_to(self._source_dir)
                if _has_hidden_component(rel.parts):
                    return
            except ValueError:
                return
            self._schedule()

else:

    class _VaultEventHandler:  # type: ignore[no-redef]
        """Placeholder when watchdog is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
