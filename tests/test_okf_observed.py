"""Local observations alongside supplied review claims."""

from pathlib import Path

import pytest

from markdown_vault_mcp._cli_read import read_payload
from markdown_vault_mcp.vault import Vault


def test_removed_entry_keeps_hidden_and_excluded_labels(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
    from markdown_vault_mcp.vault import VaultSettings

    (tmp_path / ".hidden").mkdir()
    note = tmp_path / ".hidden" / "note.md"
    note.write_text("Body")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(exclude_patterns=[".hidden/**"])
    )
    try:
        vault.index.build_index()
        present = next(
            e
            for e in vault.reader.okf_inventory(BundleTreeRequest(root="")).entries
            if e.path == ".hidden/note.md"
        )
        note.unlink()
        vault._index_mgr.observations.sweep()
        removed = next(
            e
            for e in vault.reader.okf_inventory(BundleTreeRequest(root="")).entries
            if e.path == ".hidden/note.md"
        )
        assert (removed.hidden_path, removed.visibility) == (
            present.hidden_path,
            present.visibility,
        )
    finally:
        vault.close()


@pytest.mark.parametrize("all_removed", [True, False])
def test_removed_probes_are_bounded_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, all_removed: bool
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest

    count = 1001 if all_removed else 1002
    for i in range(count):
        (tmp_path / f"{i:04}.md").write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        for i in range(0, count, 1 if all_removed else 2):
            (tmp_path / f"{i:04}.md").unlink()
        vault._index_mgr.observations.sweep()
        seen: list[str] = []
        original = BundleInspector.path_presence

        def counted(self, path):
            seen.append(path)
            return original(self, path)

        monkeypatch.setattr(BundleInspector, "path_presence", counted)
        first = vault.reader.okf_inventory(BundleTreeRequest(root="", entry_limit=1000))
        if all_removed:
            assert len(seen) == 1000
        assert first.next_cursor is not None
        seen.clear()
        second = vault.reader.okf_inventory(
            BundleTreeRequest(root="", entry_limit=1000, cursor=first.next_cursor)
        )
        if all_removed:
            assert len(seen) == 1
            assert second.next_cursor is None
        paths = [e.path for e in (*first.entries, *second.entries)]
        cursor = second.next_cursor
        while cursor is not None:
            page = vault.reader.okf_inventory(
                BundleTreeRequest(root="", entry_limit=1000, cursor=cursor)
            )
            paths.extend(e.path for e in page.entries)
            cursor = page.next_cursor
        assert paths == [f"{i:04}.md" for i in range(count)]
    finally:
        vault.close()


