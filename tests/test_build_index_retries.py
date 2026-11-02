"""A per-note build failure must leave the note retryable (#1982)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast
from unittest import mock

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from tests.test_index_coordinator import make_coordinator

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import ParsedNote


@pytest.mark.parametrize("force", [False, True])
def test_reindex_retries_a_note_that_failed_during_build(
    tmp_path: Path, force: bool
) -> None:
    """Persist the failure across a restart, then index the unchanged bytes."""
    path = tmp_path / "b.md"
    path.write_text("# B\n\nbravo\n", encoding="utf-8")
    first = make_coordinator(tmp_path)
    try:
        first.build_index()
    finally:
        first.close(timeout=5)
        cast("FTSIndex", first._fts).close()

    path.write_text("# B\n\nbravo changed\n", encoding="utf-8")
    expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    real_upsert = FTSIndex.upsert_note

    def refuse_b(self: FTSIndex, note: ParsedNote) -> int:
        if note.path == "b.md":
            raise ValueError("temporary note indexing failure")
        return real_upsert(self, note)

    rebuilt = make_coordinator(tmp_path, title_field="name")
    try:
        with mock.patch.object(FTSIndex, "upsert_note", refuse_b):
            stats = rebuilt.build_index(force=force)
        assert stats.documents_indexed == 1
        row = cast("FTSIndex", rebuilt._fts).get_note("b.md")
        if force:
            assert row is None
        else:
            assert row is not None
            assert row["content_hash"] != expected_hash
    finally:
        rebuilt.close(timeout=5)
        cast("FTSIndex", rebuilt._fts).close()

    recovered = make_coordinator(tmp_path, title_field="name")
    try:
        assert recovered.build_index().chunks_indexed == 0
        result = recovered.reindex()
        assert (result.added, result.modified, result.deleted, result.unchanged) == (
            0,
            1,
            0,
            1,
        )
        row = cast("FTSIndex", recovered._fts).get_note("b.md")
        assert row is not None
        assert row["content_hash"] == expected_hash
        settled = recovered.reindex()
        assert (
            settled.added,
            settled.modified,
            settled.deleted,
            settled.unchanged,
        ) == (
            0,
            0,
            0,
            2,
        )
    finally:
        recovered.close(timeout=5)
        cast("FTSIndex", recovered._fts).close()
