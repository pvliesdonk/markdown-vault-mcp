"""Linux default-ACL fixtures confined to pytest's disposable directory."""

from __future__ import annotations

import errno
import os
import struct
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# Linux ``posix_acl_xattr`` tags (include/uapi/linux/posix_acl.h).
_USER_OBJ, _GROUP_OBJ, _GROUP, _MASK, _OTHER = 0x01, 0x04, 0x08, 0x10, 0x20
_UNDEFINED_ID = 0xFFFFFFFF


def encode_acl(entries: list[tuple[int, int, int]], version: int = 2) -> bytes:
    """Encode ``(tag, perm, id)`` entries in Linux's ``posix_acl_xattr`` ABI."""
    header = struct.pack("<I", version)
    return header + b"".join(struct.pack("<HHI", *e) for e in entries)


def _acl_directory(tmp_path: Path, entries: list[tuple[int, int, int]]) -> Path:
    """Create a setgid directory carrying *entries* as its default ACL."""
    if sys.platform != "linux":
        pytest.skip("POSIX ACL xattrs are Linux-specific")
    directory = tmp_path / "acl-vault"
    directory.mkdir()
    directory.chmod(0o2770)
    try:
        os.setxattr(directory, "system.posix_acl_default", encode_acl(entries))
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.ENOSYS):
            pytest.skip("filesystem does not support POSIX default ACLs")
        raise
    return directory


@pytest.fixture
def acl_directory(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Create a directory whose default ACL includes a named group.

    The encoding is Linux's public ``posix_acl_xattr`` ABI. No acl package or
    command-line utility is required. Skip filesystems without ACL support.
    The indirect parameter, when given, is the default ACL's mask.
    """
    # The named group is this process's own group; unqualified entries
    # use ACL_UNDEFINED_ID.
    mask = getattr(request, "param", 7)
    entries = [
        (_USER_OBJ, 7, _UNDEFINED_ID),
        (_GROUP_OBJ, 7, _UNDEFINED_ID),
        (_GROUP, 7, os.getgid()),
        (_MASK, mask, _UNDEFINED_ID),
        (_OTHER, 4, _UNDEFINED_ID),
    ]
    return _acl_directory(tmp_path, entries)


@pytest.fixture
def minimal_acl_directory(tmp_path: Path) -> Path:
    """Create a directory whose default ACL has no mask and no named entries.

    Without a mask the group class comes from the ``ACL_GROUP_OBJ`` entry.
    """
    entries = [
        (_USER_OBJ, 6, _UNDEFINED_ID),
        (_GROUP_OBJ, 6, _UNDEFINED_ID),
        (_OTHER, 0, _UNDEFINED_ID),
    ]
    return _acl_directory(tmp_path, entries)
