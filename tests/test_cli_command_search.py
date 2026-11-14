"""Offline command discovery behavior and native Typer metadata boundaries."""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING, Any

import pytest
import typer
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_command_search import command_tree, explode

if TYPE_CHECKING:
    from pathlib import Path

TASKS = [
    ("search the vault by meaning", "search", 1),
    ("read one section of a document", "read", 1),
    ("show recently modified notes", "recent", 1),
    ("show headings in a note", "toc", 1),
    ("find backlinks to a note", "links to", 1),
    ("show outgoing links from a note", "links from", 1),
    ("find a link path between two notes", "links path", 3),
    ("find a note by filename with a typo", "query path", 3),
    ("count all notes matching a keyword", "query count", 1),
    ("group notes into buckets by tag", "query aggregate", 1),
]


def _search(target: typer.Typer, query: str, limit: int = 5) -> dict[str, Any]:
    result = CliRunner().invoke(
        target, ["cli", "search", query, "--json", "--limit", str(limit)]
    )
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    payload: dict[str, Any] = json.loads(result.stdout)
    assert set(payload) == {"query", "results"}
    assert payload["query"] == query.strip()
    for row in payload["results"]:
        assert set(row) == {"command", "summary", "usage"}
        assert all(isinstance(value, str) for value in row.values())
    return payload


def _fixture_app() -> typer.Typer:
    from markdown_vault_mcp._cli_search import register_search_commands

    target = command_tree()
    register_search_commands(target)
    return target


@pytest.mark.parametrize(("task", "expected", "top"), TASKS)
def test_real_tasks(task: str, expected: str, top: int) -> None:
    """Natural tasks lead to runnable commands with a bounded result shape."""
    rows = _search(app, task)["results"]
    assert f"markdown-vault-mcp {expected}" in [row["command"] for row in rows[:top]]
    for row in rows:
        assert (
            CliRunner().invoke(app, [*row["command"].split()[1:], "--help"]).exit_code
            == 0
        )


def test_live_registration_and_native_usage() -> None:
    """Late registered leaves retain native required/optional/variadic usage."""
    target = _fixture_app()
    assert _search(target, "quasar")["results"] == []

    @target.command(short_help="Quasar records.")
    def quasar(
        record: str,
        suffix: str = typer.Argument("tail"),
        labels: list[str] = typer.Argument(None),  # noqa: B008 -- native variadic metadata
        danger: str = typer.Option(default_factory=explode),
    ) -> None:
        raise AssertionError((record, suffix, labels, danger))

    row = _search(target, "quasar")["results"][0]
    assert row == {
        "command": "fixture quasar",
        "summary": "Quasar records.",
        "usage": "fixture quasar [OPTIONS] {record} [suffix] [labels]...",
    }


def test_pruning_runnable_groups_and_ties() -> None:
    """Pruned subtrees disappear; a callback alone never makes a group runnable."""
    rows = _search(_fixture_app(), "tie match", 20)["results"]
    paths = [row["command"] for row in rows]
    assert paths == sorted(paths)
    assert set(paths) == {
        "fixture alpha",
        "fixture beta",
        "fixture runnable",
        "fixture runnable child",
        "fixture requires-child child",
        "fixture help-only child",
    }
    assert _search(_fixture_app(), "read")["results"][0]["summary"] == "Read a record."
    assert _search(_fixture_app(), "banana")["results"] == []


def test_limits_and_unquoted_query() -> None:
    """Limits truncate deterministic ranking, with quoted/unquoted equivalence."""
    target = _fixture_app()
    all_rows = _search(target, "tie match", 20)["results"]
    assert _search(target, "tie match", 1)["results"] == all_rows[:1]
    assert len(_search(target, "tie match")["results"]) == 5
    result = CliRunner().invoke(target, ["cli", "search", "tie", "match", "--json"])
    assert json.loads(result.stdout) == _search(target, "tie match")


