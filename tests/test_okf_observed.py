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


def test_copied_bound_review_then_later_edit(tmp_path: Path) -> None:
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
            read_payload(vault, "note.md")["okf"]["observation"][
                "later_change_observed"
            ]
            is True
        )
    finally:
        vault.close()
