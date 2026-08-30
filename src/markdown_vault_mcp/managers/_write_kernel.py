"""File-write primitives shared by the document and artifact paths (#1235).

``DocumentManager`` and
:class:`~markdown_vault_mcp.managers.artifacts.ArtifactStore` write bytes the
same way: atomically, and under the same etag precondition. These are the
genuinely pure parts of that — they read no manager state — so they live here
as module functions rather than being inherited or copied.

The umask handling is load-bearing: :mod:`tempfile` hardcodes ``0o600``, so a
freshly-created file is chmod'd after the replace to match what a plain
``open(path, "w")`` would have produced.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

from markdown_vault_mcp.exceptions import ConcurrentModificationError
from markdown_vault_mcp.hashing import compute_file_hash

__all__ = ["atomic_write", "check_if_match", "new_file_mode"]

_UMASK_LOCK = threading.Lock()
_umask: int | None = None


def _process_umask() -> int:
    """Return the process umask, read once and cached.

    ``os.umask`` is set-and-restore with no read-only accessor; we read it
    once under a lock so concurrent writers serialise the set-and-restore
    and never observe a transient mask. The umask is set by the parent
    (shell / systemd) before Python starts and is effectively immutable
    for the service lifetime, so a one-time read is correct.
    """
    global _umask
    if _umask is not None:
        return _umask
    with _UMASK_LOCK:
        # The lock serialises the set-and-restore so no writer in this
        # module observes a transient mask. A second writer that lost the
        # outer cache check re-reads and re-sets the same value (the umask
        # is process-global), so no inner guard is needed.
        mask = os.umask(0o077)
        os.umask(mask)
        _umask = mask
    return _umask


def new_file_mode() -> int:
    """Mode for a freshly-created file, matching a plain ``open(path, "w")``."""
    return 0o666 & ~_process_umask()


def check_if_match(abs_path: Path, path: str, if_match: str | None) -> None:
    """Enforce an optional etag precondition on *abs_path*.

    Args:
        abs_path: Absolute path of the file the etag refers to.
        path: Vault-relative path, used in the error.
        if_match: Etag from a previous read, or ``None`` to skip the
            check. A file that does not exist counts as a mismatch.

    Raises:
        ConcurrentModificationError: If *if_match* is provided and does
            not match the current file hash (or the file is missing).
    """
    if if_match is None:
        return
    if not abs_path.is_file():
        raise ConcurrentModificationError(
            path,
            expected=if_match,
            actual="(file does not exist)",
        )
    current_hash = compute_file_hash(abs_path)
    if current_hash != if_match:
        raise ConcurrentModificationError(path, expected=if_match, actual=current_hash)


def atomic_write(abs_path: Path, data: str | bytes) -> None:
    """Write *data* to *abs_path* atomically via a sibling tempfile.

    The temp file lands with :meth:`Path.replace` semantics; permission
    bits are preserved when the target already exists. A freshly-created
    target is chmod'd to ``0o666 & ~umask`` after the replace so fresh
    writes honour the process umask (matching a plain ``open``) instead
    of the ``0o600`` ``tempfile`` hardcodes. On failure the temp file is
    removed and the original is left untouched.

    Args:
        abs_path: Absolute destination path.
        data: Text (written UTF-8) or raw bytes.
    """
    existed = abs_path.is_file()
    if isinstance(data, str):
        with tempfile.NamedTemporaryFile(
            dir=abs_path.parent,
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(data)
            tmp_name = tmp.name
    else:
        with tempfile.NamedTemporaryFile(
            dir=abs_path.parent,
            mode="wb",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(data)
            tmp_name = tmp.name
    if existed:
        shutil.copymode(abs_path, tmp_name)
    try:
        Path(tmp_name).replace(abs_path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    if not existed:
        abs_path.chmod(new_file_mode())
