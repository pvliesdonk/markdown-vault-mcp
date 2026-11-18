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


def test_removed_probes_are_bounded_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest

    for i in range(1001):
        (tmp_path / f"{i:04}.md").write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        for path in tmp_path.glob("*.md"):
            path.unlink()
        vault._index_mgr.observations.sweep()
        seen: list[str] = []
        original = BundleInspector.observe_path

        def counted(self, path, visit):
            seen.append(path)
            return original(self, path, visit)

        monkeypatch.setattr(BundleInspector, "observe_path", counted)
        first = vault.reader.okf_inventory(BundleTreeRequest(root="", entry_limit=1000))
        assert len(seen) <= 1000
        assert first.next_cursor is not None
        seen.clear()
        second = vault.reader.okf_inventory(
            BundleTreeRequest(root="", entry_limit=1000, cursor=first.next_cursor)
        )
        assert len(seen) <= 1000
        paths = [e.path for e in (*first.entries, *second.entries)]
        if second.next_cursor:
            third = vault.reader.okf_inventory(
                BundleTreeRequest(root="", entry_limit=1000, cursor=second.next_cursor)
            )
            paths += [e.path for e in third.entries]
        assert paths == [f"{i:04}.md" for i in range(1001)]
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


def test_removed_path_is_hidden_when_parent_becomes_symlink(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    source = tmp_path / "vault"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (source / "folder").mkdir()
    note = source / "folder" / "secret.md"
    note.write_text("Body")
    vault = Vault(source_dir=source)
    try:
        vault._index_mgr.observations.sweep()
        note.unlink()
        vault._index_mgr.observations.sweep()
        (source / "folder").rmdir()
        (source / "folder").symlink_to(outside, target_is_directory=True)
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert all(entry.path != "folder/secret.md" for entry in page.entries)
    finally:
        vault.close()


def test_large_mixed_inventory_continues_without_skipping(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    for i in range(1002):
        (tmp_path / f"{i:04}.md").write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault._index_mgr.observations.sweep()
        for i in range(0, 1002, 2):
            (tmp_path / f"{i:04}.md").unlink()
        vault._index_mgr.observations.sweep()
        cursor = None
        paths: list[str] = []
        while True:
            page = vault.reader.okf_inventory(
                BundleTreeRequest(root="", entry_limit=1000, cursor=cursor)
            )
            paths.extend(entry.path for entry in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert paths == [f"{i:04}.md" for i in range(1002)]
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


@pytest.mark.parametrize(
    "extra",
    [
        {"by": "human:bob", "at": "2021-01-01T00:00:00Z"},
        {"by": "human:bob", "at": "2020-01-01T00:00:00Z"},
    ],
)
def test_latest_unreceipted_review_makes_comparison_unknown(
    tmp_path: Path, extra: dict[str, str]
) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)
    snapshot = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    alice = {"by": "human:alice", "at": "2020-01-01T00:00:00Z"}
    store.append({"path": "note.md", **alice, "snapshot": snapshot})
    assert store.review_observation(
        "note.md", {"verified": [alice, extra]}, snapshot
    ) == {"state": "unavailable"}
    assert store.review_observation("other.md", {"verified": [alice]}, snapshot) == {
        "state": "unavailable"
    }


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        '{"version": 99, "rows": []}',
        '{"version": 1, "rows": [{"path": "note.md", "by": "human:a", "at": "2020-01-01T00:00:00Z", "snapshot": {"algo": "mvmcp-concept-v1", "digest": "invalid"}}]}',
    ],
)
def test_unusable_receipt_state_does_not_break_read(
    tmp_path: Path, contents: str
) -> None:
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (source / "note.md").write_text(
        '---\ntype: concept\nverified: [{by: "human:a", at: "2020-01-01T00:00:00Z"}]\n---\nBody\n'
    )
    (tmp_path / "state.receipts.json").write_text(contents)
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        assert read_payload(vault, "note.md")["okf"]["observation"] == {
            "state": "unavailable"
        }
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


def test_copy_without_receipts_remains_unknown_after_edit(tmp_path: Path) -> None:
    import shutil

    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "source"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    text = "---\ntype: concept\n---\nBody\n"
    (source / "note.md").write_text(text)
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(text))
    finally:
        vault.close()
    copied = tmp_path / "copy"
    shutil.copytree(source, copied)
    fresh = Vault(source_dir=copied)
    try:
        assert read_payload(fresh, "note.md")["okf"]["observation"] == {
            "state": "unavailable"
        }
        note = copied / "note.md"
        note.write_text(note.read_text() + "Changed\n")
        assert read_payload(fresh, "note.md")["okf"]["observation"] == {
            "state": "unavailable"
        }
    finally:
        fresh.close()


