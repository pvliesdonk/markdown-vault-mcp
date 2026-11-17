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
    accessed: 2026-11-17
  - id: tempfile
    title: Python tempfile documentation
    resource: https://docs.python.org/3/library/tempfile.html
    accessed: 2026-11-16
  - id: os
    title: Python os extended attributes
    resource: https://docs.python.org/3/library/os.html#linux-extended-attributes
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

- A directory's default ACL becomes a new file's access ACL; the entries
  that correspond to the permission bits are then restricted to the
  requested creation mode, and the umask is not used. Without a default
  ACL, the umask applies.
  [source: acl] [pins: tests/test_write_kernel.py::test_new_files_inherit_default_acl, tests/test_write_kernel.py::test_mode_honors_umask_without_acl]
- The owner bits correspond to `ACL_USER_OBJ` and the other bits to
  `ACL_OTHER`. The group bits correspond to `ACL_MASK` when the ACL has
  one, else to `ACL_GROUP_OBJ`; chmod changes those entries, and named
  entries remain subject to the mask. [source: acl]
  [pins: tests/test_write_kernel.py::test_minimal_default_acl_uses_group_obj, tests/test_write_kernel.py::test_existing_acl_and_private_mode_are_unchanged]
- Secure temporary files restrict access to their creator. An atomic writer
  must choose its final permissions separately. [source: tempfile]
  [pins: tests/test_write_kernel.py::test_new_content_stays_private_until_permissions_are_prepared]
- `os.getxattr` reads an extended attribute and is available on Linux only.
  [source: os] [pins: tests/test_write_kernel.py::test_non_linux_uses_umask]
- Linux exposes a directory's default ACL as the `system.posix_acl_default`
  xattr: a little-endian version-2 header, then entries holding a 16-bit tag,
  16-bit permissions and a 32-bit ID. The tags used here are `ACL_USER_OBJ`
  (0x01), `ACL_GROUP_OBJ` (0x04), `ACL_MASK` (0x10) and `ACL_OTHER` (0x20).
  [source: acl-format] [source: acl-tags]
  [pins: tests/test_write_kernel.py::test_mode_is_derived_without_creating_a_file, tests/test_write_kernel.py::test_unparseable_default_acl_falls_back_to_umask]

Observed on Linux with Python 3.12: under umask 0022 and the fixture's
named-group-write default ACL, plain creation produces mode 0664 and mask
`rw-`; the previous umask-only chmod produced 0644 and mask `r--`.
[observed: tests/test_write_kernel.py::test_new_files_inherit_default_acl on the pre-fix tree]

## Scope limits

This reference covers new-file POSIX ACL inheritance, not arbitrary ACL
preservation when replacing an existing inode, network ACL dialects, Windows
DACLs or ownership changes. Existing-file mode copying is unchanged. The
runtime reads only the POSIX default-ACL xattr, so inheritance through other
ACL models (NFSv4, SMB) is not derived and the umask applies there. The
Linux regression fixture writes the same ABI and skips filesystems that do
not support it.
