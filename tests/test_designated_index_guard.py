"""Designated generated indexes reject ordinary writes."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.designated_index import designated_vault


@pytest.mark.parametrize(
    "operation",
    [
        "write",
        "edit",
        "append",
        "delete",
        "rename_from",
        "rename_to",
        "move_folder_from",
        "attachment",
    ],
)
def test_mutation_routes_refuse_designation(tmp_path: Path, operation: str) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "other.md").write_text("other")
    vault = designated_vault(tmp_path)
    try:
        actions = {
            "write": lambda: vault.writer.write("guides/index.md", "changed"),
            "edit": lambda: vault.writer.edit("guides/index.md", "original", "changed"),
            "append": lambda: vault.writer.append("guides/index.md", "changed"),
            "delete": lambda: vault.writer.delete("guides/index.md"),
            "rename_from": lambda: vault.writer.rename("guides/index.md", "new.md"),
            "rename_to": lambda: vault.writer.rename("other.md", "guides/index.md"),
            "move_folder_from": lambda: vault.writer.move_folder("guides", "new"),
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


def test_directory_and_leaf_symlink_aliases(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "link").symlink_to(tmp_path / "guides", target_is_directory=True)
    (tmp_path / "alias.md").symlink_to(tmp_path / "guides" / "index.md")
    vault = designated_vault(tmp_path)
    try:
        for path in ("link/index.md", "alias.md"):
            with pytest.raises(InvalidRequestError, match="okf_generate_index"):
                vault.writer.write(path, "changed")
    finally:
        vault.close()


@pytest.mark.parametrize("target", ["guides/concept.md", "other/index.md"])
def test_designated_leaf_alias_to_authored_note_is_editable(
    tmp_path: Path, target: str
) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "guides").mkdir()
    (tmp_path / "other").mkdir()
    note = tmp_path / target
    note.write_text("authored")
    (tmp_path / "guides/index.md").symlink_to(note)
    vault = designated_vault(tmp_path)
    try:
        vault.index.build_index()
        vault.writer.write("guides/note.md", "# Note")
        wait_for_writer_drain(vault)
        assert note.read_text() == "authored"
        vault.writer.write("guides/index.md", "changed")
        assert note.read_text().endswith("changed")
    finally:
        vault.close()


def test_explicit_policy_protects_designated_leaf_alias(tmp_path: Path) -> None:
    from markdown_vault_mcp._path_guard import PathGuard
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/concept.md").write_text("authored")
    (tmp_path / "guides/index.md").symlink_to(tmp_path / "guides/concept.md")
    guard = PathGuard(
        tmp_path,
        ["guides/index.md"],
        lambda p: designated_index_path(tmp_path, p, ("guides",)),
    )
    assert "PROTECTED_PATHS" in (guard.reason("guides/index.md") or "")


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
    vault = designated_vault(tmp_path)
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


async def test_upload_refuses_designated_index(tmp_path: Path) -> None:
    from fastmcp.exceptions import ToolError

    from markdown_vault_mcp._transfer_sink import VaultTransferSink
    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "guides").mkdir()
    vault = designated_vault(tmp_path)
    try:
        sink = VaultTransferSink(
            ProjectConfig(source_dir=tmp_path, read_only=False),
            vault_provider=lambda: vault,
        )
        with pytest.raises(ToolError, match="okf_generate_index"):
            await sink.validate("guides/index.md", "upload")
    finally:
        vault.close()


@pytest.mark.parametrize("alias", ["GUIDES/index.md", "guides/INDEX.md"])
def test_existing_case_alias_cannot_edit_index(tmp_path: Path, alias: str) -> None:
    (tmp_path / "guides").mkdir()
    index = tmp_path / "guides" / "index.md"
    index.write_text("original")
    if not (tmp_path / alias).exists():
        pytest.skip("filesystem distinguishes these names")
    vault = designated_vault(tmp_path)
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
    vault = designated_vault(tmp_path)
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
        with pytest.raises(InvalidRequestError, match="Path traversal"):
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
    vault = designated_vault(tmp_path)
    try:
        vault.writer.write("GUIDES/index.md", "other")
        vault.writer.write("guides/INDEX.md", "authored change")
        assert (tmp_path / "guides" / "index.md").read_text() == "generated"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("folder", "destination"),
    [
        (".", "INDEX.md"),
        ("123", "123/INDEX.md"),
        ("guides", "guides/INDEX.md"),
        ("guides/**", "guides/NEW/INDEX.md"),
        ("café/**", "cafe\u0301/NEW/INDEX.md"),
    ],
)
def test_missing_folder_alias_cannot_lose_client_content(
    tmp_path: Path, folder: str, destination: str
) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "existing.md").write_text("# Existing\n")
    if not (tmp_path / "EXISTING.md").exists():
        pytest.skip("filesystem distinguishes case variants")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(folder,)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write(destination, "CLIENT CONTENT")
        assert not (tmp_path / destination).exists()
        parent = Path(destination).parent
        vault.writer.write(f"{parent}/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        generated = tmp_path / destination
        assert not generated.exists() or "CLIENT CONTENT" not in generated.read_text()
    finally:
        vault.close()


def test_missing_designated_folder_does_not_crash_for_distinct_case(
    tmp_path: Path,
) -> None:
    (tmp_path / "GUIDES").mkdir()
    vault = designated_vault(tmp_path)
    try:
        from markdown_vault_mcp.okf_capabilities import designated_index_path

        assert designated_index_path(tmp_path, "guides/index.md", ("guides",))
        if not (tmp_path / "guides").exists():
            vault.writer.write("GUIDES/index.md", "authored")
            assert (tmp_path / "GUIDES" / "index.md").read_text() == "authored"
    finally:
        vault.close()


def test_unicode_destination_follows_native_filesystem(tmp_path: Path) -> None:
    """An ASCII-only anchor still distinguishes native Unicode identity."""
    composed = tmp_path / "café"
    decomposed = tmp_path / "cafe\u0301"
    composed.mkdir()
    aliases = decomposed.exists() and decomposed.samefile(composed)
    composed.rmdir()
    (tmp_path / "notes.md").write_text("# Notes\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("café",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        if aliases:
            with pytest.raises(InvalidRequestError, match="okf_generate_index"):
                vault.writer.write("cafe\u0301/index.md", "authored")
            assert not decomposed.exists()
        else:
            vault.writer.write("cafe\u0301/index.md", "authored")
            assert (decomposed / "index.md").read_text() == "authored"
        assert not list(tmp_path.glob(".mvmcp-probe-*"))
    finally:
        vault.close()


def test_missing_folder_operand_never_reaches_samefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A distinct alternate folder must not make an absent operand crash."""
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    (tmp_path / "GUIDES").mkdir()
    missing = tmp_path / "guides"
    original_exists = Path.exists

    def exists(path: Path) -> bool:
        return False if str(path) == str(missing) else original_exists(path)

    original_samefile = Path.samefile

    def samefile(path: Path, other: Path) -> bool:
        if str(other) == str(missing):
            raise FileNotFoundError(str(other))
        return original_samefile(path, other)

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "samefile", samefile)
    assert designated_index_path(tmp_path, "guides/index.md", ("guides",))


def test_filesystem_probe_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.okf_capabilities import _probe

    before = set(tmp_path.iterdir())
    case, unicode = _probe(tmp_path)
    probe_entry = tmp_path / "Aé"
    probe_entry.touch()
    assert case == (tmp_path / "aÉ").exists()
    assert unicode == (tmp_path / "Ae\u0301").exists()
    probe_entry.unlink()
    assert set(tmp_path.iterdir()) == before

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(
        "markdown_vault_mcp.okf_capabilities.tempfile.TemporaryDirectory", denied
    )
    assert _probe(tmp_path) == (True, True)


def test_symlink_is_not_filesystem_wide_case_evidence(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    (tmp_path / "guides").mkdir()
    if (tmp_path / "GUIDES").exists():
        pytest.skip("filesystem aliases case variants")
    (tmp_path / "GUIDES").symlink_to(tmp_path / "guides", target_is_directory=True)
    (tmp_path / "OTHER").mkdir()
    (tmp_path / "OTHER" / "index.md").write_text("authored")
    assert not designated_index_path(tmp_path, "OTHER/index.md", ("other",))
