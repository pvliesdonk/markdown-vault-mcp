"""The CLI separates the commands that read from the ones that write (#2005)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from markdown_vault_mcp._cli_reader import register_read_commands
from markdown_vault_mcp.cli import app

# Commands that change the index, the stored vectors or the vault.
_WRITES = {
    "admin",
    "index",
    "reindex",
    "embeddings",
    "serve",
    "note",
    "folder",
    "fetch",
    "okf",
    "git",
}


def _command_names(group: Any, prefix: str = "") -> set[str]:
    """Every command path below *group*, sub-commands included.

    Typer's groups are not ``click.Group`` instances in every Typer release, so
    a group is recognised by its ``commands`` mapping.
    """
    names: set[str] = set()
    for name, command in group.commands.items():
        names.add(prefix + name)
        if hasattr(command, "commands"):
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


def test_lookups_are_grouped_under_links_and_query() -> None:
    """The graph and exact-query commands sit in their two groups (#2005)."""
    reader = typer.Typer()
    register_read_commands(reader)
    names = _command_names(get_command(reader))  # type: ignore[arg-type]
    assert {
        f"links {leaf}" for leaf in ("to", "from", "broken", "orphans", "hubs", "path")
    } <= names
    assert {
        f"query {leaf}"
        for leaf in ("metadata", "body", "note", "count", "aggregate", "path")
    } <= names
    assert not names & {
        "backlinks",
        "connection",
        "search-metadata",
        "search-path",
        "count",
    }


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


def test_help_lists_admin_and_index_with_other_aliases_hidden() -> None:
    """Help shows admin and index; reindex and embeddings remain hidden aliases."""
    visible = {
        name
        for name, command in get_command(app).commands.items()  # type: ignore[attr-defined]
        if not command.hidden
    }
    assert {"admin", "index"} <= visible
    assert not visible & {"reindex", "embeddings"}


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


@pytest.mark.parametrize("standalone", [False, True])
def test_old_name_runs_the_command_and_names_the_new_form(
    tmp_path: Path, standalone: bool
) -> None:
    """Running an alias does the work and points at the admin command on stderr."""
    selected_app = app
    if standalone:
        from markdown_vault_mcp._cli_admin import register_admin_commands
        from markdown_vault_mcp.cli import embeddings_import, index, reindex

        selected_app = typer.Typer()
        register_admin_commands(selected_app, index, reindex, embeddings_import)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nneedle\n", encoding="utf-8")
    result = CliRunner().invoke(
        selected_app,
        ["index", "--source-dir", str(vault), "--index-path", str(tmp_path / "i.db")],
    )
    assert result.exit_code == 0, result.output
    assert "Indexed 1 documents" in result.stdout
    assert "markdown-vault-mcp admin index" in result.stderr


@pytest.mark.parametrize(
    "options",
    [
        ["--source-dir", "unused"],
        ["--index-path", "unused"],
        ["--force"],
        ["--no-force"],
    ],
)
def test_index_parent_local_options_rejected_with_hosted_child(
    options: list[str],
) -> None:
    from unittest.mock import patch

    with patch(
        "markdown_vault_mcp.cli._build_vault", side_effect=AssertionError("local")
    ):
        result = CliRunner().invoke(
            app, ["index", *options, "reindex", "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 2, result.output
    assert "local options" in result.stderr


def test_hosted_writer_paths_have_offline_help() -> None:
    from unittest.mock import patch

    paths = [
        "note write",
        "note edit",
        "note append",
        "note delete",
        "note rename",
        "folder move",
        "fetch",
        "okf generate index",
        "okf seed log",
        "okf verify",
        "git sync",
        "index reindex",
    ]
    names = _command_names(get_command(app))
    assert set(paths) <= names
    assert not any("convert" in name for name in names)
    with patch(
        "markdown_vault_mcp.cli._build_vault", side_effect=AssertionError("local")
    ):
        for command in ["index", *paths]:
            result = CliRunner().invoke(app, [*command.split(), "--help"])
            assert result.exit_code == 0, result.output
