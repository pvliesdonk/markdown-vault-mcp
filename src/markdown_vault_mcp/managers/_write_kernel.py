"""File-write primitives shared by the document and artifact paths (#1235).

``DocumentManager`` and
:class:`~markdown_vault_mcp.managers.artifacts.ArtifactStore` write bytes the
same way: atomically, and under the same etag precondition. These are the
genuinely pure parts of that — they read no manager state — so they live here
as module functions rather than being inherited or copied.

Private staging uses :mod:`tempfile`'s ``0o600``. A new file is chmod'd,
before publication, to the mode a plain create receives: the directory's
POSIX default ACL when it has one, else the process umask. See
``docs/design/reference/file-creation-permissions.md``.
"""

from __future__ import annotations

import errno
import os
import shutil
import struct
import sys
import tempfile
import threading
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


# Linux ``posix_acl_xattr`` ABI (include/uapi/linux/posix_acl_xattr.h): a
# little-endian u32 version header, then (u16 tag, u16 perm, u32 id) entries.
_ACL_XATTR_DEFAULT = "system.posix_acl_default"
_ACL_XATTR_VERSION = 2
_ACL_HEADER = struct.Struct("<I")
_ACL_ENTRY = struct.Struct("<HHI")
_ACL_USER_OBJ = 0x01
_ACL_GROUP_OBJ = 0x04
_ACL_MASK = 0x10
_ACL_OTHER = 0x20
# getxattr errnos meaning "no default ACL here", not a failure.
_NO_ACL_ERRNOS = frozenset(
    {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}
)


def _read_default_acl(parent: Path) -> bytes | None:
    """Return *parent*'s raw ``system.posix_acl_default`` xattr.

    Args:
        parent: Directory whose default ACL is read.

    Returns:
        The xattr bytes, or ``None`` off Linux, on a filesystem without
        xattr support, or when the directory has no default ACL.

    Raises:
        OSError: The read failed for another reason (for example
            ``EACCES``).
    """
    if sys.platform != "linux":
        return None
    try:
        return os.getxattr(parent, _ACL_XATTR_DEFAULT)
    except OSError as exc:
        if exc.errno in _NO_ACL_ERRNOS:
            return None
        raise


def _acl_class_perms(raw: bytes) -> tuple[int, int, int] | None:
    """Extract the owner, group-class and other permissions of an ACL.

    The group class is the ``ACL_MASK`` entry when the ACL has one, else
    ``ACL_GROUP_OBJ`` (acl(5), "Correspondence between ACL entries and
    file permission bits").

    Args:
        raw: A ``posix_acl_xattr``-encoded ACL.

    Returns:
        ``(owner, group_class, other)`` permission triplets, or ``None``
        when *raw* is not a version-2 ACL carrying those entries.
    """
    body = len(raw) - _ACL_HEADER.size
    if body < 0 or body % _ACL_ENTRY.size:
        return None
    if _ACL_HEADER.unpack_from(raw)[0] != _ACL_XATTR_VERSION:
        return None
    perms: dict[int, int] = {}
    for tag, perm, _id in _ACL_ENTRY.iter_unpack(raw[_ACL_HEADER.size :]):
        perms.setdefault(tag, perm & 0o7)
    owner = perms.get(_ACL_USER_OBJ)
    group = perms.get(_ACL_MASK, perms.get(_ACL_GROUP_OBJ))
    other = perms.get(_ACL_OTHER)
    if owner is None or group is None or other is None:
        return None
    return owner, group, other


def new_file_mode(parent: Path | None = None) -> int:
    """Return the mode a plain ``open(path, "w")`` would give a new file.

    A plain create requests ``0o666``. In a directory with a POSIX default
    ACL the kernel ignores the umask and restricts the default ACL's owner,
    group-class (mask) and other entries by that request; elsewhere the
    umask applies (acl(5), "Object creation and default ACLs"). The mode
    is derived from the directory's ``system.posix_acl_default`` xattr,
    so no file is created to learn it.

    Args:
        parent: Destination directory, whose default ACL is consulted.
            Omit for the umask-only calculation.

    Returns:
        Permission bits for the new file.

    Raises:
        OSError: The directory's default ACL could not be read.
    """
    requested = 0o666
    raw = None if parent is None else _read_default_acl(parent)
    acl = None if raw is None else _acl_class_perms(raw)
    if acl is None:
        return requested & ~_process_umask()
    owner, group, other = acl
    return requested & (owner << 6 | group << 3 | other)


MISSING_ETAG = "(file does not exist)"


def check_if_match(abs_path: Path, path: str, if_match: str | None) -> None:
    """Enforce an optional etag precondition on *abs_path*.

    Args:
        abs_path: Absolute path of the file the etag refers to.
        path: Vault-relative path, used in the error.
        if_match: Etag from a previous read, or ``None`` to skip the
            check. ``MISSING_ETAG`` requires an unoccupied path; other etags
            require an existing file. The check and replacement are separate.

    Raises:
        ConcurrentModificationError: If *if_match* is provided and does
            not match the current file hash (or the file is missing).
    """
    if if_match is None:
        return
    if if_match == MISSING_ETAG:
        if not os.path.lexists(abs_path):
            return
        raise ConcurrentModificationError(
            path, expected=if_match, actual="(path occupied)"
        )
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
