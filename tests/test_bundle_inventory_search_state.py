"""Search admission is reported without changing the index."""

import sqlite3
from pathlib import Path

import pytest

from markdown_vault_mcp.bundle_inventory import (
    BundleEntry,
    BundleInspection,
    BundleInspectionRequest,
    project_search_state,
)
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
        (bundle / "new.md").write_text("---\ntitle: [\n---\nbody")
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root="bundle"))
        states = {entry.path: entry.search_state for entry in result.entries}
        missing = next(
            entry for entry in result.entries if entry.path == "bundle/missing.md"
        )
        assert (
            missing.state,
            missing.reason,
            missing.parse_state,
            missing.type_state,
        ) == ("inspected", None, "valid", "present")
        assert (
            next(e for e in result.entries if e.path == "bundle/new.md").parse_state
            == "malformed_metadata"
        )
        assert states == {
            "bundle/good.md": "searchable",
            "bundle/missing.md": "non_admitted",
            "bundle/broken.md": "unparseable",
            "bundle/excluded.md": "index_excluded",
            "bundle/new.md": "not_indexed_yet",
        }
    finally:
        vault.close()


def test_tree_pages_project_without_refresh(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    folder = tmp_path / "bundle" / "sub"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("---\ntitle: A\n---\nbody")
    (folder / "b.md").write_text("---\ntitle: B\n---\nbody")
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


def test_writer_updates_index_disposition(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "repair.md").write_text("---\ntype: concept\n---\nbody")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        vault.writer.write("bundle/new.md", "body", frontmatter={"type": "concept"})
        vault.writer.write("bundle/repair.md", "body", frontmatter={"title": "Fixed"})
        assert vault.index.wait_for_drain(timeout=5)
        entries = {
            entry.path: entry
            for entry in vault.reader.inspect_bundle(
                BundleInspectionRequest(root="bundle")
            ).entries
        }
        assert entries["bundle/new.md"].search_state == "non_admitted"
        assert entries["bundle/repair.md"].search_state == "searchable"
    finally:
        vault.close()


def test_adopted_index_live_row_wins_over_local_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Findable\n\nquartz content")
    db = tmp_path / "index.db"
    state = tmp_path / "state.json"
    owner = Vault(
        source_dir=root, settings=VaultSettings(index_path=db, state_path=state)
    )
    try:
        owner.index.build_index()
    finally:
        owner.close()
    reader = Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=db, state_path=state, exclude_patterns=["note.md"]
        ),
    )
    try:
        reader.index.use_existing_index()
        assert reader.reader.search("quartz")[0].path == "note.md"
        entry = reader.reader.inspect_bundle(BundleInspectionRequest(root="")).entries[
            0
        ]
        assert entry.visibility == "index_excluded"
        assert entry.search_state == "searchable"
    finally:
        reader.close()


def test_all_skip_categories_and_unknown_are_projected() -> None:
    from markdown_vault_mcp.types import SKIP_CATEGORIES

    expected = {
        "missing_frontmatter": "non_admitted",
        "parse_error": "unparseable",
        "encoding_error": "unparseable",
        "internal_error": "not_evaluated",
        "live": "not_evaluated",
    }
    assert set(expected) - {"live"} == SKIP_CATEGORIES
    entries = [
        BundleEntry(path=category, role="concept_candidate", state="inspected")
        for category in expected
    ]
    entries.extend(
        BundleEntry(
            path=reason, role="concept_candidate", state="not_inspected", reason=reason
        )
        for reason in ("note_limit", "byte_budget", "metadata_complexity")
    )
    entries.extend(
        [
            BundleEntry(
                path="excluded",
                role="concept_candidate",
                state="not_inspected",
                reason="note_limit",
                visibility="index_excluded",
            ),
            BundleEntry(path="symlink", state="unavailable", reason="symlink"),
            BundleEntry(path="directory", role="directory", reason="directory"),
            BundleEntry(path="supporting", role="supporting", reason="supporting"),
        ]
    )
    request = BundleInspectionRequest(root="")
    observed: list[str] = []

    def row_state(path: str) -> tuple[bool, str | None]:
        observed.append(path)
        if path in expected:
            return True, path
        if path == "excluded":
            return False, None
        return True, None

    result = project_search_state(
        BundleInspection(request, "inspected", entries=tuple(entries)), row_state
    )
    states = {entry.path: entry.search_state for entry in result.entries}
    for category, state in expected.items():
        assert states[category] == state
    for reason in ("note_limit", "byte_budget", "metadata_complexity"):
        assert states[reason] == "searchable"
    assert states["excluded"] == "index_excluded"
    assert all(
        states[path] == "not_evaluated"
        for path in ("symlink", "directory", "supporting")
    )
    assert not set(observed) & {"symlink", "directory", "supporting"}


def test_backend_without_row_state_is_usable(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import Mock

    (tmp_path / "note.md").write_text("body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        fake = Mock(spec=[])
        monkeypatch.setattr(vault._doc_mgr, "_fts", fake)
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.entries[0].search_state == "not_evaluated"
        assert fake.mock_calls == []
    finally:
        vault.close()


@pytest.mark.parametrize("error", [sqlite3.OperationalError, sqlite3.DatabaseError])
def test_index_failure_preserves_disk_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[sqlite3.Error]
) -> None:
    (tmp_path / "a.md").write_text("body")
    (tmp_path / "b.md").write_text("body")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        original = vault._fts.row_state
        calls = 0

        def fail_second(path: str) -> tuple[bool, str | None]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise error("index unavailable")
            return original(path)

        monkeypatch.setattr(vault._fts, "row_state", fail_second)
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert len(result.entries) == 2
        assert all(e.search_state == "not_evaluated" for e in result.entries)
    finally:
        vault.close()
