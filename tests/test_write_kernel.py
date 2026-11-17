"""Atomic-write permissions, including Linux inherited default ACLs."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.managers import _write_kernel as kernel
from tests.fixtures.permissions import encode_acl

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def umask_022(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore the process mask and kernel cache after a permission check."""
    previous = os.umask(0o022)
    monkeypatch.setattr(kernel, "_umask", None)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.usefixtures("umask_022")
@pytest.mark.parametrize("acl_directory", [7, 4], indirect=True)
@pytest.mark.parametrize("data", ["new note\n", b"new attachment\n"])
def test_new_files_inherit_default_acl(acl_directory: Path, data: str | bytes) -> None:
    """A new atomic file has the same ACL, not just mode, as a plain create."""
    plain = acl_directory / "plain.md"
    plain.write_text("plain create")
    target = acl_directory / "atomic.md"

    kernel.atomic_write(target, data)

    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(plain.stat().st_mode)
    assert os.getxattr(target, "system.posix_acl_access") == os.getxattr(
        plain, "system.posix_acl_access"
    )
    assert target.read_bytes() == (data.encode() if isinstance(data, str) else data)
    assert set(acl_directory.iterdir()) == {plain, target}


@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077])
def test_mode_honors_umask_without_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: int
) -> None:
    """Without a default ACL, the derived mode is the umask-masked plain create."""
    previous = os.umask(mask)
    monkeypatch.setattr(kernel, "_umask", None)
    try:
        plain = tmp_path / "plain"
        plain.write_bytes(b"")
        assert kernel.new_file_mode(tmp_path) == stat.S_IMODE(plain.stat().st_mode)
        assert kernel.new_file_mode(tmp_path) == 0o666 & ~mask
    finally:
        os.umask(previous)
    assert list(tmp_path.iterdir()) == [plain]


@pytest.mark.usefixtures("umask_022")
def test_minimal_default_acl_uses_group_obj(minimal_acl_directory: Path) -> None:
    """A default ACL without a mask takes its group class from ACL_GROUP_OBJ."""
    plain = minimal_acl_directory / "plain.md"
    plain.write_text("plain create")
    target = minimal_acl_directory / "atomic.md"

    kernel.atomic_write(target, "content")

    assert stat.S_IMODE(plain.stat().st_mode) == 0o660
    assert stat.S_IMODE(target.stat().st_mode) == 0o660


@pytest.mark.usefixtures("umask_022")
@pytest.mark.parametrize("acl_directory", [7, 4], indirect=True)
def test_mode_is_derived_without_creating_a_file(acl_directory: Path) -> None:
    """Learning the mode leaves the directory, and so the watcher, untouched."""
    plain = acl_directory / "plain.md"
    plain.write_text("plain create")

    mode = kernel.new_file_mode(acl_directory)

    assert mode == stat.S_IMODE(plain.stat().st_mode)
    assert list(acl_directory.iterdir()) == [plain]


@pytest.mark.usefixtures("umask_022")
@pytest.mark.parametrize("err", [errno.ENODATA, errno.ENOTSUP, errno.ENOSYS])
def test_missing_default_acl_falls_back_to_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    """No default ACL, or no xattr support, means the umask applies."""

    def fail(_path: Path, _name: str) -> bytes:
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(kernel.sys, "platform", "linux")
    monkeypatch.setattr(kernel.os, "getxattr", fail, raising=False)
    assert kernel.new_file_mode(tmp_path) == 0o644


@pytest.mark.usefixtures("umask_022")
def test_unreadable_default_acl_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real read failure is not hidden behind the umask fallback."""

    def fail(_path: Path, _name: str) -> bytes:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(kernel.sys, "platform", "linux")
    monkeypatch.setattr(kernel.os, "getxattr", fail, raising=False)
    with pytest.raises(PermissionError):
        kernel.new_file_mode(tmp_path)


@pytest.mark.usefixtures("umask_022")
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        encode_acl([(0x01, 7, 0)])[:-1],
        encode_acl([(0x01, 7, 0), (0x04, 7, 0), (0x20, 7, 0)], version=3),
        encode_acl([(0x04, 7, 0), (0x20, 7, 0)]),
        encode_acl([(0x01, 7, 0), (0x20, 7, 0)]),
    ],
    ids=["empty", "truncated", "version", "no-owner", "no-group"],
)
def test_unparseable_default_acl_falls_back_to_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    """An xattr that is not a usable version-2 ACL leaves the umask in charge."""
    monkeypatch.setattr(kernel.sys, "platform", "linux")
    monkeypatch.setattr(kernel.os, "getxattr", lambda *_: raw, raising=False)
    assert kernel.new_file_mode(tmp_path) == 0o644


@pytest.mark.usefixtures("umask_022")
def test_non_linux_uses_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Other platforms keep the umask-only mode without reading xattrs."""

    def fail(*_args: object) -> bytes:
        raise AssertionError("must not read xattrs off Linux")

    monkeypatch.setattr(kernel.sys, "platform", "darwin")
    monkeypatch.setattr(kernel.os, "getxattr", fail, raising=False)
    assert kernel.new_file_mode(tmp_path) == 0o644


