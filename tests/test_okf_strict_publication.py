"""Strict publication refuses artifacts whose report is not safe to publish."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.okf_export_report import PublicationRefused
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_strict_refuses_nonconformant_artifact_with_report(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Read me\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        assert build_okf_bundle(vault, config).report.verdict == "nonconformant"
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, strict=True)
        assert refused.value.report.verdict == "nonconformant"
        assert refused.value.report.format_findings[0].path == "README.md"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("body", "observation"),
    [
        ("[outside](/outside.md)", "dependencies"),
        ("---\ntype: Note\nresource: ambiguous.md\n---\n", "ambiguous_path"),
    ],
)
def test_strict_refuses_scope_observations(
    tmp_path: Path, body: str, observation: str
) -> None:
    scope = tmp_path / "scope"
    scope.mkdir()
    if not body.startswith("---"):
        body = "---\ntype: Note\n---\n" + body
    (scope / "note.md").write_text(body)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config, folder="scope")
        assert faithful.report.verdict == "conformant"
        assert getattr(faithful.report, observation)
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, folder="scope", strict=True)
        assert getattr(refused.value.report, observation)
    finally:
        vault.close()


def test_strict_returns_conformant_artifact(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("---\ntype: Note\n---\nText\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), strict=True
        )
        assert result.report.verdict == "conformant"
        assert result.data.startswith(b"PK")
    finally:
        vault.close()


def test_cli_refusal_preserves_existing_destination(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "README.md").write_text("# Read me\n")
    destination = tmp_path / "bundle.zip"
    destination.write_bytes(b"existing")
    result = CliRunner().invoke(
        app,
        ["export", str(destination), "--source-dir", str(root), "--strict"],
    )
    assert result.exit_code != 0
    assert '"verdict": "nonconformant"' in result.output
    assert destination.read_bytes() == b"existing"


def test_strict_refuses_broken_root_link(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("---\ntype: Note\n---\n[missing](missing.md)\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        assert build_okf_bundle(vault, config).report.broken_links
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, strict=True)
        assert refused.value.report.broken_links
    finally:
        vault.close()


def test_cli_never_exports_configured_server_state(tmp_path: Path, monkeypatch) -> None:
    import io
    import zipfile

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("---\ntype: Note\n---\n")
    (root / "state.json").write_text("sensitive state")
    (root / "index.sqlite").write_bytes(b"database")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(root / "state.json"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(root / "index.sqlite"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "json,sqlite")
    destination = tmp_path / "bundle.zip"
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root), "--include-hidden"]
    )
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(io.BytesIO(destination.read_bytes())) as archive:
        assert archive.namelist() == ["note.md"]
    assert (root / "state.json").read_text() == "sensitive state"
    assert (root / "index.sqlite").read_bytes() == b"database"


def test_strict_refuses_incomplete_audit(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_bytes(b"\xff")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), strict=True)
        assert refused.value.report.verdict == "incomplete"
        assert "note.md" in refused.value.report.unchecked
    finally:
        vault.close()


def test_strict_refuses_changed_snapshot(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp import okf_bundle

    path = tmp_path / "note.md"
    path.write_text("---\ntype: Note\n---\noriginal")
    write = okf_bundle._write

    def write_and_change(archive, name, data):
        write(archive, name, data)
        path.write_text("---\ntype: Note\n---\nchanged")

    monkeypatch.setattr(okf_bundle, "_write", write_and_change)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), strict=True)
        assert refused.value.report.snapshot.inconsistent_paths == ["note.md"]
    finally:
        vault.close()


def test_cli_exports_subtree_with_metadata_and_links(tmp_path: Path) -> None:
    import zipfile

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    scope = root / "guides"
    scope.mkdir(parents=True)
    (scope / "a.md").write_text(
        "---\ntype: Note\nsource: supplied\nx-custom: kept\n---\n[other](/guides/b.md)\n"
    )
    (scope / "b.md").write_text("---\ntype: Note\n---\nText\n")
    destination = tmp_path / "bundle.zip"
    result = CliRunner().invoke(
        app,
        [
            "export",
            str(destination),
            "--source-dir",
            str(root),
            "--folder",
            "guides",
            "--strict",
        ],
    )
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"a.md", "b.md"}
        body = archive.read("a.md").decode()
    assert "source: supplied" in body and "x-custom: kept" in body
    assert "[other](/b.md)" in body


def test_cli_options_include_hidden_and_externalize_collision(tmp_path: Path) -> None:
    import zipfile

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    scope = root / "guides"
    scope.mkdir(parents=True)
    (scope / "a.md").write_text("[outside](/b.md)")
    (scope / "b.md").write_text("# B")
    (scope / ".hidden.md").write_text("# hidden")
    destination = tmp_path / "bundle.zip"
    result = CliRunner().invoke(
        app,
        [
            "export",
            str(destination),
            "--source-dir",
            str(root),
            "--folder",
            "guides",
            "--include-hidden",
            "--external-base",
            "https://example.org/vault",
        ],
    )
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(destination) as archive:
        assert ".hidden.md" in archive.namelist()
        assert b"https://example.org/vault/b.md" in archive.read("a.md")
