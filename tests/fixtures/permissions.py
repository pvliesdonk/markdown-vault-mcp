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


@pytest.fixture
def acl_directory(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Create a directory whose default ACL includes a named group.

    The encoding is Linux's public ``posix_acl_xattr`` ABI. No acl package or
    command-line utility is required. Skip filesystems without ACL support.
    """
    if sys.platform != "linux":
        pytest.skip("POSIX ACL xattrs are Linux-specific")
    directory = tmp_path / "acl-vault"
    directory.mkdir()
    directory.chmod(0o2770)
    # user_obj, group_obj, named group, mask, other. Unqualified entries
    # use ACL_UNDEFINED_ID; the named group is this process's own group.
    entries = [(1, 7, 0xFFFFFFFF), (4, 7, 0xFFFFFFFF), (8, 7, os.getgid())]
    entries += [(16, getattr(request, "param", 7), 0xFFFFFFFF), (32, 4, 0xFFFFFFFF)]
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)
    try:
        os.setxattr(directory, "system.posix_acl_default", acl)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.ENOSYS):
            pytest.skip("filesystem does not support POSIX default ACLs")
        raise
    return directory
