"""Graph commands expose native indexed topology and outcomes in either mode."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_discovery import reader_backend


def _data(output: str, command: str, mode: str) -> Any:
    if mode != "agent":
        return json.loads(output)
    header, *records = output.splitlines()
    assert header.startswith(f"OK {command} ")
    assert "index_stale=" in header
    values = [json.loads(record) for record in records]
    return values[0] if command == "connection" else values


@pytest.mark.usefixtures("graph_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("mode", ["json", "text", "agent"])
@pytest.mark.parametrize(
    ("arguments", "key", "expected"),
    [
        (
            ["backlinks", "notes/beta.md"],
            "source_path",
            ["alpha.md", "notes/deep/delta.md"],
        ),
        (["outlinks", "alpha.md"], "target_path", ["missing-root.md", "notes/beta.md"]),
        (["broken-links", "--folder", "notes"], "source_path", ["notes/beta.md"]),
        (["orphans"], "path", ["island.md"]),
        (["hubs", "--limit", "1"], "path", ["notes/beta.md"]),
        (
            ["connection", "notes/deep/delta.md", "notes/ø.md"],
            "path",
            ["notes/deep/delta.md", "notes/beta.md", "notes/ø.md"],
        ),
    ],
)
def test_graph_command_values(
    remote: bool, mode: str, arguments: list[str], key: str, expected: list[str]
) -> None:
    """Wrong graph dispatch, folder, limit or source/target order changes real values."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, [*arguments, *connection, *([] if mode == "text" else ["--" + mode])]
        )
    assert result.exit_code == 0, result.output
    data = _data(result.stdout, arguments[0], mode)
    if arguments[0] == "connection":
        assert data == {"found": True, "path": expected, "hops": 2}
    else:
        assert sorted(row[key] for row in data) == expected
        if arguments[0] == "hubs":
            assert data[0]["backlink_count"] == 2
        if arguments[0] == "outlinks":
            assert {row["target_path"]: row["exists"] for row in data} == {
                "missing-root.md": False,
                "notes/beta.md": True,
            }


@pytest.mark.usefixtures("graph_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["backlinks", "notes/beta.md", "--limit", "1"], 1),
        (["outlinks", "alpha.md", "--limit", "0"], 0),
        (["broken-links", "--folder", ""], ["alpha.md"]),
        (["broken-links", "--folder", "notes/"], ["notes/beta.md"]),
        (["broken-links", "--folder", "absent"], []),
        (["backlinks", "island.md"], []),
        (["outlinks", "island.md"], []),
        (
            ["connection", "alpha.md", "island.md"],
            {"found": False, "path": [], "hops": -1},
        ),
        (
            ["connection", "notes/deep/delta.md", "notes/ø.md", "--max-depth", "1"],
            {"found": False, "path": [], "hops": -1},
        ),
        (
            ["connection", "notes/ø.md", "alpha.md", "--max-depth", "0"],
            {"found": True, "path": ["notes/ø.md", "alpha.md"], "hops": 1},
        ),
        (
            ["connection", "notes/ø.md", "notes/ø.md"],
            {"found": True, "path": ["notes/ø.md"], "hops": 0},
        ),
    ],
)
def test_graph_native_boundaries(
    remote: bool, arguments: list[str], expected: Any
) -> None:
    """Empty results stay successful, root/subtree scopes stay distinct, native depth clamps."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, [*arguments, *connection, "--json", "--wait-for-pending-writes"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    if type(expected) is int:
        assert len(data) == expected
    elif isinstance(expected, list) and expected:
        assert [row["source_path"] for row in data] == expected
    else:
        assert data == expected


@pytest.mark.usefixtures("graph_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize(
    "arguments",
    [
        ["backlinks", "ALPHA.md"],
        ["outlinks", "missing.md"],
        ["connection", "missing.md", "alpha.md"],
        ["connection", "alpha.md", "missing.md"],
        ["connection", "missing.md", "missing.md"],
        ["outlinks", "../escape.md"],
    ],
)
def test_graph_missing_endpoints_are_errors(remote: bool, arguments: list[str]) -> None:
    """Missing, wrong-case and invalid identities must not become empty graph successes."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, [*arguments, *connection, "--json"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "ERROR:" in result.stderr


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_connection_upper_depth_clamp(graph_source: Path, remote: bool) -> None:
    """Depth above ten cannot expose an eleven-hop connection."""
    for i in range(12):
        (graph_source / f"chain{i}.md").write_text(
            f"# {i}\n" + (f"[Next](chain{i + 1}.md)\n" if i < 11 else "")
        )
    with reader_backend(remote) as connection:
        found = CliRunner().invoke(
            app,
            [
                "connection",
                "chain0.md",
                "chain10.md",
                "--max-depth",
                "100",
                *connection,
                "--json",
            ],
        )
        absent = CliRunner().invoke(
            app,
            [
                "connection",
                "chain0.md",
                "chain11.md",
                "--max-depth",
                "100",
                *connection,
                "--json",
            ],
        )
    assert found.exit_code == absent.exit_code == 0, (found.output, absent.output)
    assert json.loads(found.stdout) == {
        "found": True,
        "path": [f"chain{i}.md" for i in range(11)],
        "hops": 10,
    }
    assert json.loads(absent.stdout) == {"found": False, "path": [], "hops": -1}


