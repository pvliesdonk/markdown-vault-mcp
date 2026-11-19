"""Review assessments keep claims and local receipts separate."""

from markdown_vault_mcp._okf_review import concept_snapshot


def test_claims_and_receipts_are_consumed_once() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    claim = {**entry, "check": "links", "snapshot": snapshot}
    metadata = {"verified": [entry, entry], "x-mvmcp": {"reviews": [claim, claim]}}
    items = assess_reviews(metadata, snapshot, lambda: [claim])
    assert [item["snapshot"] for item in items] == ["matches", "matches"]
    assert [item["origin"] for item in items] == ["local_receipt", "supplied"]
    metadata["x-mvmcp"]["reviews"].pop()
    items = assess_reviews(metadata, snapshot, lambda: [claim])
    assert [item["snapshot"] for item in items] == ["matches", "unbound"]


def test_unmatched_claim_does_not_contradict_aggregate() -> None:
    from markdown_vault_mcp.okf import derive_annotation

    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    assessments = [
        {
            **entry,
            "snapshot": "unbound",
            "origin": "supplied",
            "check": None,
            "local_evidence": "unavailable",
        }
    ]
    metadata = {"verified": [entry], "x-mvmcp": {"reviews": [{"by": "human:bob"}]}}
    assert derive_annotation(metadata, assessments=assessments)[
        "review_applicability"
    ] == {"snapshot": "unbound", "basis": "no_snapshot_binding"}


def test_independent_dimensions_and_receipt_failures() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    claim = {**entry, "check": "links", "snapshot": {**snapshot, "extra": 1}}
    metadata = {"verified": [entry], "x-mvmcp": {"reviews": [claim]}}
    item = assess_reviews(metadata, snapshot, lambda: None)[0]
    assert (item["snapshot"], item["check"], item["origin"]) == (
        "matches",
        "links",
        "supplied",
    )
    claim["snapshot"]["algo"] = "future"
    item = assess_reviews(metadata, snapshot, lambda: [claim])[0]
    assert (item["snapshot"], item["check"], item["origin"]) == (
        "unbound",
        "links",
        "local_receipt",
    )
    metadata["x-mvmcp"]["reviews"] = [None, {**claim, "by": "human:bob"}]

    def should_not_load():
        raise AssertionError("unmatched claims must not load receipts")

    assert (
        assess_reviews(metadata, snapshot, should_not_load)[0]["snapshot"] == "unbound"
    )


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
        from markdown_vault_mcp._cli_read import _context_annotations

        context = {"frontmatter": read["frontmatter"]}
        _context_annotations(vault, context, "note.md")
        assert context["okf"]["review_applicability"] == {
            "review": "unknown",
            "basis": "snapshot_not_available",
        }
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
        assert (item["origin"], item["local_evidence"]) == ("supplied", "unavailable")
    finally:
        vault.close()
    fresh = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "fresh")
    )
    try:
        assert (
            read_payload(fresh, "renamed.md")["okf"]["review_assessments"][0]["origin"]
            == "supplied"
        )
    finally:
        fresh.close()


def test_distinct_claims_and_compact_annotations() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    snapshot = concept_snapshot("Body")
    old = concept_snapshot("Old")
    entry = {"by": "process:tool", "at": "2026-01-01T00:00:00Z"}
    claims = [
        {**entry, "check": "schema", "snapshot": old},
        {**entry, "check": "links", "snapshot": snapshot},
    ]
    metadata = {"verified": [entry, entry], "x-mvmcp": {"reviews": claims}}
    items = assess_reviews(metadata, snapshot, lambda: list(claims))
    assert [(item["snapshot"], item["check"], item["origin"]) for item in items] == [
        ("differs", "schema", "local_receipt"),
        ("matches", "links", "local_receipt"),
    ]
    assert derive_annotation(metadata)["review_applicability"] == {
        "review": "unknown",
        "basis": "snapshot_not_available",
    }
    assert (
        derive_annotation({"verified": entry})["review_applicability"]["snapshot"]
        == "unbound"
    )


def test_corrupt_receipts_are_unavailable(tmp_path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    path = tmp_path / "receipts.json"
    path.write_text('{"version": 99, "rows": []}')
    store = ReceiptStore(path, tmp_path / "vault")
    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    claim = {**entry, "check": "links", "snapshot": snapshot}
    item = store.review_assessments(
        "note.md", {"verified": entry, "x-mvmcp": {"reviews": [claim]}}, snapshot
    )[0]
    assert (item["snapshot"], item["origin"], item["local_evidence"]) == (
        "matches",
        "supplied",
        "unavailable",
    )


def test_malformed_binding_and_absent_store() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    for binding in (None, {"algo": "x", "digest": "invalid"}):
        metadata = {
            "verified": entry,
            "x-mvmcp": {"reviews": [{**entry, "check": 12, "snapshot": binding}]},
        }
        item = assess_reviews(metadata, snapshot, None)[0]
        assert (item["snapshot"], item["check"], item["origin"]) == (
            "unbound",
            None,
            "supplied",
        )


def test_receipt_backed_claim_is_not_shadowed() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews

    current = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    orphan = {**entry, "check": "links", "snapshot": concept_snapshot("Old")}
    actual = {**entry, "check": "human-review", "snapshot": current}
    metadata = {"verified": entry, "x-mvmcp": {"reviews": [orphan, actual]}}
    item = assess_reviews(metadata, current, lambda: [actual])[0]
    assert (item["snapshot"], item["check"], item["origin"]) == (
        "matches",
        "human-review",
        "local_receipt",
    )


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


def test_malformed_binding_and_container_are_unknown() -> None:
    from markdown_vault_mcp._okf_receipts import assess_reviews
    from markdown_vault_mcp.okf import derive_annotation

    snapshot = concept_snapshot("Body")
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    for extension in (
        {"reviews": [{**entry, "snapshot": {"algo": "x", "digest": "bogus"}}]},
        {"reviews": "bad"},
        "bad",
    ):
        metadata = {"verified": entry, "x-mvmcp": extension}
        items = assess_reviews(metadata, snapshot, None)
        assert items[0]["snapshot_basis"] == "malformed_binding"
        assert derive_annotation(metadata, assessments=items)[
            "review_applicability"
        ] == {"review": "unknown", "basis": "malformed_binding"}
        assert (
            derive_annotation(metadata)["review_applicability"]["review"] == "unknown"
        )