@pytest.mark.parametrize(
    "args",
    [
        [],
        [" "],
        ["x", "--limit", "0"],
        ["x", "--limit", "21"],
        ["x", "--limit", "no"],
        ["--unknown"],
    ],
)
def test_native_usage_errors(args: list[str]) -> None:
    """Invalid discovery inputs use native usage errors on stderr alone."""
    result = CliRunner().invoke(app, ["cli", "search", *args])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Usage:" in result.stderr


def test_help_pointer_text_and_no_results() -> None:
    """Text gives an actionable help command, including a successful empty result."""
    runner = CliRunner()
    assert 'cli search "<what you want to do>"' in runner.invoke(app, ["--help"]).stdout
    group_help = runner.invoke(app, ["cli"])
    assert "search" in group_help.output
    found = runner.invoke(
        app, ["cli", "search", "count matching notes", "--limit", "1"]
    )
    assert found.exit_code == 0
    assert found.stdout.startswith("markdown-vault-mcp query count  ")
    assert found.stdout.endswith(
        "Run 'markdown-vault-mcp query count --help' for flags and examples.\n"
    )
    for query in ("zzzzquasar", "?!", "the and a"):
        assert _search(app, query)["results"] == []
        empty = runner.invoke(app, ["cli", "search", query])
        assert empty.exit_code == 0
        assert (
            empty.stdout == "Run 'markdown-vault-mcp --help' for flags and examples.\n"
        )


def test_discovery_never_resolves_configuration_or_backends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Broken synthetic configuration and forbidden I/O cannot affect discovery."""
    import markdown_vault_mcp._cli_client as client
    import markdown_vault_mcp.cli as cli
    from markdown_vault_mcp.config import ProjectConfig

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_PORT", "invalid")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path / "missing"))
    profile = tmp_path / "markdown-vault-mcp" / "client.toml"
    profile.parent.mkdir()
    profile.write_text("[remote\nbroken", encoding="utf-8")
    for module, name in (
        (client, "resolve_settings"),
        (client, "_profile"),
        (cli, "_build_vault"),
        (ProjectConfig, "from_env"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
    ):
        monkeypatch.setattr(module, name, lambda *_args, **_kwargs: explode())
    assert _search(app, "count notes")["results"]
    assert not (tmp_path / "missing").exists()


def test_weighting_prefix_synonyms_and_terminal_escaping() -> None:
    """Exact command terms outrank summary text, with safe concise output."""
    target = _fixture_app()
    target.command("archive", help="Read read read read read.")(explode)
    target.command("outline", help="Outline records.")(explode)
    target.command("controls", help="Safety\x1b[31m\u2028text.")(explode)
    assert _search(target, "read")["results"][0]["command"] == "fixture read"
    assert _search(target, "outl")["results"][0]["command"] == "fixture outline"
    assert _search(target, "toc")["results"][0]["command"] == "fixture outline"
    output = CliRunner().invoke(target, ["cli", "search", "controls"]).stdout
    assert "\x1b" not in output and "\u2028" not in output
    assert "\\x1b" in output


@settings(max_examples=45, deadline=None)
@given(
    st.one_of(
        st.text(max_size=90),
        st.tuples(
            st.sampled_from(["read", "tie match", "runnable", "child"]),
            st.text(max_size=80),
        ).map(" ".join),
    ),
    st.integers(min_value=1, max_value=20),
)
def test_arbitrary_queries_are_deterministic_bounded_and_runnable(
    query: str, limit: int
) -> None:
    """Unicode queries cannot invent commands, change order or exceed the bound."""
    if not query.strip() or "\x00" in query or query.startswith("-"):
        return
    target = _fixture_app()
    first = _search(target, query, limit)
    assert first == _search(target, query, limit)
    rows = first["results"]
    assert len(rows) <= limit
    allowed = {
        "fixture read",
        "fixture alpha",
        "fixture beta",
        "fixture runnable",
        "fixture runnable child",
        "fixture requires-child child",
        "fixture help-only child",
    }
    assert {row["command"] for row in rows} <= allowed
