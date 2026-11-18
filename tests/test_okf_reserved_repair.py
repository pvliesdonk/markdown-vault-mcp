"""The deliberate reserved-file placeholder migration."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp._okf_reserved_repair import ReservedFrontmatterRepair, _strip
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_preview_and_apply_including_excluded_notes(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    root = tmp_path / "index.md"
    root.write_text(
        '---\nokf_version: "0.2"\ntitle: Index\ntype: null\nconflict_with: null\ncustom: null\n---\n# Index\n'
    )
    log = tmp_path / "guides" / "log.md"
    log.write_text("---\ntitle: Log\ntype: null\n---\n# Log\n")
    edited = tmp_path / "guides" / "index.md"
    edited.write_text("---\ntitle: Hand edited\ntype: null\n---\n# Guide\n")
    before = [p.read_bytes() for p in (root, log, edited)]
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            required_frontmatter=["title", "type", "conflict_with"],
            exclude_patterns=["guides/**"],
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        preview = repair.run()
        assert {x.path: x.keys for x in preview} == {
            "index.md": ("title", "type"),
            "guides/index.md": ("type",),
            "guides/log.md": ("title", "type"),
        }
        assert [p.read_bytes() for p in (root, log, edited)] == before
        assert repair.run(apply=True) == preview
        assert (
            root.read_text()
            == '---\nokf_version: "0.2"\nconflict_with: null\ncustom: null\n---\n# Index\n'
        )
        assert log.read_text() == "---\n---\n# Log\n"
        assert edited.read_text() == "---\ntitle: Hand edited\n---\n# Guide\n"
    finally:
        vault.close()


def test_read_only_allows_preview_but_refuses_apply(tmp_path: Path) -> None:
    (tmp_path / "log.md").write_text("---\ntitle: Log\n---\n# Log\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=True, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run()[0].keys == ("title",)
        with pytest.raises(ReadOnlyError):
            repair.run(apply=True)
    finally:
        vault.close()


def test_surgical_splice_preserves_crlf_and_comments() -> None:
    original = "---\r\ntitle: Log\r\ntype: null # user comment\r\nother: value\r\n---\r\n# Log\r\n"
    changed, keys = _strip(original, ["title", "type"], "title", "Log")
    assert keys == ("title",)
    assert changed == original.replace("title: Log\r\n", "")


def test_cli_dry_run_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    log = root / "log.md"
    log.write_text("---\ntitle: Log\ntype: null\n---\n# Log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title,type")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    runner = CliRunner()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert result.exit_code == 0, result.output
    assert "log.md: title" in result.stdout and "log.md: type" in result.stdout
    assert "title: Log" in log.read_text()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 0, result.output
    assert log.read_text() == "---\n---\n# Log\n"


def test_apply_designated_index_preserves_other_bytes(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf_capabilities import OkfCapabilities

    path = tmp_path / "index.md"
    original = b'---\r\nokf_version: "0.2"\r\ntitle: Index\r\ncustom: "preserve" # keep\r\n---\r\n# Index\r\n\r\nmanual content\r\n'
    path.write_bytes(original)
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        required_frontmatter=["title"],
        protected_paths=["index.md"],
        okf_capabilities=OkfCapabilities(
            author=False, verify="off", index=True, log="off", folders=(".",)
        ),
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run(apply=True, allow_protected=True)[0].keys == ("title",)
        assert path.read_bytes() == original.replace(b"title: Index\r\n", b"")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index.md"]
    finally:
        vault.close()


def test_custom_title_and_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import ConcurrentModificationError

    (tmp_path / "guides").mkdir()
    path = tmp_path / "guides" / "index.md"
    path.write_text("---\nname: guides\ntitle: null\n---\nbody\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            required_frontmatter=["name", "title"],
            title_field="name",
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run()[0].keys == ("name", "title")
        write = vault._doc_mgr.write

        def concurrent(*args: object, **kwargs: object) -> object:
            path.write_text("new content\n")
            return write(*args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "write", concurrent)
        with pytest.raises(ConcurrentModificationError):
            repair.run(apply=True)
        assert path.read_text() == "new content\n"
    finally:
        vault.close()


def test_ambiguous_yaml_is_left_unchanged() -> None:
    original = "---\ntitle: Log\ntitle: Log\ntype: null\n---\nbody\n"
    assert _strip(original, ["title", "type"], "title", "Log") == (original, ())
