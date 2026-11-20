"""Strict publication refuses artifacts whose report is not safe to publish."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.okf_export_report import PublicationRefused
from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.mark.parametrize(
    ("name", "body", "verdict"),
    [
        ("README.md", b"# Read me\n", "nonconformant"),
        ("note.md", b"\xff", "incomplete"),
        ("note.md", b"---\ntype: Note\n---\nText\n", "conformant"),
    ],
)
def test_strict_artifact_verdict(
    tmp_path: Path, name: str, body: bytes, verdict: str
) -> None:
    (tmp_path / name).write_bytes(body)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config)
        assert faithful.report.verdict == verdict
        if verdict == "conformant":
            assert build_okf_bundle(vault, config, strict=True).data.startswith(b"PK")
        else:
            with pytest.raises(PublicationRefused) as refused:
                build_okf_bundle(vault, config, strict=True)
            assert refused.value.report.verdict == verdict
            assert name in (
                refused.value.report.unchecked
                if verdict == "incomplete"
                else [f.path for f in refused.value.report.format_findings]
            )
    finally:
        vault.close()


def test_large_note_relocation_failure_is_not_masked(tmp_path: Path) -> None:
    import io
    import zipfile

    scope = tmp_path / "scope"
    scope.mkdir()
    original = (
        "---\ntype: Note\nexecutor: &loop\n  resource: /scope/b.md\n  self: *loop\n---\n"
        + "x" * 70000
    ).encode()
    (scope / "a.md").write_bytes(original)
    (scope / "b.md").write_text("---\ntype: Note\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config, folder="scope")
        with zipfile.ZipFile(io.BytesIO(faithful.data)) as archive:
            assert archive.read("a.md") == original
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, folder="scope", strict=True)
        assert refused.value.report.uninspected["scope/a.md"] == "metadata_complexity"
    finally:
        vault.close()


def test_cli_export_preserves_destination_modes(tmp_path: Path, monkeypatch) -> None:
    import os
    import stat

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("---\ntype: Note\n---\n")
    destination = tmp_path / "out.zip"
    from markdown_vault_mcp.managers import _write_kernel

    monkeypatch.setattr(_write_kernel, "_umask", None)
    old_umask = os.umask(0o027)
    try:
        result = CliRunner().invoke(
            app, ["export", str(destination), "--source-dir", str(root)]
        )
    finally:
        os.umask(old_umask)
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
    destination.chmod(0o664)
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(destination.stat().st_mode) == 0o664


def test_cli_external_hardlink_is_safely_replaced(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("---\ntype: Note\n---\noriginal")
    destination = tmp_path / "out.zip"
    destination.hardlink_to(note)
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert note.read_text() == "---\ntype: Note\n---\noriginal"
    assert destination.read_bytes().startswith(b"PK")


@pytest.mark.parametrize(
    ("folder", "body", "observation"),
    [
        ("scope", "[outside](/outside.md)", "dependencies"),
        ("", "[missing](missing.md)", "broken_links"),
    ],
)
def test_strict_refuses_scope_observations(
    tmp_path: Path, folder: str, body: str, observation: str
) -> None:
    scope = tmp_path / folder
    if folder:
        scope.mkdir()
    if not body.startswith("---"):
        body = "---\ntype: Note\n---\n" + body
    (scope / "note.md").write_text(body)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config, folder=folder)
        assert faithful.report.verdict == "conformant"
        assert getattr(faithful.report, observation)
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, folder=folder, strict=True)
        assert getattr(refused.value.report, observation)
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


def test_cli_options_include_hidden_and_externalize_collision(tmp_path: Path) -> None:
    import zipfile

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    scope = root / "guides"
    scope.mkdir(parents=True)
    (scope / "a.md").write_text("---\ntype: Note\n---\n[other](/guides/b.md)")
    (scope / "b.md").write_text("---\ntype: Note\n---\nB")
    destination = tmp_path / "bundle.zip"
    runner = CliRunner()
    strict = runner.invoke(
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
    assert strict.exit_code == 0, strict.output
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"a.md", "b.md"}
    (scope / "a.md").write_text("[outside](/b.md)")
    (scope / ".hidden.md").write_text("# hidden")
    result = runner.invoke(
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


@pytest.mark.parametrize("value", ["../outside.md", "ambiguous.md"])
@pytest.mark.parametrize(
    "field",
    [
        "resource: {v}",
        "sources:\n  - resource: {v}",
        "computation: {v}",
        "executor:\n  resource: {v}",
        "attester:\n  resource: {v}",
    ],
)
def test_strict_checks_root_metadata(tmp_path: Path, value: str, field: str) -> None:
    original = f"---\ntype: Note\n{field.format(v=value)}\n---\n"
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config)
        with pytest.raises(PublicationRefused):
            build_okf_bundle(vault, config, strict=True)
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(faithful.data)) as archive:
            assert archive.read("note.md").decode() == original
    finally:
        vault.close()


def test_nested_root_metadata_resolves_relative_paths_and_source_names(
    tmp_path: Path,
) -> None:
    import io
    import zipfile

    (tmp_path / "nested").mkdir()
    original = "---\ntype: Note\nresource: ../target.md\n---\n"
    (tmp_path / "nested/note.md").write_text(original)
    (tmp_path / "target.md").write_text("---\ntype: Note\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), strict=True
        )
        assert result.report.dependencies == []
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("nested/note.md").decode() == original
        (tmp_path / "nested/note.md").write_text(
            original.replace("../target.md", "../../outside.md")
        )
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), strict=True)
        assert refused.value.report.dependencies[0].path == "nested/note.md"
    finally:
        vault.close()


def test_strict_refuses_uninspected_metadata(tmp_path: Path) -> None:
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "a.md").write_text(
        "---\ntype: Note\nexecutor: &loop\n  resource: /scope/b.md\n  self: *loop\n---\n"
    )
    (scope / "b.md").write_text("---\ntype: Note\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        assert build_okf_bundle(vault, config, folder="scope").report.uninspected
        with pytest.raises(PublicationRefused) as refused:
            build_okf_bundle(vault, config, folder="scope", strict=True)
        assert "metadata_complexity" in refused.value.report.uninspected.values()
    finally:
        vault.close()


@pytest.mark.parametrize("kind", ["direct", "symlink"])
def test_cli_refuses_vault_destination_and_alias(tmp_path: Path, kind: str) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("---\ntype: Note\n---\noriginal")
    alias = tmp_path / "alias.md"
    if kind == "symlink":
        alias.symlink_to(note)
    destination = note if kind == "direct" else alias
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root)]
    )
    assert result.exit_code != 0
    assert note.read_text() == "---\ntype: Note\n---\noriginal"


def test_cli_atomic_write_failure_preserves_destination(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("---\ntype: Note\n---\n")
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"original")

    def fail(*_args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(Path, "replace", fail)
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root)]
    )
    assert result.exit_code != 0
    assert "ERROR:" in result.output
    assert destination.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_warns_when_git_snapshot_omits_edits(tmp_path: Path) -> None:
    import subprocess

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (root / "note.md").write_text("---\ntype: Note\n---\ncommitted")
    git("add", "note.md")
    git("commit", "-qm", "fixture")
    (root / "note.md").write_text("---\ntype: Note\n---\nedited")
    destination = tmp_path / "bundle.zip"
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root), "--strict"]
    )
    assert result.exit_code == 0, result.output
    assert "uncommitted" in result.output and "note.md" in result.output


def test_cli_matches_indexed_export_for_wikilinks(tmp_path: Path) -> None:
    import zipfile

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("---\ntype: Note\n---\nSee [[b]]\n")
    (root / "b.md").write_text("---\ntype: Note\n---\nB\n")
    destination = tmp_path / "export.zip"
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(destination) as archive:
        assert b"[b](/b.md)" in archive.read("a.md")


def test_cli_index_build_does_not_modify_vault(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    (root / "a.md").write_text("---\ntype: Note\n---\nSee [[b]]\n")
    (root / "b.md").write_text("---\ntype: Note\n---\nB\n")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    result = CliRunner().invoke(
        app, ["export", str(tmp_path / "out.zip"), "--source-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize(
    "case", ["folder", "external_base", "destination_parent", "source"]
)
def test_cli_input_errors_are_readable(tmp_path: Path, case: str) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    if case != "source":
        root.mkdir()
    destination = tmp_path / (
        "missing/out.zip" if case == "destination_parent" else "out.zip"
    )
    options = {
        "folder": ["--folder", "missing"],
        "external_base": ["--external-base", "ftp://example.org"],
    }.get(case, [])
    result = CliRunner().invoke(
        app, ["export", str(destination), "--source-dir", str(root), *options]
    )
    assert result.exit_code != 0
    assert "ERROR:" in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("scope", ["", "scope"])
@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (".", True),
        ("./a.md", True),
        ("/{prefix}a.md", True),
        ("./dir/", True),
        ("./a.md/", False),
        ("/{prefix}a.md/", False),
        ("../../outside.md", False),
        ("./%2Fsecret.md", False),
        ("./%FF.md", False),
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "resource: {v}",
        "sources:\n  - resource: {v}",
        "computation: {v}",
        "executor:\n  resource: {v}",
        "attester:\n  resource: {v}",
    ],
)
def test_metadata_path_validation(
    tmp_path: Path, scope: str, value: str, accepted: bool, field: str
) -> None:
    import io
    import zipfile

    folder = tmp_path / scope
    folder.mkdir(exist_ok=True)
    (folder / "dir").mkdir()
    (folder / "dir/child.md").write_text("---\ntype: Note\n---\n")
    (folder / "a.md").write_text("---\ntype: Note\n---\n")
    value = value.format(prefix=f"{scope}/" if scope else "")
    original = f"---\ntype: Note\n{field.format(v=value)}\n---\n"
    (folder / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        faithful = build_okf_bundle(vault, config, folder=scope)
        if not scope:
            with zipfile.ZipFile(io.BytesIO(faithful.data)) as archive:
                assert archive.read("note.md").decode() == original
        if accepted:
            assert (
                build_okf_bundle(
                    vault, config, folder=scope, strict=True
                ).report.dependencies
                == []
            )
        else:
            with pytest.raises(PublicationRefused) as refused:
                build_okf_bundle(vault, config, folder=scope, strict=True)
            assert refused.value.report.dependencies[0].path == "note.md"
    finally:
        vault.close()
