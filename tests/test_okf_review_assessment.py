"""Review assessments keep claims and local receipts separate."""

from markdown_vault_mcp._okf_review import concept_snapshot


def test_receipt_backed_claim_is_not_shadowed() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    current = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    orphan = {**entry, "check": "links", "snapshot": concept_snapshot("Old")}
    actual = {**entry, "check": "human-review", "snapshot": current}
    metadata = {"verified": entry, "x-mvmcp": {"reviews": [orphan, actual]}}
    items = assess_reviews(metadata, current, lambda: [actual])
    assert [(item["snapshot"], item["origin"]) for item in items] == [
        ("differs", "supplied"),
        ("matches", "local_receipt"),
    ]
    metadata = {"verified": [entry, entry], "x-mvmcp": {"reviews": [actual, actual]}}
    items = assess_reviews(metadata, current, lambda: [actual])
    assert [item["snapshot"] for item in items] == ["matches", "matches"]
    assert [item["origin"] for item in items] == ["local_receipt", "local_receipt"]
    metadata["x-mvmcp"]["reviews"].pop()
    assert [
        item["snapshot"] for item in assess_reviews(metadata, current, lambda: [actual])
    ] == ["matches", "unbound"]
    metadata["x-mvmcp"]["reviews"] = [orphan, actual]
    items = assess_reviews(metadata, current, lambda: [orphan, actual])
    assert [(item["snapshot"], item["check"]) for item in items] == [
        ("differs", "links"),
        ("matches", "human-review"),
    ]
    assert [item["origin"] for item in items] == ["local_receipt", "local_receipt"]
    metadata["verified"] = entry
    assert len(assess_reviews(metadata, current, lambda: [orphan, actual])) == 2
    metadata["verified"] = [entry, entry]
    metadata["x-mvmcp"]["reviews"] = [None, {**actual, "by": "human:bob"}]

    def should_not_load():
        raise AssertionError("unmatched claims must not load receipts")

    items = assess_reviews(metadata, current, should_not_load)
    assert [item["snapshot_basis"] for item in items] == [
        "malformed_binding",
        "malformed_binding",
    ]
    assert derive_annotation(metadata, assessments=items)["review_applicability"] == {
        "review": "unknown",
        "basis": "malformed_binding",
    }


def test_unsupported_binding_is_not_absent() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    current = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    claim = {**entry, "check": "links", "snapshot": {**current, "algo": "future"}}
    metadata = {"verified": entry, "x-mvmcp": {"reviews": [claim]}}
    items = assess_reviews(metadata, current, lambda: [claim])
    assert items[0]["origin"] == "local_receipt"
    assert items[0]["snapshot_basis"] == "unsupported_algorithm"
    assert derive_annotation(metadata, assessments=items)["review_applicability"] == {
        "review": "unknown",
        "basis": "unsupported_algorithm",
    }


def test_corrupt_receipts_are_unavailable(tmp_path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore, assess_reviews

    path = tmp_path / "receipts.json"
    path.write_text('{"version": 99, "rows": []}')
    store = ReceiptStore(path, tmp_path / "vault")
    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    claim = {**entry, "check": "links", "snapshot": {**snapshot, "extra": 1}}
    item = assess_reviews(
        {"verified": entry, "x-mvmcp": {"reviews": [claim]}},
        snapshot,
        lambda: store.receipts("note.md"),
    )[0]
    assert (item["snapshot"], item["origin"], item["local_evidence"]) == (
        "matches",
        "supplied",
        "unavailable",
    )


def test_malformed_binding_and_container_are_unknown() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    for extension in (
        {"reviews": [{**entry, "check": 12, "snapshot": None}]},
        {"reviews": [{**entry, "snapshot": {"algo": "x", "digest": "bogus"}}]},
        {"reviews": "bad"},
        "bad",
    ):
        metadata = {"verified": entry, "x-mvmcp": extension}
        items = assess_reviews(metadata, snapshot, None)
        assert items[0]["snapshot_basis"] == "malformed_binding"
        assert items[0]["check"] is None
        assert derive_annotation(metadata, assessments=items)[
            "review_applicability"
        ] == {"review": "unknown", "basis": "malformed_binding"}
        assert (
            derive_annotation(metadata)["review_applicability"]["review"] == "unknown"
        )
    metadata = {
        "verified": entry,
        "x-mvmcp": {"reviews": [{**entry, "snapshot": snapshot}]},
    }
    assert derive_annotation(metadata)["review_applicability"] == {
        "review": "unknown",
        "basis": "snapshot_not_available",
    }


def test_real_vault_read_and_rename(tmp_path) -> None:
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.vault import Vault, VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    original = "---\ntype: concept\nstale_after: 2000-01-01\n---\nBody\n"
    note = source / "note.md"
    note.write_text(original)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        read = read_payload(vault, "note.md")
        item = read["okf"]["review_assessments"][0]
        assert (item["snapshot"], item["origin"], item["check"]) == (
            "matches",
            "local_receipt",
            "human-review",
        )
        assert read["okf"]["freshness"]["state"] == "expired"
        fresh = Vault(
            source_dir=source, settings=VaultSettings(state_path=tmp_path / "fresh")
        )
        try:
            copied = read_payload(fresh, "note.md")["okf"]["review_assessments"][0]
            assert (copied["snapshot"], copied["origin"], copied["local_evidence"]) == (
                "matches",
                "supplied",
                "unavailable",
            )
        finally:
            fresh.close()
        reviewed = note.read_text()
        note.unlink()
        note.write_text(reviewed)
        assert (
            read_payload(vault, "note.md")["okf"]["review_assessments"][0]["origin"]
            == "local_receipt"
        )
        vault.writer.edit("note.md", "Body", "Changed", if_match=read["etag"])
        assert (
            read_payload(vault, "note.md")["okf"]["review_assessments"][0]["snapshot"]
            == "differs"
        )
        vault.writer.rename("note.md", "renamed.md")
        item = read_payload(vault, "renamed.md")["okf"]["review_assessments"][0]
        assert (item["snapshot"], item["origin"], item["local_evidence"]) == (
            "differs",
            "supplied",
            "unavailable",
        )
    finally:
        vault.close()


def test_unattributable_rows_are_unknown() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    for row in (
        "broken",
        {"by": "human:alice"},
        {"by": "human:alice", "at": "2026-01-01"},
    ):
        metadata = {"verified": entry, "x-mvmcp": {"reviews": [row]}}
        items = assess_reviews(metadata, snapshot, None)
        assert items[0]["snapshot_basis"] == "malformed_binding"
    metadata = {
        "verified": {"by": "bad"},
        "x-mvmcp": {"reviews": [{**entry, "snapshot": snapshot}]},
    }
    items = assess_reviews(metadata, snapshot, None)
    assert (
        derive_annotation(metadata, assessments=items)["review_applicability"]
        == derive_annotation(metadata)["review_applicability"]
    )
