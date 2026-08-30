"""Single owner of write-callback dispatch shape (#1235).

``DocumentManager`` and :class:`~markdown_vault_mcp.managers.artifacts.ArtifactStore`
both report completed writes to the same callback, and both must dispatch it
identically — including the ``old_path`` opt-in probe (#894), which is easy to
get subtly wrong twice. This holds that logic once; the two share one instance
by reference, never a copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from markdown_vault_mcp.types import ACCEPTS_OLD_PATH_ATTR

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import WriteCallback, WriteOperation

__all__ = ["OnWriteCallback", "WriteNotifier"]


class OnWriteCallback(Protocol):
    """The write-callback shape the managers dispatch to.

    Widens :data:`~markdown_vault_mcp.types.WriteCallback` with the optional
    ``old_path`` keyword the rename sites pass (#894).  The dispatcher this is
    wired to — :meth:`WriteCallbackDispatcher.fire` — accepts it
    unconditionally and forwards it only to callbacks that opted in, so a
    three-argument ``on_write`` remains valid at the ``Vault`` boundary.
    """

    def __call__(
        self,
        path: Path,
        content: str,
        operation: WriteOperation,
        /,
        old_path: Path | None = None,
    ) -> None:
        """Record one completed write."""
        ...


class WriteNotifier:
    """Dispatches completed writes to the configured callback.

    Args:
        on_write: The vault's write callback, or ``None`` for a vault with no
            versioning backend (dispatch then becomes a no-op).
    """

    def __init__(self, on_write: OnWriteCallback | WriteCallback | None) -> None:
        self._callback: OnWriteCallback | WriteCallback = on_write or (
            lambda *_a, **_kw: None
        )
        #: Probed once at construction: whether the callback opted into
        #: ``old_path`` (#894).  A callback that did not keeps the historical
        #: three-argument call.
        self._takes_old_path = bool(getattr(on_write, ACCEPTS_OLD_PATH_ATTR, False))

    def fire(self, abs_path: Path, content: str, operation: WriteOperation) -> None:
        """Report one completed write.

        Args:
            abs_path: Absolute path that was written.
            content: The content written (``""`` where the callback does not
                need it, as for artifacts and deletes).
            operation: Which kind of write completed.
        """
        self._callback(abs_path, content, operation)

    def fire_rename(self, new_abs: Path, content: str, old_abs: Path) -> None:
        """Report a rename, passing both sides when the callback accepts them.

        Args:
            new_abs: Absolute path the file now lives at.
            content: File content at the new path (``""`` for artifacts).
            old_abs: Absolute path the file moved from, so a callback that
                opted in can scope its git staging to the two paths the rename
                touched instead of staging the whole repository (#894).
        """
        if self._takes_old_path:
            rename_aware = cast("OnWriteCallback", self._callback)
            rename_aware(new_abs, content, "rename", old_path=old_abs)
        else:
            self._callback(new_abs, content, "rename")
