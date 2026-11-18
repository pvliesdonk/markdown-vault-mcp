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
        request = ReviewRequest(concept_snapshot(note.content), note.etag)
        result = vault.writer.okf_verify("note.md", "alice", request)
        assert result["verifier"] == "human:alice"
        assert receipts_for(vault).rows()[0]["digest"] == request.snapshot["digest"]
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_verify("note.md", "alice", request)
    finally:
        vault.close()


def test_json_review_keeps_snapshot_and_original_metadata() -> None:
    original = '{\n"type": "concept",\n"weight": 1.00,\n"verified": [{"by": "human:b", "at": "2025-01-01"}]\n}\nBody\n'
    snapshot = concept_snapshot(original)
    result = append_okf_verification(
        original,
        subject="alice",
        now=datetime(2026, 1, 2, tzinfo=UTC),
        reviewed_snapshot=snapshot,
    )
    assert concept_snapshot(result) == snapshot
    assert '"weight": 1.00' in result
    assert result.endswith("}\nBody\n")
    assert len(frontmatter.loads(result).metadata["verified"]) == 2


def test_malformed_verified_refused_without_overwriting() -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    text = "---\nverified: pending-alice\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="verified"):
        append_okf_verification(
            text,
            subject="a",
            now=datetime.now(UTC),
            reviewed_snapshot=concept_snapshot(text),
        )


def test_future_receipts_refused_before_read_and_append(tmp_path) -> None:
    import sqlite3
    from contextlib import closing

    import pytest

    from markdown_vault_mcp._okf_receipts import ReceiptStore
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    path = tmp_path / "receipts.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=2")
    store = ReceiptStore(path)
    with pytest.raises(StoreUnsupportedSchemaError):
        store.rows()
    with pytest.raises(StoreUnsupportedSchemaError):
        store.append(
            {
                "path": "n",
                "by": "a",
                "at": "t",
                "check": "c",
                "digest": "d",
                "recorded_at": "t",
            }
        )


def test_corrupt_receipts_are_not_an_empty_history(tmp_path) -> None:
    import sqlite3

    import pytest

    from markdown_vault_mcp._okf_receipts import ReceiptStore

    path = tmp_path / "receipts.sqlite3"
    path.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        ReceiptStore(path).rows()


def test_receipt_legacy_schema_is_migrated_and_connections_closed(tmp_path) -> None:
    import gc
    import sqlite3
    import warnings
    from contextlib import closing

    from markdown_vault_mcp._okf_receipts import ReceiptStore

    path = tmp_path / "receipts.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE receipts (path TEXT, actor TEXT, at TEXT, check_name TEXT, digest TEXT, recorded_at TEXT)"
        )
    store = ReceiptStore(path)
    receipt = {
        "path": "n",
        "by": "a",
        "at": "t",
        "check": "c",
        "digest": "d",
        "recorded_at": "t",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        store.prepare()
        store.append(receipt)
        assert store.rows() == [receipt]
        gc.collect()
    assert not [
        warning for warning in caught if issubclass(warning.category, ResourceWarning)
    ]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_future_receipt_schema_refuses_before_note_write(tmp_path) -> None:
    import sqlite3
    from contextlib import closing

    import pytest

    from markdown_vault_mcp._okf_review import ReviewRequest
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    path = root / "note.md"
    path.write_text("Body\n")
    state = tmp_path / "state.json"
    with closing(sqlite3.connect(tmp_path / "state.json.receipts.sqlite3")) as db:
        db.execute("PRAGMA user_version=2")
    vault = Vault(
        source_dir=root, settings=VaultSettings(read_only=False, state_path=state)
    )
    try:
        note = vault.reader.read("note.md")
        assert note is not None and note.etag
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.writer.okf_verify(
                "note.md",
                "alice",
                ReviewRequest(concept_snapshot(note.content), note.etag),
            )
        assert path.read_text() == "Body\n"
    finally:
        vault.close()


def test_writer_rejects_empty_subject_and_check(tmp_path) -> None:
    import pytest

    from markdown_vault_mcp._okf_review import ReviewRequest
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    path = tmp_path / "note.md"
    path.write_text("Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None and note.etag
        snapshot = concept_snapshot(note.content)
        for subject, check in [("", "human-review"), ("alice", "")]:
            with pytest.raises(InvalidRequestError):
                vault.writer.okf_verify(
                    "note.md", subject, ReviewRequest(snapshot, note.etag, check)
                )
            assert path.read_text() == "Body\n"
    finally:
        vault.close()


def test_json_review_empty_header_and_existing_extension() -> None:
    for original in (
        "{\n}\nBody\n",
        '{\n"type":"x", "x-mvmcp":{"other":true}\n}\nBody\n',
    ):
        snapshot = concept_snapshot(original)
        result = append_okf_verification(
            original, subject="alice", now=datetime.now(UTC), reviewed_snapshot=snapshot
        )
        assert concept_snapshot(result) == snapshot
        assert (
            frontmatter.loads(result).metadata["x-mvmcp"]["reviews"][0]["snapshot"]
            == snapshot
        )


def test_invalid_extension_and_toml_refused_without_rewriting() -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    for text in (
        "---\nx-mvmcp: bad\n---\nBody\n",
        "---\nx-mvmcp: {reviews: bad}\n---\nBody\n",
        '+++\ntype = "concept"\n+++\nBody\n',
    ):
        with pytest.raises(InvalidRequestError):
            append_okf_verification(
                text,
                subject="a",
                now=datetime.now(UTC),
                reviewed_snapshot=concept_snapshot(text),
            )


def test_receipt_missing_and_invalid_schema_are_unavailable(tmp_path) -> None:
    import sqlite3
    from contextlib import closing

    import pytest

    from markdown_vault_mcp._okf_receipts import ReceiptStore

    path = tmp_path / "receipts.sqlite3"
    store = ReceiptStore(path)
    with pytest.raises(sqlite3.OperationalError):
        store.rows()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE receipts (other TEXT)")
    with pytest.raises(sqlite3.DatabaseError, match="schema"):
        store.prepare()
    with pytest.raises(sqlite3.DatabaseError, match="schema"):
        store.rows()


def test_duplicate_json_review_keys_are_refused() -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    text = '{\n"verified": [], "verified": []\n}\nBody\n'
    with pytest.raises(InvalidRequestError, match="Duplicate"):
        append_okf_verification(
            text,
            subject="a",
            now=datetime.now(UTC),
            reviewed_snapshot=concept_snapshot(text),
        )


def test_missing_receipt_file_is_safe_to_prepare(tmp_path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    ReceiptStore(tmp_path / "new.sqlite3").prepare()


def test_writer_refuses_missing_receipt_store_or_unsafe_transform(
    tmp_path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp._okf_review import ReviewRequest
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    path = tmp_path / "note.md"
    path.write_text("Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None and note.etag
        review = ReviewRequest(concept_snapshot(note.content), note.etag)
        store_factory = vault.writer._receipt_store
        vault.writer._receipt_store = None
        with pytest.raises(InvalidRequestError, match="storage"):
            vault.writer.okf_verify("note.md", "alice", review)
        vault.writer._receipt_store = store_factory
        monkeypatch.setattr(
            "markdown_vault_mcp.facets.writer.concept_snapshot", lambda _: {}
        )
        with pytest.raises(InvalidRequestError, match="preserve"):
            vault.writer.okf_verify("note.md", "alice", review)
        assert path.read_text() == "Body\n"
    finally:
        vault.close()
