"""Designated generated indexes reject ordinary writes."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.mark.parametrize(
    "operation",
    [
        "write",
        "edit",
        "append",
        "delete",
        "rename_from",
        "rename_to",
        "move_folder",
        "attachment",
    ],
)
def test_mutation_routes_refuse_designation(tmp_path: Path, operation: str) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "other.md").write_text("other")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        actions = {
            "write": lambda: vault.writer.write("guides/index.md", "changed"),
            "edit": lambda: vault.writer.edit("guides/index.md", "original", "changed"),
            "append": lambda: vault.writer.append("guides/index.md", "changed"),
            "delete": lambda: vault.writer.delete("guides/index.md"),
            "rename_from": lambda: vault.writer.rename("guides/index.md", "new.md"),
            "rename_to": lambda: vault.writer.rename("other.md", "guides/index.md"),
            "move_folder": lambda: vault.writer.move_folder("guides", "new"),
            "attachment": lambda: vault.writer.write_attachment(
                "guides/index.md", b"changed"
            ),
        }
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            actions[operation]()
        assert (tmp_path / "guides" / "index.md").read_text() == "original"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "enabled,mode,folder",
    [(False, "on", "guides"), (True, "auto", "guides"), (True, "on", "other")],
)
def test_inactive_or_undesignated_index_is_editable(
    tmp_path: Path, enabled: bool, mode: str, folder: str
) -> None:
    (tmp_path / "guides").mkdir()
    caps = ContentConfig(
        okf_mode=mode, okf_index=enabled, okf_folders=(folder,)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode=mode, okf_capabilities=caps),
    )
    try:
        vault.writer.write("guides/index.md", "authored")
        vault.writer.write("guides/log.md", "history")
        assert (tmp_path / "guides" / "index.md").read_text().endswith("authored")
    finally:
        vault.close()


@pytest.mark.parametrize("protected", [False, True])
def test_generator_obeys_operator_protection(tmp_path: Path, protected: bool) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("# Before\n")
    (tmp_path / "guides" / "note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["guides/index.md"] if protected else [],
        ),
    )
    try:
        vault.index.build_index()
        if protected:
            with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
                vault.writer.okf_generate_index(folder="guides")
            assert (tmp_path / "guides" / "index.md").read_text() == "# Before\n"
        else:
            vault.writer.okf_generate_index(folder="guides")
            assert "/guides/note.md" in (tmp_path / "guides" / "index.md").read_text()
    finally:
        vault.close()


def test_aliases_and_library_override(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "link").symlink_to(tmp_path / "guides", target_is_directory=True)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        for path in ("./guides/index.md", "link/index.md", "guides/../guides/index.md"):
            with pytest.raises(InvalidRequestError, match="okf_generate_index"):
                vault.writer.write(path, "changed")
        vault.writer.write("guides/index.md", "override", allow_protected=True)
        assert (tmp_path / "guides" / "index.md").read_text() == "override"
    finally:
        vault.close()


def test_automatic_maintenance_and_legacy_alias(tmp_path: Path) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_mode="on", okf_write=True).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("index.md", "changed")
        vault.writer.write("note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert "/note.md" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_rename_skips_designated_backlink(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("[Old](/old.md)\n")
    (tmp_path / "old.md").write_text("# Old\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        result = vault.writer.rename("old.md", "new.md", update_links=True)
        assert result.hint and "okf_generate_index" in result.hint
        assert (tmp_path / "guides" / "index.md").read_text() == "[Old](/old.md)\n"
    finally:
        vault.close()


def test_explicit_policy_takes_precedence(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["guides/index.md"],
        ),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.write("guides/index.md", "changed")
    finally:
        vault.close()


def test_automatic_maintenance_respects_explicit_protection(tmp_path: Path) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "guides").mkdir()
    index = tmp_path / "guides" / "index.md"
    index.write_text("# Original\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["guides/index.md"],
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.write("guides/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert index.read_text() == "# Original\n"
        assert (tmp_path / "guides" / "note.md").exists()
    finally:
        vault.close()


async def test_upload_refuses_designated_index(tmp_path: Path) -> None:
    from fastmcp.exceptions import ToolError

    from markdown_vault_mcp._transfer_sink import VaultTransferSink
    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "guides").mkdir()
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        sink = VaultTransferSink(
            ProjectConfig(source_dir=tmp_path, read_only=False),
            vault_provider=lambda: vault,
        )
        with pytest.raises(ToolError, match="okf_generate_index"):
            await sink.validate("guides/index.md", "upload")
    finally:
        vault.close()


@pytest.mark.parametrize(
    "alias", ["GUIDES/index.md", "guides/INDEX.md", "GUIDES/INDEX.md"]
)
def test_existing_case_alias_cannot_edit_index(tmp_path: Path, alias: str) -> None:
    (tmp_path / "guides").mkdir()
    index = tmp_path / "guides" / "index.md"
    index.write_text("original")
    if not (tmp_path / alias).exists():
        pytest.skip("filesystem distinguishes these names")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write(alias, "changed")
        assert index.read_text() == "original"
    finally:
        vault.close()


def test_unicode_folder_alias_cannot_edit_index(tmp_path: Path) -> None:
    folder = tmp_path / "caf\u00e9"
    folder.mkdir()
    (folder / "index.md").write_text("original")
    alias = "cafe\u0301/index.md"
    if not (tmp_path / alias).exists():
        pytest.skip("filesystem distinguishes Unicode spellings")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("caf\u00e9",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write(alias, "changed")
    finally:
        vault.close()


def test_directory_named_index_is_not_reserved(tmp_path: Path) -> None:
    (tmp_path / "guides" / "index.md").mkdir(parents=True)
    (tmp_path / "guides" / "index.md" / "note.md").write_text("note")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.writer.move_folder("guides", "archive")
        assert (tmp_path / "archive" / "index.md" / "note.md").read_text() == "note"
    finally:
        vault.close()


def test_external_parent_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("link",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.write("link/index.md", "outside")
        assert not (outside / "index.md").exists()
    finally:
        vault.close()


def test_distinct_case_names_remain_editable(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    other = tmp_path / "GUIDES"
    try:
        other.mkdir()
    except FileExistsError:
        pytest.skip("filesystem aliases case variants")
    (tmp_path / "guides" / "index.md").write_text("generated")
    (tmp_path / "guides" / "INDEX.md").write_text("authored")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.writer.write("GUIDES/index.md", "other")
        vault.writer.write("guides/INDEX.md", "authored change")
        assert (tmp_path / "guides" / "index.md").read_text() == "generated"
    finally:
        vault.close()


def test_designation_spelling_resolves_to_existing_folder(tmp_path: Path) -> None:
    folder = tmp_path / "Guides"
    folder.mkdir()
    (folder / "index.md").write_text("original")
    if not (tmp_path / "guides").exists():
        pytest.skip("filesystem distinguishes case variants")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("Guides/index.md", "changed")
        assert (folder / "index.md").read_text() == "original"
    finally:
        vault.close()


def test_case_alias_of_missing_index_destination_is_refused(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    if not (tmp_path / "GUIDES").exists():
        pytest.skip("filesystem distinguishes case variants")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("guides/INDEX.md", "changed")
        assert not (tmp_path / "guides" / "index.md").exists()
    finally:
        vault.close()


def test_root_missing_case_alias_is_refused(tmp_path: Path) -> None:
    alternate = tmp_path.with_name(tmp_path.name.swapcase())
    if not alternate.exists() or not alternate.samefile(tmp_path):
        pytest.skip("filesystem distinguishes case variants")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("INDEX.md", "changed")
    finally:
        vault.close()


def test_numeric_root_still_reserves_index(tmp_path: Path) -> None:
    root = tmp_path / "123"
    root.mkdir()
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("index.md", "changed")
    finally:
        vault.close()


def test_numeric_root_missing_case_alias_is_refused(tmp_path: Path) -> None:
    alternate = tmp_path.with_name(tmp_path.name.swapcase())
    if not alternate.exists() or not alternate.samefile(tmp_path):
        pytest.skip("filesystem distinguishes case variants")
    root = tmp_path / "123"
    root.mkdir()
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("INDEX.md", "changed")
    finally:
        vault.close()
