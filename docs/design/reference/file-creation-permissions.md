---
type: Reference
title: File creation permissions and default ACLs
description: Kernel permission inheritance used by atomic note and attachment writes.
subject_version: "Linux POSIX ACLs; Python 3.12"
valid_for: "Linux POSIX ACL ABI and Python 3.x file creation"
generated:
  by: process:researching-references
  at: 2026-11-16T05:35:00+00:00
stale_after: 2027-05-16T05:35:00+00:00
status: stable
sources:
  - id: acl
    title: Linux acl(5), object creation and default ACLs
    resource: https://man7.org/linux/man-pages/man5/acl.5.html
    accessed: 2026-11-16
  - id: tempfile
    title: Python tempfile documentation
    resource: https://docs.python.org/3/library/tempfile.html
    accessed: 2026-11-16
  - id: os
    title: Python os file descriptor operations
    resource: https://docs.python.org/3/library/os.html#os.open
    accessed: 2026-11-16
  - id: acl-format
    title: Linux public POSIX ACL extended-attribute format
    resource: https://github.com/torvalds/linux/blob/master/include/uapi/linux/posix_acl_xattr.h
    accessed: 2026-11-16
  - id: acl-tags
    title: Linux public POSIX ACL tags and permissions
    resource: https://github.com/torvalds/linux/blob/master/include/uapi/linux/posix_acl.h
    accessed: 2026-11-16
---

# File creation permissions and default ACLs

The consumer is `managers/_write_kernel.py`; the project policy is in
[atomic file permissions](../design.md#atomic-file-permissions).

- A directory's default ACL becomes a new file's access ACL, restricted by
  the requested creation mode. Without a default ACL, the umask applies.
  [source: acl] [pins: tests/test_write_kernel.py::test_new_files_inherit_default_acl, tests/test_write_kernel.py::test_probe_honors_umask_without_acl]
- With an ACL mask, the file's group mode bits represent that mask; chmod
  changes the corresponding ACL entries. Named entries remain subject to
  the mask. [source: acl]
  [pins: tests/test_write_kernel.py::test_existing_acl_and_private_mode_are_unchanged]
- Secure temporary files restrict access to their creator. An atomic writer
  must choose its final permissions separately. [source: tempfile]
  [pins: tests/test_write_kernel.py::test_new_content_stays_private_until_permissions_are_prepared]
- `os.open` accepts creation flags and a mode; `os.fstat` reads the opened
  descriptor's metadata, and `os.close` releases it. [source: os]
  [pins: tests/test_write_kernel.py::test_probe_failure_closes_and_removes_empty_file]
- Linux ACL xattrs use a little-endian version-2 header and entries containing
  a 16-bit tag, 16-bit permissions and 32-bit ID. The test fixture uses the
  public user/group/mask/other tags and undefined-ID sentinel. [source: acl-format]
  [source: acl-tags] [pins: tests/test_write_kernel.py::test_new_files_inherit_default_acl]

Observed on Linux with Python 3.12: under umask 0022 and the fixture's
named-group-write default ACL, plain creation produces mode 0664 and mask
`rw-`; the previous umask-only chmod produced 0644 and mask `r--`.
[observed: tests/test_write_kernel.py::test_new_files_inherit_default_acl on the pre-fix tree]

## Scope limits

This reference covers new-file POSIX ACL inheritance, not arbitrary ACL
preservation when replacing an existing inode, network ACL dialects, Windows
DACLs or ownership changes. Existing-file mode copying is unchanged. The
runtime does not parse Linux ACLs; only the Linux regression fixture uses
that ABI, skipping filesystems that do not support it.
