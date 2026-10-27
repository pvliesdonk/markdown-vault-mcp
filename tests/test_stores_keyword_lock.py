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
from tests.fixtures.keyword_index_holder import Holder

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = str(Path(__file__).resolve().parents[1])

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
        timeout=60,
    ).stdout.strip()
    return out == "wrote"


def _files(root: Path) -> dict[str, bytes]:
    # The shared-memory index is SQLite's scratch, rewritten by any reader.
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith("-shm")
    }


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


def test_the_refusal_writes_nothing(db: Path, tmp_path: Path) -> None:
    held = Holder(db, note=True)
    try:
        before = _files(tmp_path)
        with pytest.raises(StoreBusyError):
            SqliteKeywordBackend().open(db)
        assert _files(tmp_path) == before
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


def test_an_unclosed_index_releases_its_share_when_collected(db: Path) -> None:
    import gc

    index = SqliteKeywordBackend().open(db)
    del index
    gc.collect()
    assert _another_process_writes(db)


def test_a_vault_that_fails_to_build_releases_the_lock(tmp_path: Path) -> None:
    import dataclasses
    import gc

    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "vault").mkdir()
    db = tmp_path / "index.db"
    settings = dataclasses.replace(VaultSettings(), index_path=db)
    with pytest.raises(ValueError, match="needs an embedding_provider"):
        # Fails after the keyword index opened: a store without a provider.
        Vault(source_dir=tmp_path / "vault", settings=settings, vector_store=object())
    gc.collect()
    assert _another_process_writes(db)


def test_two_names_for_one_index_share_one_lock(tmp_path: Path) -> None:
    real = tmp_path / "real" / "index.db"
    real.parent.mkdir()
    alias = tmp_path / "alias.db"
    held = Holder(real)
    try:
        alias.symlink_to(real)
        with pytest.raises(StoreBusyError):
            SqliteKeywordBackend().open(alias)
    finally:
        held.stop()
