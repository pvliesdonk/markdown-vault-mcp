"""The CLI separates the commands that read from the ones that write (#2005)."""

from __future__ import annotations

import ast
from pathlib import Path

import click
import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from markdown_vault_mcp._cli_reader import register_read_commands
from markdown_vault_mcp.cli import app

# Commands that change the index, the stored vectors or the vault.
_WRITES = {"admin", "index", "reindex", "embeddings", "serve"}


def _command_names(group: click.Group, prefix: str = "") -> set[str]:
    names: set[str] = set()
    for name, command in group.commands.items():
        names.add(prefix + name)
        if isinstance(command, click.Group):
            names |= _command_names(command, f"{prefix}{name} ")
    return names


def test_read_commands_alone_reach_no_write_command() -> None:
    """An app built from the read registration holds no command that writes."""
    reader = typer.Typer()
    register_read_commands(reader)
    names = _command_names(get_command(reader))  # type: ignore[arg-type]
    assert "search" in names
    assert "read" in names
    assert not {name.split()[0] for name in names} & _WRITES


def test_read_module_imports_no_write_command_at_load() -> None:
    """The read module's top-level imports stay clear of the admin side and cli."""
    source = Path("src/markdown_vault_mcp/_cli_reader.py").read_text(encoding="utf-8")
    top_level = [
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in top_level
        for alias in node.names
    }
    assert "markdown_vault_mcp._cli_admin" not in modules
    assert "markdown_vault_mcp.cli" not in modules


def test_help_lists_admin_and_hides_the_old_names() -> None:
    """The help shows the admin group; the shipped names are hidden aliases."""
    visible = {
        name
        for name, command in get_command(app).commands.items()  # type: ignore[attr-defined]
        if not command.hidden
    }
    assert "admin" in visible
    assert not visible & {"index", "reindex", "embeddings"}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (["index", "--help"], "admin index"),
        (["reindex", "--help"], "admin reindex"),
        (["embeddings", "import", "--help"], "admin embeddings import"),
    ],
)
def test_old_names_keep_working(old: list[str], new: str) -> None:
    """A shipped name still resolves to the same command under admin."""
    runner = CliRunner()
    assert runner.invoke(app, old).exit_code == 0
    assert runner.invoke(app, [*new.split(), "--help"]).exit_code == 0


def test_old_name_runs_the_command_and_names_the_new_form(tmp_path: Path) -> None:
    """Running an alias does the work and points at the admin command on stderr."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nneedle\n", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["index", "--source-dir", str(vault), "--index-path", str(tmp_path / "i.db")],
    )
    assert result.exit_code == 0, result.output
    assert "Indexed 1 documents" in result.stdout
    assert "markdown-vault-mcp admin index" in result.stderr
