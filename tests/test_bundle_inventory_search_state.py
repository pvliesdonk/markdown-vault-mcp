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
