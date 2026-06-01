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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


def _has_hidden_component(path: str) -> bool:
    """Return True when *path* should be ignored.

    Two cases:
    - Any path component starts with a dot (hidden dirs like ``.git/``).
    - Empty parts (``'.'``) meaning the watched root itself — ``DirModifiedEvent``
      on source_dir fires for every file addition/deletion inside it, hidden or
      not.  Filtering it out is safe because the concrete file events always
      accompany it and carry the actual path information.
    """
    from pathlib import PurePosixPath

    parts = PurePosixPath(path).parts
    if not parts:
        return True
    return any(part.startswith(".") for part in parts)


class VaultFileWatcher:
    """Watch a vault directory for external file changes and call *on_change*.

    Debounces rapid bursts of filesystem events into a single callback.
    Changes inside hidden directories (e.g. ``.git/``, ``.markdown_vault_mcp/``)
    are silently ignored so git operations and state-file writes do not
    trigger spurious reindexes.

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

    def _schedule(self) -> None:
        """Reset the debounce timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._on_change()
        except Exception:
            logger.error("file_watcher: on_change callback raised", exc_info=True)

    def start(self) -> None:
        """Start watching *source_dir*.  No-op when watchdog is not installed."""
        if not _WATCHDOG_AVAILABLE:
            logger.warning(
                "file_watcher: watchdog not installed; external file changes "
                "will not trigger automatic reindex. "
                "Install watchdog: pip install 'markdown-vault-mcp[file-watcher]'"
            )
            return

        handler = _VaultEventHandler(self._schedule, self._source_dir)
        observer = Observer()
        observer.schedule(handler, str(self._source_dir), recursive=True)
        observer.start()
        self._observer = observer
        logger.info(
            "file_watcher: watching source_dir=%s debounce_s=%s",
            self._source_dir,
            self._debounce_s,
        )

    def stop(self) -> None:
        """Stop watching and cancel any pending debounce timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                logger.warning("file_watcher: error stopping observer", exc_info=True)
            finally:
                self._observer = None


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
                from pathlib import Path as _Path

                rel = _Path(path).relative_to(self._source_dir)
                if _has_hidden_component(str(rel)):
                    return
            except ValueError:
                return
            self._schedule()

else:

    class _VaultEventHandler:  # type: ignore[no-redef]
        """Placeholder when watchdog is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