def test_receipt_read_error_is_unavailable(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(tmp_path, tmp_path / "vault")
    metadata = {"verified": [{"by": "human:a", "at": "2099-01-01T00:00:00Z"}]}
    snapshot = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    assert store.review_observation("note.md", metadata, snapshot) == {
        "state": "unavailable"
    }


def test_latest_receipt_is_used_for_matching_identity(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)
    first = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    second = {"algo": "mvmcp-concept-v1", "digest": "b" * 64}
    review = {"by": "human:a", "at": "2020-01-01T00:00:00Z"}
    store.append({"path": "note.md", **review, "snapshot": first})
    store.append({"path": "note.md", **review, "snapshot": second})
    assert store.review_observation("note.md", {"verified": review}, second) == {
        "state": "unavailable"
    }
    assert store.review_observation("note.md", {"verified": review}, first) == {
        "state": "available",
        "later_change_observed": True,
    }
    assert store.review_observation("note.md", {"verified": review}, None) == {
        "state": "unavailable"
    }
    assert store.review_observation("note.md", {}, first) == {"state": "unavailable"}


def test_direct_reader_without_receipt_store_reports_unknown(tmp_path: Path) -> None:
    from markdown_vault_mcp.facets.reader import ReaderFacet

    vault = Vault(source_dir=tmp_path)
    try:
        reader = ReaderFacet(
            search_mgr=vault._search_mgr,
            doc_mgr=vault._doc_mgr,
            git_query_mgr=vault._git_query_mgr,
            require_built=vault._require_built,
        )
        assert reader.review_observation("note.md", {}, None) == {
            "state": "unavailable"
        }
    finally:
        vault.close()


def test_equivalent_review_timestamp_matches_local_receipt(tmp_path: Path) -> None:
    import re

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
        reviewed = note.read_text()
        reformatted = re.sub(r"(at: )'([^']+)'", r"\1\2", reviewed)
        assert reformatted != reviewed
        note.write_text(reformatted.replace("Body", "Changed"))
        assert (
            read_payload(vault, "note.md")["okf"]["observation"][
                "later_change_observed"
            ]
            is True
        )
    finally:
        vault.close()


def test_revert_and_ledger_reset_do_not_assert_no_change(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = source / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    settings = VaultSettings(read_only=False, state_path=tmp_path / "state")
    vault = Vault(source_dir=source, settings=settings)
    try:
        vault.index.build_index()
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        reviewed = note.read_text()
        note.write_text(reviewed.replace("Body", "Changed"))
        vault._index_mgr.observations.sweep()
        note.write_text(reviewed)
        vault._index_mgr.observations.sweep()
        assert read_payload(vault, "note.md")["okf"]["observation"] == {
            "state": "unavailable"
        }
    finally:
        vault.close()
    (tmp_path / "state.observations.json").unlink()
    vault = Vault(source_dir=source, settings=settings)
    try:
        assert read_payload(vault, "note.md")["okf"]["observation"] == {
            "state": "unavailable"
        }
        note.write_text(reviewed.replace("Body", "Changed"))
        assert read_payload(vault, "note.md")["okf"]["observation"] == {
            "state": "available",
            "later_change_observed": True,
        }
    finally:
        vault.close()


def test_removed_folder_paging_and_blocked_scope(tmp_path: Path) -> None:
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
        folder.symlink_to(tmp_path / "nonexistent", target_is_directory=True)
        blocked = vault.reader.okf_inventory(BundleTreeRequest(root="folder"))
        assert blocked.state == "unavailable"
        assert blocked.entries == ()
    finally:
        vault.close()


def test_receipt_snapshot_extra_fields_do_not_prove_change(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore

    store = ReceiptStore(None, tmp_path)
    snapshot = {"algo": "mvmcp-concept-v1", "digest": "a" * 64}
    review = {"by": "human:alice", "at": "2020-01-01T00:00:00Z"}
    store.append({"path": "note.md", **review, "snapshot": {**snapshot, "extra": 1}})
    assert store.review_observation("note.md", {"verified": [review]}, snapshot) == {
        "state": "unavailable"
    }


@pytest.mark.parametrize("failure", ["newer", "unreadable"])
def test_inventory_without_usable_ledger_keeps_live_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
    from markdown_vault_mcp.indexing.observations import ObservationLedger
    from markdown_vault_mcp.vault import VaultSettings

    source = tmp_path / "vault"
    source.mkdir()
    (source / "live.md").write_text("Live")
    state = tmp_path / "state"
    (tmp_path / "state.observations.json").write_text('{"version": 99, "rows": {}}')
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        if failure == "unreadable":

            def fail(_self):
                raise OSError("unreadable")

            monkeypatch.setattr(ObservationLedger, "rows", fail)
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert [(entry.path, entry.presence) for entry in page.entries] == [
            ("live.md", "present")
        ]
    finally:
        vault.close()
