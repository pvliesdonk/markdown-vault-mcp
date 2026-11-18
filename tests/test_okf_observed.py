"""Local observations alongside supplied review claims."""

from pathlib import Path

import pytest

from markdown_vault_mcp._cli_read import read_payload
from markdown_vault_mcp.vault import Vault


def test_initial_discovery_is_not_a_change_after_imported_review(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (tmp_path / "note.md").write_text(
        '---\ntype: concept\nverified: [{by: "human:a", at: "2020-01-01T00:00:00Z"}]\n---\nBody\n'
    )
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        result = read_payload(vault, "note.md")
        assert result["okf"]["observation"]["state"] == "unavailable"
    finally:
        vault.close()


def test_change_after_review_preserves_claim(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = tmp_path / "note.md"
    original = '---\ntype: concept\nverified: [{by: "human:a", at: "2020-01-01T00:00:00Z"}]\n---\nBody\n'
    note.write_text(original)
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        note.write_text(original + "Edit\n")
        vault._index_mgr.observations.sweep()
        result = read_payload(vault, "note.md")
        assert result["okf"]["observation"]["state"] == "unavailable"
        assert result["frontmatter"]["verified"] == [
            {"by": "human:a", "at": "2020-01-01T00:00:00Z"}
        ]
    finally:
        vault.close()


def test_inventory_contains_removed_candidate(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    note = tmp_path / "lost.md"
    note.write_text("---\ntype: concept\n---\nBody\n")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        note.unlink()
        vault._index_mgr.observations.sweep()
        page = vault.reader.okf_inventory(BundleTreeRequest(root=""))
        assert [(entry.path, entry.presence) for entry in page.entries] == [
            ("lost.md", "removed")
        ]
    finally:
        vault.close()


def test_unobserved_edit_and_invalid_review_are_unknown(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = tmp_path / "note.md"
    note.write_text(
        '---\ntype: concept\nverified: [{by: "human:a", at: "2020-01-01T00:00:00Z"}]\n---\nBody\n'
    )
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        note.write_text(note.read_text() + "Unobserved\n")
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
        note.write_text(
            '---\ntype: concept\nverified: [{by: "human:a", at: nonsense}]\n---\nBody\n'
        )
        vault._index_mgr.observations.sweep()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
    finally:
        vault.close()


def test_removed_inventory_paginates_and_respects_scope(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    (tmp_path / "sub").mkdir()
    for path in ("a.md", "b.md", "sub/c.md"):
        (tmp_path / path).write_text("Body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        for path in ("a.md", "b.md", "sub/c.md"):
            (tmp_path / path).unlink()
        vault._index_mgr.observations.sweep()
        page = vault.reader.okf_inventory(BundleTreeRequest(root="", entry_limit=1))
        assert [entry.path for entry in page.entries] == ["a.md"]
        page = vault.reader.okf_inventory(
            BundleTreeRequest(root="", entry_limit=1, cursor=page.next_cursor)
        )
        assert [entry.path for entry in page.entries] == ["b.md"]
        page = vault.reader.okf_inventory(BundleTreeRequest(root="sub"))
        assert [entry.path for entry in page.entries] == ["sub/c.md"]
    finally:
        vault.close()


def test_comparison_does_not_change_claims_and_handles_no_history() -> None:
    from markdown_vault_mcp._okf_observed import review_observation
    from markdown_vault_mcp.indexing.observations import Observation

    metadata = {"verified": [{"by": "human:a", "at": "2026-02-01T00:00:00Z"}]}
    row = Observation(
        "note.md",
        "concept_candidate",
        "a" * 64,
        None,
        "2026-01-01T00:00:00+00:00",
        "2026-01-15T00:00:00+00:00",
    )
    assert review_observation(metadata, row, "a" * 64) == {
        "state": "available",
        "later_change_observed": False,
    }
    assert review_observation(metadata, None, "a" * 64) == {"state": "unavailable"}
    assert review_observation({}, row, "a" * 64) == {"state": "unavailable"}
    assert metadata["verified"][0]["at"] == "2026-02-01T00:00:00Z"


def test_review_metadata_only_does_not_count_as_content_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, tzinfo

    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.okf import append_okf_verification

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    note = tmp_path / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    vault = Vault(source_dir=tmp_path)
    try:

        class Earlier(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> datetime:
                return datetime(2019, 1, 1, tzinfo=tz)

        with monkeypatch.context() as patch:
            patch.setattr("markdown_vault_mcp.indexing.observations.datetime", Earlier)
            vault.index.build_index()
        reviewed = append_okf_verification(
            original,
            subject="a",
            now=datetime.now(UTC),
            reviewed_snapshot=concept_snapshot(original),
            check="human-review",
        )
        note.write_text(reviewed)
        vault._index_mgr.observations.sweep()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
        note.write_text(reviewed + "Changed\n")
        vault._index_mgr.observations.sweep()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"][
                "later_change_observed"
            ]
            is True
        )
    finally:
        vault.close()


def test_copied_bound_review_then_later_edit_remains_unknown(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.okf import append_okf_verification

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    text = "---\ntype: concept\n---\nBody\n"
    reviewed = append_okf_verification(
        text,
        subject="a",
        now=datetime(2020, 1, 1, tzinfo=UTC),
        reviewed_snapshot=concept_snapshot(text),
        check="human-review",
    )
    note = tmp_path / "note.md"
    note.write_text(reviewed)
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
        note.write_text(reviewed + "changed\n")
        vault._index_mgr.observations.sweep()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
    finally:
        vault.close()


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


def test_future_review_never_proves_no_change() -> None:
    from markdown_vault_mcp._okf_observed import review_observation
    from markdown_vault_mcp.indexing.observations import Observation

    metadata = {"verified": [{"by": "human:a", "at": "2099-01-01T00:00:00Z"}]}
    row = Observation(
        "note.md",
        "concept_candidate",
        "a" * 64,
        None,
        "2020-01-01T00:00:00+00:00",
        "2021-01-01T00:00:00+00:00",
    )
    assert review_observation(metadata, row, "a" * 64) == {"state": "unavailable"}


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
        cursor = None
        entries = []
        while True:
            page = vault.reader.okf_inventory(
                BundleTreeRequest(root="", entry_limit=1, cursor=cursor)
            )
            entries.extend((e.path, e.presence) for e in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert entries == [
            ("a.md", "removed"),
            ("b.md", "present"),
            ("c.md", "present"),
            ("d.md", "present"),
            ("e.md", "removed"),
        ]
    finally:
        vault.close()


def test_imported_mismatched_review_then_metadata_only_change(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp._okf_review import concept_snapshot
    from markdown_vault_mcp.okf import append_okf_verification

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    original = "---\ntype: concept\n---\nOriginal\n"
    reviewed = append_okf_verification(
        original,
        subject="a",
        now=datetime(2020, 1, 1, tzinfo=UTC),
        reviewed_snapshot=concept_snapshot(original),
        check="human-review",
    )
    imported = reviewed.replace("Original", "Changed before import")
    note = tmp_path / "note.md"
    note.write_text(imported)
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        note.write_text(imported.replace("check: human-review", "check: another-check"))
        vault._index_mgr.observations.sweep()
        assert (
            read_payload(vault, "note.md")["okf"]["observation"]["state"]
            == "unavailable"
        )
    finally:
        vault.close()


def test_malformed_snapshot_claim_does_not_support_change() -> None:
    from markdown_vault_mcp._okf_observed import review_observation
    from markdown_vault_mcp.indexing.observations import Observation

    metadata = {
        "verified": [{"by": "human:a", "at": "2020-01-01T00:00:00Z"}],
        "x-mvmcp": {
            "reviews": [
                {
                    "by": "human:a",
                    "at": "2020-01-01T00:00:00Z",
                    "snapshot": {"algo": "mvmcp-concept-v1", "digest": "x"},
                }
            ]
        },
    }
    row = Observation(
        "note.md",
        "concept_candidate",
        "a" * 64,
        None,
        "2019-01-01T00:00:00+00:00",
        "2021-01-01T00:00:00+00:00",
    )
    assert review_observation(
        metadata, row, "a" * 64, {"algo": "mvmcp-concept-v1", "digest": "b" * 64}
    ) == {"state": "unavailable"}
    with pytest.raises(ValueError, match="snapshot"):
        review_observation(metadata, row, "a" * 64, {})


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


def test_same_second_first_observation_is_not_rounded_before_review() -> None:
    from markdown_vault_mcp._okf_observed import review_observation
    from markdown_vault_mcp.indexing.observations import Observation

    metadata = {"verified": [{"by": "human:a", "at": "2020-01-01T00:00:00Z"}]}
    row = Observation(
        "note.md",
        "concept_candidate",
        "a" * 64,
        None,
        "2020-01-01T00:00:00.100000+00:00",
        "2021-01-01T00:00:00+00:00",
    )
    assert review_observation(metadata, row, "a" * 64) == {"state": "unavailable"}


@pytest.mark.parametrize(
    ("extension", "snapshot"),
    [
        (None, {"algo": "mvmcp-concept-v1", "digest": "b" * 64}),
        ({"reviews": "invalid"}, {"algo": "mvmcp-concept-v1", "digest": "b" * 64}),
        ({"reviews": []}, None),
    ],
)
def test_observed_change_without_usable_binding_is_unknown(extension, snapshot) -> None:
    from markdown_vault_mcp._okf_observed import review_observation
    from markdown_vault_mcp.indexing.observations import Observation

    metadata = {
        "verified": [{"by": "human:a", "at": "2020-01-01T00:00:00Z"}],
        "x-mvmcp": extension,
    }
    row = Observation(
        "note.md",
        "concept_candidate",
        "a" * 64,
        None,
        "2019-01-01T00:00:00+00:00",
        "2021-01-01T00:00:00+00:00",
    )
    assert review_observation(metadata, row, "a" * 64, snapshot) == {
        "state": "unavailable"
    }