def test_mode_error_does_not_publish_or_leave_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permissions error is not hidden by falling back to the process mask."""

    def fail(_parent: Path) -> int:
        raise PermissionError("cannot create probe")

    monkeypatch.setattr(kernel, "new_file_mode", fail)
    with pytest.raises(PermissionError, match="cannot create probe"):
        kernel.atomic_write(tmp_path / "new.md", "content")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("umask_022")
def test_new_content_stays_private_until_permissions_are_prepared(
    acl_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only owner-only staging holds the note's text before publication."""
    original = kernel.new_file_mode

    def inspect(parent: Path) -> int:
        staging = list(parent.glob("*.tmp"))
        assert len(staging) == 1
        assert staging[0].read_text() == "private staging"
        assert stat.S_IMODE(staging[0].stat().st_mode) == 0o600
        return original(parent)

    monkeypatch.setattr(kernel, "new_file_mode", inspect)
    kernel.atomic_write(acl_directory / "new.md", "private staging")


@pytest.mark.usefixtures("umask_022")
def test_new_file_permissions_are_ready_before_replace(
    acl_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The target is never published with tempfile's owner-only ACL mask."""
    target = acl_directory / "new.md"
    original = Path.replace

    def inspect(source: Path, destination: Path) -> Path:
        assert destination == target
        assert not target.exists()
        assert stat.S_IMODE(source.stat().st_mode) == 0o664
        return original(source, destination)

    monkeypatch.setattr(Path, "replace", inspect)
    kernel.atomic_write(target, "content")


def test_existing_file_keeps_private_mode_without_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing-file permissions continue to come from copymode, not creation."""
    target = tmp_path / "private.md"
    target.write_text("original")
    target.chmod(0o600)

    def fail(_parent: Path) -> int:
        raise AssertionError("must not probe existing files")

    monkeypatch.setattr(kernel, "new_file_mode", fail)
    kernel.atomic_write(target, "replacement")
    assert target.read_text() == "replacement"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.usefixtures("umask_022")
@pytest.mark.parametrize("operation", ["write", "append", "attachment"])
def test_writer_creates_with_default_acl(acl_directory: Path, operation: str) -> None:
    """All public creation paths share the corrected atomic-write primitive."""
    from markdown_vault_mcp.vault import Vault, VaultSettings

    plain = acl_directory / "plain.md"
    plain.write_text("plain")
    name = "new.png" if operation == "attachment" else "new.md"
    vault = Vault(source_dir=acl_directory, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        if operation == "append":
            vault.writer.append(name, "content", create_if_missing=True)
        elif operation == "attachment":
            vault.writer.write_attachment(name, b"attachment")
        else:
            vault.writer.write(name, "content")
    finally:
        vault.close()
    assert os.getxattr(acl_directory / name, "system.posix_acl_access") == os.getxattr(
        plain, "system.posix_acl_access"
    )


def test_existing_acl_and_private_mode_are_unchanged(acl_directory: Path) -> None:
    """An overwrite retains the same inherited entries and private ACL mask."""
    target = acl_directory / "private.md"
    target.write_text("original")
    target.chmod(0o600)
    original_acl = os.getxattr(target, "system.posix_acl_access")
    kernel.atomic_write(target, "replacement")
    assert os.getxattr(target, "system.posix_acl_access") == original_acl
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_copymode_failure_preserves_existing_file_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure preparing existing permissions leaves no staged content behind."""
    target = tmp_path / "existing.md"
    target.write_text("original")

    def fail(_source: Path, _destination: Path) -> None:
        raise PermissionError("cannot copy permissions")

    monkeypatch.setattr(kernel.shutil, "copymode", fail)
    with pytest.raises(PermissionError, match="cannot copy permissions"):
        kernel.atomic_write(target, "replacement")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.usefixtures("umask_022")
def test_no_argument_mode_keeps_legacy_behavior() -> None:
    """Callers without a destination still receive the umask-only mode."""
    assert kernel.new_file_mode() == 0o644
