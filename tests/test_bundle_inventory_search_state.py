"""Search admission is reported without changing the index."""

from pathlib import Path

from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_search_state_tracks_index_without_refresh(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "good.md").write_text("---\ntitle: Good\ntype: concept\n---\nbody")
    (bundle / "missing.md").write_text("---\ntype: concept\n---\nbody")
    (bundle / "broken.md").write_text("---\ntitle: [\n---\nbody")
    (bundle / "excluded.md").write_text("---\ntitle: Excluded\n---\nbody")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            required_frontmatter=["title"], exclude_patterns=["bundle/excluded.md"]
        ),
    )
    try:
        before = vault.reader.inspect_bundle(BundleInspectionRequest(root="bundle"))
        assert all(entry.search_state == "not_evaluated" for entry in before.entries)
        vault.index.build_index()
        (bundle / "new.md").write_text("---\ntitle: New\n---\nbody")
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root="bundle"))
        states = {entry.path: entry.search_state for entry in result.entries}
        assert states == {
            "bundle/good.md": "searchable",
            "bundle/missing.md": "non_admitted",
            "bundle/broken.md": "unparseable",
            "bundle/excluded.md": "index_excluded",
            "bundle/new.md": "not_indexed_yet",
        }
    finally:
        vault.close()


def test_pages_are_read_only_and_direct_inspector_needs_no_index(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest

    folder = tmp_path / "bundle" / "sub"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("---\ntitle: A\n---\nbody")
    (folder / "b.md").write_text("---\ntitle: B\n---\nbody")
    direct = BundleInspector(tmp_path, (), frozenset(), 0)
    assert (
        direct.inspect(BundleInspectionRequest(root="bundle", directory="sub"))
        .entries[0]
        .search_state
        == "not_evaluated"
    )
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()

        def forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("inspection must not mutate or refresh")

        monkeypatch.setattr(vault._coordinator, "prepare_index_read", forbidden)
        monkeypatch.setattr(vault._index_mgr._tracker, "update_state", forbidden)
        monkeypatch.setattr(vault._index_mgr, "build_index", forbidden)
        first = vault.reader.inspect_bundle_tree(
            BundleTreeRequest(root="bundle", directory="sub", entry_limit=1)
        )
        second = vault.reader.inspect_bundle_tree(
            BundleTreeRequest(
                root="bundle", directory="sub", entry_limit=1, cursor=first.next_cursor
            )
        )
        assert [entry.search_state for entry in first.entries + second.entries] == [
            "searchable",
            "searchable",
        ]
    finally:
        vault.close()


def test_dirty_index_disposition_and_lost_tracker(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "repair.md").write_text("---\ntype: concept\n---\nbody")
    (bundle / "broken.md").write_text("---\ntitle: [\n---\nbody")
    state = tmp_path / "state.json"
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            state_path=state, read_only=False, required_frontmatter=["title"]
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.write("bundle/new.md", "body", frontmatter={"type": "concept"})
        vault.writer.write("bundle/repair.md", "body", frontmatter={"title": "Fixed"})
        assert vault.index.wait_for_drain(timeout=5)
        state.unlink()
        entries = {
            entry.path: entry
            for entry in vault.reader.inspect_bundle(
                BundleInspectionRequest(root="bundle")
            ).entries
        }
        assert entries["bundle/new.md"].search_state == "non_admitted"
        assert entries["bundle/repair.md"].search_state == "searchable"
        assert entries["bundle/broken.md"].search_state == "unparseable"
    finally:
        vault.close()


def test_budget_limited_regular_notes_retain_index_state(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for i in range(18):
        (bundle / f"{i:02}.md").write_text("body " * 14000)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(exclude_patterns=["bundle/17.md"])
    )
    try:
        vault.index.build_index()
        page = vault.reader.inspect_bundle(BundleInspectionRequest(root="bundle"))
        entries = {entry.path: entry for entry in page.entries}
        assert entries["bundle/00.md"].reason == "note_limit"
        assert entries["bundle/00.md"].search_state == "searchable"
        budget = [
            entry
            for entry in entries.values()
            if entry.reason == "byte_budget" and entry.visibility == "visible"
        ]
        assert budget and all(entry.search_state == "searchable" for entry in budget)
        assert entries["bundle/17.md"].search_state == "index_excluded"
    finally:
        vault.close()


def test_repaired_scan_skip_uses_live_row(tmp_path: Path) -> None:
    note = tmp_path / "repair.md"
    note.write_text("---\ntype: concept\n---\nbody")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        vault.writer.write("repair.md", "body", frontmatter={"title": "Fixed"})
        assert vault.index.wait_for_drain(timeout=5)
        entry = vault.reader.inspect_bundle(BundleInspectionRequest(root="")).entries[0]
        assert entry.search_state == "searchable"
    finally:
        vault.close()


def test_lost_tracker_preserves_parse_tombstone(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text("---\ntitle: [\n---\nbody")
    state = tmp_path / "state.json"
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
        state.unlink()
        entry = vault.reader.inspect_bundle(BundleInspectionRequest(root="")).entries[0]
        assert entry.search_state == "unparseable"
    finally:
        vault.close()


def test_unknown_tombstone_and_new_malformed_note(tmp_path: Path) -> None:
    from markdown_vault_mcp.types import SkippedFile

    (tmp_path / "error.md").write_text("body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        vault._fts.upsert_tombstone(
            SkippedFile(path="error.md", category="internal_error", detail="private"),
            content_hash="hash",
            modified_at=1.0,
        )
        (tmp_path / "new.md").write_text("---\ntitle: [\n---\nbody")
        entries = {
            e.path: e
            for e in vault.reader.inspect_bundle(
                BundleInspectionRequest(root="")
            ).entries
        }
        assert entries["error.md"].search_state == "not_evaluated"
        assert entries["new.md"].parse_state == "malformed_metadata"
        assert entries["new.md"].search_state == "not_indexed_yet"
        assert "private" not in repr(entries)
    finally:
        vault.close()


def test_unavailable_paths_are_not_queried(tmp_path: Path, monkeypatch) -> None:
    note = tmp_path / "note.md"
    note.write_text("body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        note.unlink()
        note.symlink_to(tmp_path / "missing")

        def forbidden(_path: str) -> None:
            raise AssertionError("unavailable entry queried")

        monkeypatch.setattr(vault._fts, "get_note", forbidden)
        monkeypatch.setattr(vault._fts, "get_tombstone", forbidden)
        entry = vault.reader.inspect_bundle(BundleInspectionRequest(root="")).entries[0]
        assert entry.reason == "symlink"
        assert entry.search_state == "not_evaluated"
    finally:
        vault.close()