def test_mixed_inventory_pages_and_reappearing_path(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    for name in ("a.md", "c.md", "e.md"):
        (tmp_path / name).write_text("old")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        for name in ("a.md", "c.md", "e.md"):
            (tmp_path / name).unlink()
        vault._index_mgr.observations.sweep()
        for name in ("b.md", "c.md", "d.md"):
            (tmp_path / name).write_text("new")
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert page.removal_history == "available"
        assert (
            vault.reader.inspect_bundle_tree(BundleTreeRequest(root="")).removal_history
            == "not_inspected"
        )
        entries = [(e.path, e.presence) for e in page.entries]
        assert entries == [
            ("a.md", "removed"),
            ("b.md", "present"),
            ("c.md", "present"),
            ("d.md", "present"),
            ("e.md", "removed"),
        ]
    finally:
        vault.close()


def test_reappeared_large_note_removal_probe_has_bounded_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.bundle_inventory as inventory
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    note = tmp_path / "note.md"
    note.write_text("original")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        note.unlink()
        vault._index_mgr.observations.sweep()
        note.write_bytes(b"x" * (4 * 1024 * 1024))
        total = 0
        original = inventory._observation_digests
        original_read = inventory.read_regular_nofollow

        def counted(stream):
            nonlocal total
            result = original(stream)
            total += stream.tell()
            return result

        def counted_read(parent, name, limit):
            nonlocal total
            result = original_read(parent, name, limit)
            total += len(result)
            return result

        monkeypatch.setattr(inventory, "_observation_digests", counted)
        monkeypatch.setattr(inventory, "read_regular_nofollow", counted_read)
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert [(e.path, e.presence) for e in page.entries] == [("note.md", "present")]
        assert total <= 1024 * 1024
    finally:
        vault.close()


def test_local_review_and_immediate_edit(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = source / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        before = read_payload(vault, "note.md")
        assert before["okf"]["observation"] == {"state": "unavailable"}
        vault.writer.edit("note.md", "Body", "Changed", if_match=before["etag"])
        after = read_payload(vault, "note.md")
        assert after["okf"]["observation"] == {
            "state": "available",
            "later_change_observed": True,
        }
        assert after["frontmatter"]["verified"] == before["frontmatter"]["verified"]
        assert after["okf"]["trust_tier"] == before["okf"]["trust_tier"]
    finally:
        vault.close()


def test_removal_outside_scope_does_not_reduce_live_page(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    (tmp_path / "selected").mkdir()
    for i in range(600):
        (tmp_path / "selected" / f"{i:04}.md").write_text("Body")
    removed = tmp_path / "outside.md"
    removed.write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        removed.unlink()
        vault._index_mgr.observations.sweep()
        page = vault.reader.okf_inventory(
            BundleTreeRequest(root="selected", entry_limit=1000)
        )
        assert len(page.entries) == 600
        assert page.next_cursor is None
    finally:
        vault.close()


@pytest.mark.parametrize("receipts", [1, 2])
@pytest.mark.parametrize(
    "extra",
    [
        {"by": "human:bob", "at": "2021-01-01T00:00:00Z"},
        {"by": "human:bob", "at": "2020-01-01T00:00:00Z"},
        {"by": "human:alice", "at": "2020-01-01T00:00:00+00:00"},
    ],
)
def test_latest_unreceipted_review_makes_comparison_unknown(
    tmp_path: Path, extra: dict[str, str], receipts: int
) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)
    snapshot = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    alice = {"by": "human:alice", "at": "2020-01-01T00:00:00Z"}
    for _ in range(receipts):
        store.append({"path": "note.md", **alice, "snapshot": snapshot})
    current = {**snapshot, "digest": "b" * 64}
    assert store.review_observation(
        "note.md", {"verified": [alice, extra]}, current
    ) == {"state": "unavailable"}
    assert store.review_observation("other.md", {"verified": [alice]}, current) == {
        "state": "unavailable"
    }


def test_failed_second_same_instant_review_receipt_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    import markdown_vault_mcp.facets.writer as writer
    from markdown_vault_mcp._okf_receipts import ReceiptNotRecordedError
    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.vault import VaultSettings

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 -- same signature as datetime.now
            return cls(2026, 11, 18, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(writer, "datetime", FixedDatetime)
    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    original = "---\ntype: concept\n---\nBody\n"
    (source / "note.md").write_text(original)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        before = read_payload(vault, "note.md")
        vault.writer.edit("note.md", "Body", "Changed", if_match=before["etag"])
        changed = read_payload(vault, "note.md")

        def fail(_receipt):
            raise OSError("disk full")

        monkeypatch.setattr(vault.writer._receipt_store, "append", fail)
        with pytest.raises(ReceiptNotRecordedError):
            vault.writer.okf_verify("note.md", "alice", changed["okf"]["snapshot"])
        after = read_payload(vault, "note.md")
        assert len(after["frontmatter"]["verified"]) == 2
        assert after["okf"]["observation"] == {"state": "unavailable"}
    finally:
        vault.close()


async def test_mcp_read_exposes_receipt_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client

    from tests.conftest import wait_for_mcp_writer_drain
    from tests.server_factory import make_server

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (source / "note.md").write_text("---\ntype: concept\n---\nBody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        result = await client.call_tool("read", {"path": "note.md"})
        assert result.structured_content["okf"]["observation"] == {
            "state": "unavailable"
        }


@pytest.mark.parametrize(
    "contents",
    [
        None,
        "not json",
        '{"version": 99, "rows": []}',
        '{"version": 1, "rows": [{"path": "note.md", "at": "2099-01-01T00:00:00Z", "by": ["human:a"]}]}',
        '{"version": 1, "rows": [{"path": "note.md", "at": "2099-01-01T00:00:00Z", "by": {}}]}',
    ],
)
def test_receipt_read_error_is_unavailable(
    tmp_path: Path, contents: str | None
) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    receipt_path = tmp_path if contents is None else tmp_path / "receipts.json"
    if contents is not None:
        receipt_path.write_text(contents)
    store = ReceiptStore(receipt_path, tmp_path / "vault")
    metadata = {"verified": [{"by": "human:a", "at": "2099-01-01T00:00:00Z"}]}
    snapshot = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    assert store.review_observation("note.md", metadata, snapshot) == {
        "state": "unavailable"
    }


def test_latest_receipt_is_used_for_matching_identity(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)
    first = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    second = {"algo": "mvmcp-concept-v1", "digest": "b" * 64}
    review = {"by": "human:a", "at": "2020-01-01T00:00:00Z"}
    store.append({"path": "note.md", **review, "snapshot": first})
    assert store.review_observation("note.md", {"verified": review}, first) == {
        "state": "unavailable"
    }
    assert store.review_observation("note.md", {"verified": review}, second) == {
        "state": "available",
        "later_change_observed": True,
    }
    assert store.review_observation("note.md", {"verified": review}, None) == {
        "state": "unavailable"
    }
    dated_review = {**review, "at": datetime(2020, 1, 1, tzinfo=UTC)}
    assert store.review_observation("note.md", {"verified": dated_review}, second) == {
        "state": "available",
        "later_change_observed": True,
    }
    store.append({"path": "note.md", **review, "snapshot": second})
    third = {"algo": "mvmcp-concept-v1", "digest": "c" * 64}
    assert store.review_observation("note.md", {"verified": review}, third) == {
        "state": "unavailable"
    }
    unknown = ReceiptStore(None, tmp_path)
    unknown.append(
        {
            "path": "note.md",
            **review,
            "snapshot": {"algo": "future-concept-v2", "digest": "c" * 64},
        }
    )
    assert unknown.review_observation("note.md", {"verified": review}, first) == {
        "state": "unavailable"
    }
    assert store.review_observation("note.md", {}, first) == {"state": "unavailable"}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("case", ["equal_any", "all_different", "invalid"])
def test_tied_distinct_reviews_compare_every_receipt(
    tmp_path: Path, reverse: bool, case: str
) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)

    def snapshot(digest: str) -> dict[str, str]:
        return {"algo": "mvmcp-concept-v1", "digest": digest * 64}

    reviews = [
        {"by": f"human:{name}", "at": "2020-01-01T00:00:00Z"}
        for name in ("alice", "bob")
    ]
    snapshots = [snapshot("a"), snapshot("b")]
    if case == "invalid":
        snapshots[1] = {"algo": "future-concept-v2", "digest": "b" * 64}
    rows = [
        {"path": "note.md", **review, "snapshot": baseline}
        for review, baseline in zip(reviews, snapshots, strict=True)
    ]
    for row in reversed(rows) if reverse else rows:
        store.append(row)
    current = snapshot("a" if case == "equal_any" else "c")
    expected = (
        {"state": "available", "later_change_observed": True}
        if case == "all_different"
        else {"state": "unavailable"}
    )
    assert (
        store.review_observation("note.md", {"verified": reviews}, current) == expected
    )


@pytest.mark.parametrize("existing_target", [True, False])
def test_removed_folder_paging_and_blocked_scope(
    tmp_path: Path, existing_target: bool
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    folder = tmp_path / "folder"
    folder.mkdir()
    for i in range(5):
        (folder / f"{i}.md").write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
        vault._index_mgr.observations.sweep()
        cursor = None
        paths = []
        while True:
            page = vault.reader.okf_inventory(
                BundleTreeRequest(root="folder", entry_limit=2, cursor=cursor)
            )
            if cursor is None:
                assert page.state == "missing"
            paths.extend((e.path, e.presence) for e in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert paths == [(f"folder/{i}.md", "removed") for i in range(5)]
        target = tmp_path / "target"
        if existing_target:
            target.mkdir()
        folder.symlink_to(target, target_is_directory=True)
        blocked = vault.reader.okf_inventory(BundleTreeRequest(root="folder"))
        assert blocked.state == "unavailable"
        assert blocked.entries == ()
        root_page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert not any(e.path.startswith("folder/") for e in root_page.entries)
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["missing", "newer", "corrupt", "unreadable"])
def test_inventory_without_usable_ledger_keeps_live_entries(
    tmp_path: Path, failure: str
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "live.md").write_text("Live")
    state = tmp_path / "state"
    ledger = tmp_path / "state.observations.json"
    if failure == "unreadable":
        ledger.mkdir()
    elif failure != "missing":
        ledger.write_text(
            "not json" if failure == "corrupt" else '{"version": 99, "rows": {}}'
        )
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert [(entry.path, entry.presence) for entry in page.entries] == [
            ("live.md", "present")
        ]
        assert page.enumeration == "complete"
        assert page.removal_history == "unavailable"
    finally:
        vault.close()


def test_interleaved_review_receipt_order_does_not_assert_later_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    import markdown_vault_mcp.facets.writer as writer
    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.vault import VaultSettings

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 -- same signature as datetime.now
            return cls(2026, 11, 18, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(writer, "datetime", FixedDatetime)
    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = source / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        store = vault.writer._receipt_store
        original_append = store.append
        first = True

        def interleaved_append(receipt):
            nonlocal first
            if first:
                first = False
                after_alice = read_payload(vault, "note.md")
                vault.writer.edit(
                    "note.md", "Body", "Changed", if_match=after_alice["etag"]
                )
                reviewed_by_bob = read_payload(vault, "note.md")
                vault.writer.okf_verify(
                    "note.md", "bob", reviewed_by_bob["okf"]["snapshot"]
                )
            original_append(receipt)

        monkeypatch.setattr(store, "append", interleaved_append)
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        current = read_payload(vault, "note.md")
        assert [r["by"] for r in current["frontmatter"]["verified"]] == [
            "human:alice",
            "human:bob",
        ]
        assert [r["by"] for r in store._load()] == ["human:bob", "human:alice"]
        assert current["okf"]["observation"] == {"state": "unavailable"}
    finally:
        vault.close()
