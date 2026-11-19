"""External changes converge designated OKF indexes on reindex."""

from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_external_add_updates_existing_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        (root / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


@pytest.mark.parametrize("change", ["delete", "move", "metadata"])
def test_external_changes_update_listing(tmp_path: Path, change: str) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = root / "concept.md"
    concept.write_text("---\ndescription: Before\n---\n# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="")
        if change == "delete":
            concept.unlink()
        elif change == "move":
            concept.rename(root / "moved.md")
        else:
            concept.write_text(
                "---\ndescription: After\n---\n# Concept\n", encoding="utf-8"
            )
        vault.index.reindex()
        listing = index.read_text(encoding="utf-8")
        if change == "delete":
            assert "concept.md" not in listing
        elif change == "move":
            assert "concept.md" not in listing
            assert "moved.md" in listing
        else:
            assert "After" in listing
            assert "Before" not in listing
    finally:
        vault.close()


def test_reserved_index_edit_does_not_regenerate(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        edited = '---\nokf_version: "0.2"\n---\n# Hand edited\n'
        index.write_text(edited, encoding="utf-8")
        vault.index.reindex()
        assert index.read_text(encoding="utf-8") == edited
    finally:
        vault.close()


def test_cold_build_then_reindex_repairs_stale_index(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


def test_identical_reindex_does_not_write_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        writes: list[str] = []
        original = vault._doc_mgr.replace_body

        def record(path: str, *args: object, **kwargs: object) -> bool:
            writes.append(path)
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "replace_body", record)
        vault.index.reindex()
        assert writes == []
    finally:
        vault.close()


def test_reserved_change_does_not_mask_concept_change(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        index.write_text('---\nokf_version: "0.2"\n---\n# Edited\n', encoding="utf-8")
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_reindex_completion_includes_convergence(
    tmp_path: Path, asynchronous: bool
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        if asynchronous:
            vault.index.reindex_async().result(timeout=10)
        else:
            with vault.pause_writes():
                vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
    finally:
        vault.close()


def test_failed_refresh_retries_without_another_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        original = vault._okf_migrate.generate_index
        calls = 0

        def fail_once(**kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary failure")
            return original(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail_once)
        (tmp_path / "concept.md").write_text("# Concept\n", encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" not in index.read_text(encoding="utf-8")
        vault.index.reindex()
        assert "concept.md" in index.read_text(encoding="utf-8")
        assert calls == 2
    finally:
        vault.close()


def test_prior_observation_sweep_keeps_change_for_reindex(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n', encoding="utf-8")
    concept = tmp_path / "concept.md"
    concept.write_text("---\ndescription: Before\n---\n# Concept\n", encoding="utf-8")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault.index.reindex()
        concept.write_text(
            "---\ndescription: After\n---\n# Concept\n", encoding="utf-8"
        )
        assert vault._index_mgr.observations is not None
        vault._index_mgr.observations.sweep()
        vault.index.reindex()
        assert "After" in index.read_text(encoding="utf-8")
    finally:
        vault.close()
