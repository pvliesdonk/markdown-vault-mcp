"""File-write primitives shared by the document and artifact paths (#1235).

``DocumentManager`` and
:class:`~markdown_vault_mcp.managers.artifacts.ArtifactStore` write bytes the
same way: atomically, and under the same etag precondition. These are the
genuinely pure parts of that — they read no manager state — so they live here
as module functions rather than being inherited or copied.

Private staging uses :mod:`tempfile`'s ``0o600``. For new files, an empty
sibling probe obtains the mode a plain create receives from the kernel,
including default ACLs; that mode is applied before publication. See
``docs/design/reference/file-creation-permissions.md``.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import uuid
from pathlib import Path

from markdown_vault_mcp.exceptions import ConcurrentModificationError
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.utils.fs import is_regular_file

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


def new_file_mode(parent: Path | None = None) -> int:
    """Return the mode a plain file creation receives.

    Args:
        parent: Destination directory, including its default ACL policy.
            Omit for the legacy umask-only calculation.

    Returns:
        Permission bits assigned by the kernel, or the umask-only mode.
    """
    if parent is None:
        return 0o666 & ~_process_umask()
    for _ in range(100):
        probe = parent / f".mode-{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            return stat.S_IMODE(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
            probe.unlink()
    raise FileExistsError("Could not create an exclusive permission probe")


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
    if not is_regular_file(abs_path):
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
    bits are preserved when the target already exists. New files receive
    the mode of a plain create in their directory, honoring the umask or
    inherited default ACL. Content is staged privately; final permissions
    are set before replacement. On failure the temp file is removed and
    the original is left untouched.

    Args:
        abs_path: Absolute destination path.
        data: Text (written UTF-8) or raw bytes.
    """
    existed = is_regular_file(abs_path)
    text = isinstance(data, str)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=abs_path.parent,
            mode="w" if text else "wb",
            encoding="utf-8" if text else None,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
        if existed:
            shutil.copymode(abs_path, tmp_path)
        else:
            tmp_path.chmod(new_file_mode(abs_path.parent))
        tmp_path.replace(abs_path)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
