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

    def should_not_load():
        raise AssertionError("unmatched claims must not load receipts")

    metadata["x-mvmcp"]["reviews"] = [{**actual, "by": "human:bob"}]
    assert all(
        "snapshot_basis" not in item
        for item in assess_reviews(metadata, current, should_not_load)
    )
    metadata["x-mvmcp"]["reviews"] = [actual, "broken"]
    items = assess_reviews(metadata, current, lambda: [actual])
    assert (items[0]["snapshot"], items[0]["origin"]) == ("matches", "local_receipt")
    assert derive_annotation(metadata, assessments=items)["review_applicability"] == {
        "review": "unknown",
        "basis": "malformed_binding",
    }
    metadata["x-mvmcp"]["reviews"] = [None, {**actual, "by": "human:bob"}]
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
    claim = {**entry, "snapshot": snapshot, "check": 123}
    item = assess_reviews(
        {"verified": entry, "x-mvmcp": {"reviews": [claim]}}, snapshot, None
    )[0]
    assert (item["snapshot"], item["check"], item["check_basis"]) == (
        "matches",
        None,
        "malformed_binding",
    )
    metadata = {
        "verified": entry,
        "x-mvmcp": {"reviews": [{**entry, "snapshot": snapshot}]},
    }
    assert "check_basis" not in assess_reviews(metadata, snapshot, None)[0]
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
        {"by": "invalid actor", "at": entry["at"]},
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


def test_whole_read_scales_with_review_count(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import yaml

    from markdown_vault_mcp import _okf_receipts as receipts
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.vault import Vault, VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    snapshot = concept_snapshot("---\ntype: concept\n---\nBody\n")
    entries = [
        {
            "by": "human:alice",
            "at": (datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=i)).isoformat(),
        }
        for i in range(800)
    ]
    claims = [
        {**entry, "check": "human-review", "snapshot": snapshot} for entry in entries
    ]
    text = (
        "---\n"
        + yaml.safe_dump(
            {"type": "concept", "verified": entries, "x-mvmcp": {"reviews": claims}},
            sort_keys=False,
        )
        + "---\nBody\n"
    )
    assert len(text.encode()) < 262144
    (source / "note.md").write_text(text)
    calls = 0
    key = receipts._review_key

    def counted(row):
        nonlocal calls
        calls += 1
        return key(row)

    monkeypatch.setattr(receipts, "_review_key", counted)
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        result = read_payload(vault, "note.md")
        assert len(result["okf"]["review_assessments"]) == 800
        assert calls < 16000, f"{calls} identity parses for 800 reviews"
    finally:
        vault.close()


def test_reviewed_whole_read_loads_persistent_receipts_once(tmp_path, monkeypatch):
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp._okf_receipts import ReceiptStore
    from markdown_vault_mcp.vault import Vault, VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    original = "---\ntype: concept\n---\nBody\n"
    (source / "note.md").write_text(original)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        calls = []
        original_load = ReceiptStore._load

        def counted(self):
            calls.append(self._path)
            return original_load(self)

        monkeypatch.setattr(ReceiptStore, "_load", counted)
        read_payload(vault, "note.md")
        assert len(calls) == 1
        assert calls[0] is not None
        calls.clear()
        from markdown_vault_mcp._okf_receipts import review_evidence

        review_evidence(
            vault.reader._receipt_store, "note.md", {}, concept_snapshot(original)
        )
        assert not calls
        assessments, observation = review_evidence(
            None, "note.md", {}, concept_snapshot(original)
        )
        assert assessments == [] and observation == {"state": "unavailable"}

        def unavailable(self):
            calls.append(self._path)
            raise OSError("unreadable")

        monkeypatch.setattr(ReceiptStore, "_load", unavailable)
        result = read_payload(vault, "note.md")["okf"]
        assert len(calls) == 1
        assert result["observation"]["state"] == "unavailable"
        assert result["review_assessments"][0]["local_evidence"] == "unavailable"
    finally:
        vault.close()