_NAMES = ["a.md", "n/é.md", "n/ß.md", "isolated.md"]
_EDGES = st.sets(st.tuples(st.integers(0, 3), st.integers(0, 3)))


def _write_graph(root: Path, edges: set[tuple[int, int]]) -> None:
    for i, name in enumerate(_NAMES):
        note = root / name
        note.parent.mkdir(exist_ok=True)
        note.write_text(
            f"# Note {i}\n"
            + "".join(f"[edge](/{_NAMES[b]})\n" for a, b in sorted(edges) if a == i),
            encoding="utf-8",
        )


def _invoke_local(directory: str, *args: str) -> Any:
    result = CliRunner().invoke(
        app, [*args, "--local", "--source-dir", directory, "--json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@given(_EDGES, st.integers(0, 3))
@settings(max_examples=24, deadline=None)
def test_graph_directed_edge_property(edges: set[tuple[int, int]], source: int) -> None:
    """Backlink and outlink adapters preserve the fixture's exact directed edges."""
    with TemporaryDirectory() as directory:
        _write_graph(Path(directory), edges)
        outgoing = _invoke_local(directory, "outlinks", _NAMES[source])
        incoming = _invoke_local(directory, "backlinks", _NAMES[source])
        assert {row["target_path"] for row in outgoing} == {
            _NAMES[b] for a, b in edges if a == source
        }
        assert {row["source_path"] for row in incoming} == {
            _NAMES[a] for a, b in edges if b == source
        }


@given(_EDGES, st.integers(0, 3), st.integers(0, 3))
@settings(max_examples=24, deadline=None)
def test_graph_connection_property(
    edges: set[tuple[int, int]], source: int, target: int
) -> None:
    """Connection adapters retain shortest undirected hops, endpoints and empty outcomes."""
    distances = [[0 if a == b else 99 for b in range(4)] for a in range(4)]
    for a, b in edges:
        if a != b:
            distances[a][b] = distances[b][a] = 1
    # Independent all-pairs oracle; do not reuse native graph traversal.
    for intermediate in range(4):
        for a in range(4):
            for b in range(4):
                distances[a][b] = min(
                    distances[a][b],
                    distances[a][intermediate] + distances[intermediate][b],
                )
    with TemporaryDirectory() as directory:
        _write_graph(Path(directory), edges)
        forward = _invoke_local(directory, "connection", _NAMES[source], _NAMES[target])
        reverse = _invoke_local(directory, "connection", _NAMES[target], _NAMES[source])
        distance = distances[source][target]
        if distance == 99:
            assert forward == reverse == {"found": False, "path": [], "hops": -1}
        else:
            assert forward["found"] is reverse["found"] is True
            assert forward["hops"] == reverse["hops"] == distance
            assert forward["path"][0] == reverse["path"][-1] == _NAMES[source]
            assert forward["path"][-1] == reverse["path"][0] == _NAMES[target]
            assert len(forward["path"]) == distance + 1
            for a, b in zip(forward["path"], forward["path"][1:], strict=False):
                assert (_NAMES.index(a), _NAMES.index(b)) in edges or (
                    _NAMES.index(b),
                    _NAMES.index(a),
                ) in edges


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("command", ["orphans", "hubs", "broken-links"])
def test_empty_graph_lists_succeed(
    graph_source: Path, remote: bool, command: str
) -> None:
    """A completed empty graph returns lists rather than unavailable or fabricated notes."""
    for note in graph_source.rglob("*.md"):
        note.unlink()
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, [command, *connection, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


@pytest.mark.usefixtures("graph_source")
def test_remote_graph_does_not_construct_local_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A graph HTTP read must succeed when client-side source configuration is invalid."""
    with reader_backend(True) as connection:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/missing/client/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "not-a-provider")
        result = CliRunner().invoke(
            app,
            ["connection", "notes/deep/delta.md", "notes/ø.md", *connection, "--json"],
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "found": True,
        "path": ["notes/deep/delta.md", "notes/beta.md", "notes/ø.md"],
        "hops": 2,
    }


def test_graph_shared_index_never_initializes_embedding_model(
    graph_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graph reads need only the completed keyword index, even with a local model configured."""
    from markdown_vault_mcp.vault import Vault, VaultSettings

    index = tmp_path / "graph.sqlite"
    vault = Vault(source_dir=graph_source, settings=VaultSettings(index_path=index))
    try:
        vault.index.build_index()
    finally:
        vault.close()
    before = index.read_bytes()
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "fastembed")
    vectors = tmp_path / "vectors.db"
    vectors.write_bytes(b"operator-owned vector sidecar must stay untouched")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(vectors))

    def forbidden_model(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("graph read initialized an embedding model")

    monkeypatch.setattr(
        "markdown_vault_mcp.providers.get_embedding_provider", forbidden_model
    )
    result = CliRunner().invoke(
        app, ["backlinks", "notes/beta.md", "--local", "--json"]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert [row["source_path"] for row in json.loads(result.stdout)] == [
        "alpha.md",
        "notes/deep/delta.md",
    ]
    assert index.read_bytes() == before
    assert vectors.read_bytes() == b"operator-owned vector sidecar must stay untouched"


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_graph_preserves_link_rows_and_distinct_hub_sources(
    graph_source: Path, remote: bool
) -> None:
    """Repeated link rows retain all fields while hub counts remain distinct-note counts."""
    (graph_source / "alpha.md").write_text(
        "# Alpha\n[First](notes/beta.md#Heading)\n[Second](notes/beta.md)\n",
        encoding="utf-8",
    )
    with reader_backend(remote) as connection:
        outgoing = CliRunner().invoke(
            app, ["outlinks", "alpha.md", *connection, "--json"]
        )
        hubs = CliRunner().invoke(app, ["hubs", "--limit", "1", *connection, "--json"])
    assert outgoing.exit_code == hubs.exit_code == 0, (outgoing.output, hubs.output)
    assert json.loads(outgoing.stdout) == [
        {
            "target_path": "notes/beta.md",
            "link_text": "First",
            "link_type": "markdown",
            "fragment": "Heading",
            "exists": True,
            "raw_target": "notes/beta.md#Heading",
        },
        {
            "target_path": "notes/beta.md",
            "link_text": "Second",
            "link_type": "markdown",
            "fragment": None,
            "exists": True,
            "raw_target": "notes/beta.md",
        },
    ]
    assert json.loads(hubs.stdout)[0] == {
        "path": "notes/beta.md",
        "title": "Beta",
        "folder": "notes",
        "backlink_count": 2,
    }


@given(st.one_of(st.none(), st.integers(-(2**63), 2**63 - 1)))
@settings(max_examples=16, deadline=None)
def test_graph_limit_property(limit: int | None) -> None:
    """Native link limits allow zero/negative values and have no hidden ten-row default."""
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "hub.md").write_text(
            "# Hub\n" + "".join(f"[Spoke](spoke{i}.md)\n" for i in range(12))
        )
        for i in range(12):
            (root / f"spoke{i}.md").write_text(f"# Spoke {i}\n[Hub](hub.md)\n")
        options = [] if limit is None else ["--limit", str(limit)]
        for command in ["backlinks", "outlinks", "hubs"]:
            arguments = [] if command == "hubs" else ["hub.md"]
            rows = _invoke_local(directory, command, *arguments, *options)
            population = 13 if command == "hubs" else 12
            cap = (10 if command == "hubs" else population) if limit is None else limit
            assert len(rows) == (population if cap < 0 else min(population, cap))
            if command == "hubs" and rows:
                assert rows[0]["path"] == "hub.md"
                assert rows[0]["backlink_count"] == 12
