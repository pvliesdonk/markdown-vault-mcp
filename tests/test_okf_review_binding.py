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


def test_review_preserves_extension_source_lexemes() -> None:
    text = "---\nx-mvmcp:\n  precise: 0.1234567890123456789\n  reviews: []\n---\nBody\n"
    result = append_okf_verification(
        text,
        subject="alice",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(text),
    )
    assert "precise: 0.1234567890123456789" in result


def test_review_accepts_null_stubs() -> None:
    text = "---\nverified: null\nx-mvmcp: null\n---\nBody\n"
    result = append_okf_verification(
        text,
        subject="alice",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(text),
    )
    assert frontmatter.loads(result).metadata["x-mvmcp"]["reviews"]


def test_existing_mapping_review_keeps_precision() -> None:
    text = "---\nverified:\n  by: human:b\n  score: 0.1234567890123456789\nx-mvmcp: {other: 0x10}\n---\nBody\n"
    result = append_okf_verification(
        text,
        subject="a",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(text),
    )
    assert "score: 0.1234567890123456789" in result
    assert "other: 0x10" in result
    assert len(frontmatter.loads(result).metadata["verified"]) == 2


def test_receipt_store_persists_and_refuses_bad_state(tmp_path) -> None:
    import json

    import pytest

    from markdown_vault_mcp._okf_receipts import ReceiptStore
    from markdown_vault_mcp.exceptions import (
        InvalidRequestError,
        StoreUnsupportedSchemaError,
    )

    vault = tmp_path / "vault"
    vault.mkdir()
    path = tmp_path / "receipts.json"
    store = ReceiptStore(path, vault)
    store.check()
    row = {"snapshot": {"algo": "mvmcp-concept-v1", "digest": "abc"}}
    store.append(row)
    assert json.loads(path.read_text())["rows"] == [row]
    for bad in ("broken", '{"version":2,"rows":[]}'):
        path.write_text(bad)
        with pytest.raises(StoreUnsupportedSchemaError):
            store.check()
        with pytest.raises(StoreUnsupportedSchemaError):
            store.append(row)
    with pytest.raises(InvalidRequestError, match="outside"):
        ReceiptStore(vault / "receipts.json", vault).check()


def test_non_yaml_reviews_refuse_without_conversion() -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    for text in (
        '{\n"type":"concept"\n}\nBody\n',
        '+++\ntype = "concept"\n+++\nBody\n',
    ):
        with pytest.raises(InvalidRequestError, match=r"convert.*YAML"):
            append_okf_verification(
                text,
                subject="a",
                now=datetime.now(UTC),
                reviewed_snapshot=concept_snapshot(text),
            )


def test_review_splices_existing_sequences_without_touching_values() -> None:
    variants = [
        "verified:\n  - by: human:b\n    precision: 0.1234567890123456789 # kept\nx-mvmcp:\n  date: 2025-01-01\n  hex: 0x10\n  reviews:\n    - by: human:b\n      precision: 0.1234567890123456789 # kept\n",
        "verified: [{by: human:b, precision: 0.1234567890123456789,}]\nx-mvmcp: {hex: 0x10, reviews: [{by: human:b, precision: 0.1234567890123456789,}],}\n",
        "x-mvmcp:\n  precision: 0.1234567890123456789\n  date: 2025-01-01\n",
        "x-mvmcp: {precision: 0.1234567890123456789, reviews: null}\n",
    ]
    for header in variants:
        text = "---\n" + header + "---\nBody\n"
        result = append_okf_verification(
            text,
            subject="a",
            now=datetime.now(UTC),
            reviewed_snapshot=concept_snapshot(text),
        )
        assert "0.1234567890123456789" in result
        assert concept_snapshot(result) == concept_snapshot(text)
        if "0x10" in text:
            assert "0x10" in result
        if "# kept" in text:
            assert result.count("# kept") == text.count("# kept")


def test_review_rejects_duplicate_and_aliased_fields() -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    for header in ("verified: []\nverified: []\n", "base: &base []\nverified: *base\n"):
        text = "---\n" + header + "---\nBody\n"
        with pytest.raises(InvalidRequestError):
            append_okf_verification(
                text,
                subject="a",
                now=datetime.now(UTC),
                reviewed_snapshot=concept_snapshot(text),
            )


def test_unrelated_yaml_alias_is_preserved() -> None:
    text = "---\nbase: &a {k: 1}\nother: *a\n---\nBody\n"
    result = append_okf_verification(
        text,
        subject="a",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(text),
    )
    assert "base: &a {k: 1}\nother: *a\n" in result
    assert concept_snapshot(result) == concept_snapshot(text)


def test_flow_mapping_review_keeps_precision() -> None:
    text = "---\nverified: {by: human:b, precision: 0.1234567890123456789}\n---\nBody\n"
    result = append_okf_verification(
        text,
        subject="a",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(text),
    )
    assert "{by: human:b, precision: 0.1234567890123456789}" in result
    assert len(frontmatter.loads(result).metadata["verified"]) == 2
    assert concept_snapshot(result) == concept_snapshot(text)


def test_facet_rejects_stale_and_malformed_snapshots(tmp_path) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    text = "---\ntype: concept\n---\nBody\n"
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        snapshot = concept_snapshot(text)
        assert vault.writer.require_reviewable("note.md", snapshot).content == text
        for bad in (
            {},
            {**snapshot, "digest": "x"},
            concept_snapshot(text + "changed"),
        ):
            with pytest.raises(InvalidRequestError, match="snapshot"):
                vault.writer.require_reviewable("note.md", bad)
    finally:
        vault.close()


def test_unconfigured_receipt_store_is_refused(tmp_path) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    path = tmp_path / "note.md"
    path.write_text("Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer._receipt_store = None
        with pytest.raises(InvalidRequestError, match="storage"):
            vault.writer.okf_verify("note.md", "alice", concept_snapshot("Body\n"))
        assert path.read_text() == "Body\n"
    finally:
        vault.close()
