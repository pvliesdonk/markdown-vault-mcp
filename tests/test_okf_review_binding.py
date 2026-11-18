"""Review bindings retain portable metadata and the reviewed bytes."""

from datetime import UTC, datetime

import frontmatter

from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.okf import append_okf_verification


def test_review_binding_preserves_body_and_unrelated_header() -> None:
    original = "---\r\ntype: concept # keep\r\ntitle: 'A'\r\n---\r\n\r\nBody\r\n"
    snapshot = concept_snapshot(original)
    result = append_okf_verification(
        original,
        subject="alice",
        now=datetime(2026, 1, 2, tzinfo=UTC),
        reviewed_snapshot=snapshot,
        check="human-review",
    )
    metadata = frontmatter.loads(result).metadata
    assert metadata["verified"] == [{"by": "human:alice", "at": "2026-01-02T00:00:00Z"}]
    assert metadata["x-mvmcp"]["reviews"] == [
        {
            "by": "human:alice",
            "at": "2026-01-02T00:00:00Z",
            "check": "human-review",
            "snapshot": snapshot,
        }
    ]
    assert "type: concept # keep\r\ntitle: 'A'\r\n" in result
    assert result.endswith("---\r\n\r\nBody\r\n")
    assert concept_snapshot(result) == snapshot


def test_snapshot_validation_rejects_stale_and_malformed() -> None:
    import pytest

    from markdown_vault_mcp._okf_review import validate_reviewed_snapshot
    from markdown_vault_mcp.exceptions import InvalidRequestError

    text = "---\ntype: concept\n---\nBody"
    snapshot = concept_snapshot(text)
    assert validate_reviewed_snapshot(text, snapshot) == snapshot
    for bad in ({}, {**snapshot, "digest": "x"}, concept_snapshot(text + " changed")):
        with pytest.raises(InvalidRequestError):
            validate_reviewed_snapshot(text, bad)


def test_receipts_are_local_and_persistent(tmp_path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    path = tmp_path / "state.receipts.sqlite3"
    receipt = {
        "path": "note.md",
        "by": "human:a",
        "at": "2026-01-02T00:00:00Z",
        "check": "human-review",
        "digest": "a" * 64,
        "recorded_at": "2026-01-02T00:00:01Z",
    }
    ReceiptStore(path).append(receipt)
    assert ReceiptStore(path).rows() == [receipt]
    assert ReceiptStore(None).rows() == []


def test_writer_facet_verifies_and_refuses_stale_etag(tmp_path) -> None:
    import pytest

    from markdown_vault_mcp._okf_receipts import receipts_for
    from markdown_vault_mcp._okf_review import ReviewRequest
    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    path = tmp_path / "note.md"
    path.write_text("---\ntype: concept\n---\nBody\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None and note.etag
        request = ReviewRequest("alice", concept_snapshot(note.content), note.etag)
        result = vault.writer.okf_verify("note.md", request)
        assert result["verifier"] == "human:alice"
        assert receipts_for(vault).rows()[0]["digest"] == request.snapshot["digest"]
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_verify("note.md", request)
    finally:
        vault.close()
