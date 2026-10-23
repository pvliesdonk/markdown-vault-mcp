"""One process writes a built-in vector store at a time (#1505 child 6a)."""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import StoreBusyError, StoreUnavailableError
from markdown_vault_mcp.stores import _writer_lock
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN
from tests.fixtures.vector_store_contract import IDENT, doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

BACKENDS = {"numpy": NumpySidecarBackend, "sqlite": SqliteVectorBackend}

_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
    from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
    from tests.fixtures.vector_store_contract import IDENT
    backend = {"numpy": NumpySidecarBackend, "sqlite": SqliteVectorBackend}[sys.argv[1]]
    store = backend().open(Path(sys.argv[2]), IDENT)
    print("held", flush=True)
    sys.stdin.readline()
    store.close()
    """
)


class Holder:
    """Another process holding a writer open on a base path."""

    def __init__(self, name: str, base: Path) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, name, str(base)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "held"

    @property
    def pid(self) -> int:
        return self.process.pid

    def stop(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.write("\n")
            self.process.stdin.flush()
        self.process.wait(timeout=30)

    def kill(self) -> None:
        self.process.kill()
        self.process.wait(timeout=30)


@pytest.fixture(params=sorted(BACKENDS))
def name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return tmp_path / "emb"


@pytest.fixture
def holder(name: str, base: Path) -> Iterator[Holder]:
    held = Holder(name, base)
    try:
        yield held
    finally:
        held.stop()


def _backend(name: str) -> Any:
    return BACKENDS[name]()


def test_a_second_process_may_not_write(name: str, base: Path, holder: Holder) -> None:
    with pytest.raises(StoreBusyError) as failure:
        _backend(name).open(base, IDENT)
    assert isinstance(failure.value, StoreUnavailableError)
    assert f"pid {holder.pid}" in str(failure.value)


@pytest.mark.usefixtures("holder")
def test_a_second_process_may_not_open_fresh(name: str, base: Path) -> None:
    with pytest.raises(StoreBusyError):
        _backend(name).open_fresh(base, IDENT)


def test_a_killed_writer_leaves_no_lock(name: str, base: Path) -> None:
    Holder(name, base).kill()
    store = _backend(name).open(base, IDENT)
    store.close()
    assert base.with_suffix(".lock").exists()  # never deleted, only unlocked


def test_one_process_never_blocks_itself(name: str, base: Path) -> None:
    backend = _backend(name)
    first = backend.open(base, IDENT)
    second = backend.open(base, IDENT)
    fresh = backend.open_fresh(base, IDENT)
    fresh.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    for store in (first, second, fresh):
        store.close()
    Holder(name, base).stop()  # the lock is free again once all three closed


@pytest.mark.usefixtures("holder")
def test_a_reader_is_never_blocked(name: str, base: Path) -> None:
    reader = _backend(name).open(base, IDENT, owns_files=False)
    reader.close()


def test_a_failed_open_releases_the_lock(name: str, base: Path) -> None:
    base.with_suffix(".v2.json" if name == "numpy" else ".sqlite3").write_text(
        "{not json"
    )
    with pytest.raises(Exception):  # noqa: B017 - each store's own corrupt error
        _backend(name).open(base, IDENT)
    assert os.path.realpath(base.with_suffix(".lock")) not in _writer_lock._HELD
    fd = os.open(base.with_suffix(".lock"), os.O_RDWR)
    try:  # a fresh open file description takes it, so it was released
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions"
)
def test_an_unlockable_directory_writes_as_before(
    name: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.stores"):
            store = _backend(name).open(folder / "emb", IDENT)
        assert store.count == 0
        store.close()
    finally:
        folder.chmod(0o755)
    assert [
        r.args
        for r in caplog.records
        if r.msg.startswith("embeddings_writer_lock_skipped")
    ] == [(str(folder / "emb.lock"), "unwritable")]


def test_without_fcntl_the_writer_proceeds(
    name: str,
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(_writer_lock, "fcntl", None)
    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.stores"):
        _backend(name).open(base, IDENT).close()
    reasons = [
        r.args[1]
        for r in caplog.records
        if r.msg.startswith("embeddings_writer_lock_skipped")
    ]
    assert reasons == ["unsupported"]


def test_the_lock_file_is_named_like_the_store(name: str, tmp_path: Path) -> None:
    store = _backend(name).open(tmp_path / "emb.npy", IDENT)
    store.close()
    assert (tmp_path / "emb.lock").is_file()  # with_suffix, as #819 names files


def test_the_holder_writes_its_pid(base: Path, holder: Holder) -> None:
    assert base.with_suffix(".lock").read_text().strip() == str(holder.pid)


def test_an_unclosed_store_releases_its_share_when_collected(
    name: str, base: Path
) -> None:
    import gc

    store = _backend(name).open(base, IDENT)
    del store
    gc.collect()
    Holder(name, base).stop()  # another process can write again


def test_a_file_system_without_locks_writes_as_before(
    name: str,
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import errno

    def no_locks(_fd: int, _op: int) -> None:
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(_writer_lock.fcntl, "flock", no_locks)
    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.stores"):
        _backend(name).open(base, IDENT).close()
    assert [
        r.args[1]
        for r in caplog.records
        if r.msg.startswith("embeddings_writer_lock_skipped")
    ] == ["unwritable"]


def test_a_holder_that_wrote_no_pid_is_named_generically(
    name: str, base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def held(_fd: int, _op: int) -> None:
        raise BlockingIOError

    monkeypatch.setattr(_writer_lock.fcntl, "flock", held)
    with pytest.raises(StoreBusyError, match=r"written by another process\.$"):
        _backend(name).open(base, IDENT)


def test_a_fresh_open_that_fails_releases_the_lock(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.stores import numpy_sidecar

    def refused(_base: Path) -> int:
        raise PermissionError("stat refused")

    monkeypatch.setattr(numpy_sidecar, "_highest_seq", refused)
    base.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PermissionError):
        NumpySidecarBackend().open_fresh(base, IDENT)
    assert os.path.realpath(base.with_suffix(".lock")) not in _writer_lock._HELD
