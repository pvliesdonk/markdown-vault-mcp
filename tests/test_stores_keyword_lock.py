"""One process owns an on-disk keyword index at a time (#1890, #1896)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import (
    StoreBusyError,
    StoreCorruptError,
    StoreUnavailableError,
)
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = str(Path(__file__).resolve().parents[1])

# Opens the index as its owner, optionally after indexing one note, and holds
# it until a line arrives on stdin.
_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
    from markdown_vault_mcp.types import Chunk
    from tests.test_fts_index import make_note
    index = SqliteKeywordBackend().open(Path(sys.argv[1]))
    if sys.argv[2] == "note":
        chunk = Chunk(heading=None, heading_level=0, content="Dragons.", start_line=0)
        index.upsert_note(make_note("alpha.md", chunks=[chunk]))
    print("held", flush=True)
    sys.stdin.readline()
    index.close()
    """
)

_PROBE = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from markdown_vault_mcp.exceptions import StoreBusyError
    from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
    try:
        SqliteKeywordBackend().open(Path(sys.argv[1])).close()
    except StoreBusyError:
        print("busy")
    else:
        print("wrote")
    """
)


def _another_process_writes(db: Path) -> bool:
    """Whether another process may open *db* as its owner now."""
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(db)],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=True,
    ).stdout.strip()
    return out == "wrote"


class Holder:
    """Another process owning the keyword index at *db*."""

    def __init__(self, db: Path, *, note: bool = False) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(db), "note" if note else "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=_ROOT,
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


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "index.db"


@pytest.fixture
def holder(db: Path) -> Iterator[Holder]:
    held = Holder(db)
    try:
        yield held
    finally:
        held.stop()


def test_a_second_process_may_not_write_the_index(db: Path, holder: Holder) -> None:
    with pytest.raises(StoreBusyError) as failure:
        SqliteKeywordBackend().open(db)
    assert isinstance(failure.value, StoreUnavailableError)
    assert str(failure.value).startswith(f"Keyword index at {db} is being written")
    assert f"pid {holder.pid}" in str(failure.value)
    assert failure.value.backend == "sqlite"
    assert failure.value.location == str(db)


def test_the_refusal_writes_nothing(db: Path) -> None:
    held = Holder(db, note=True)
    try:
        wal = Path(f"{db}-wal")
        before = (db.read_bytes(), wal.read_bytes() if wal.exists() else None)
        with pytest.raises(StoreBusyError):
            SqliteKeywordBackend().open(db)
        assert (db.read_bytes(), wal.read_bytes() if wal.exists() else None) == before
        reader = SqliteKeywordBackend().open(db, owns_files=False)
        try:
            assert reader.count_documents() == 1
        finally:
            reader.close()
    finally:
        held.stop()


def test_a_first_open_locks_before_the_file_exists(tmp_path: Path) -> None:
    db = tmp_path / "new" / "state" / "index.db"
    held = Holder(db)
    try:
        assert Path(f"{db}.lock").is_file()
        assert not _another_process_writes(db)
    finally:
        held.stop()


def test_a_read_only_open_takes_no_lock(db: Path, holder: Holder) -> None:
    del holder  # owns the index while this process reads it
    reader = SqliteKeywordBackend().open(db, owns_files=False)
    try:
        assert reader.search("dragons") == []
    finally:
        reader.close()


@pytest.mark.parametrize("location", [None, ":memory:"])
def test_an_in_memory_index_takes_no_lock(
    location: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    SqliteKeywordBackend().open(location).close()
    assert list(tmp_path.rglob("*.lock")) == []


def test_the_same_process_may_open_twice(db: Path) -> None:
    first = SqliteKeywordBackend().open(db)
    second = SqliteKeywordBackend().open(db)
    first.close()
    assert not _another_process_writes(db)  # the second still holds a share
    second.close()
    assert _another_process_writes(db)


def test_close_releases_the_lock(db: Path) -> None:
    SqliteKeywordBackend().open(db).close()
    assert _another_process_writes(db)


def _lock_is_free(lock: Path) -> bool:
    """Whether another process may take *lock* now, whatever the index holds."""
    probe = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    print('busy')\n"
        "else:\n"
        "    print('free')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe, str(lock)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out == "free"


def test_a_failed_open_releases_the_lock(db: Path) -> None:
    db.write_bytes(b"not a database at all, just bytes " * 200)
    with pytest.raises(StoreCorruptError):
        SqliteKeywordBackend().open(db)
    assert _lock_is_free(Path(f"{db}.lock"))
