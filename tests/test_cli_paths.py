"""Path CLI mappings and receipts preserve the native discovery contract."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from markdown_vault_mcp._cli_output import render_reply
from markdown_vault_mcp._cli_remote import RemoteReply
from markdown_vault_mcp.cli import app
from tests.fixtures.cli_discovery import reader_backend


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("mode", ["json", "text", "agent"])
def test_path_command_preserves_native_object(remote: bool, mode: str) -> None:
    """Wrong dispatch or flattening changes actual local/HTTP note metadata."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [
                "search-path",
                "alpha.md",
                *connection,
                *([] if mode == "text" else ["--" + mode]),
            ],
        )
    assert result.exit_code == 0, result.output
    if mode == "agent":
        header, record = result.stdout.splitlines()
        assert header.startswith("OK search-path count=1 ")
        assert 'coverage="not_run" truncated=false' in header
        assert f"index_stale={'false' if remote else 'unknown'}" in header
        data = json.loads(record)
    else:
        data = json.loads(result.stdout)
    assert [item["path"] for item in data["items"]] == ["alpha.md"]
    assert data["items"][0]["frontmatter"] == {"title": "Alpha", "tags": ["shared"]}
    assert data["items"][0]["score"] is None
    assert data["coverage"] == "not_run"
    assert data["reasons"] == ["fuzzy_disabled"]
    assert data["truncated"] is False
    assert ("index_stale" in data) is remote


@given(st.text(st.integers(0, 0x10FFFF).map(chr)), st.booleans())
@settings(max_examples=100, deadline=None)
def test_path_receipts_round_trip_without_header_injection(
    coverage: str, truncated: bool
) -> None:
    """Escaped typed receipts retain their values without changing record counts."""
    data: dict[str, Any] = {
        "items": [{"path": "note.md", "frontmatter": {"label": coverage}}],
        "coverage": coverage,
        "reasons": [coverage],
        "truncated": truncated,
    }
    output = io.StringIO()
    with redirect_stdout(output):
        render_reply("search-path", RemoteReply(data), "agent")
    header, record = output.getvalue().splitlines()
    prefix, receipt = header.split(" coverage=", 1)
    value, end = json.JSONDecoder().raw_decode(receipt)
    assert value == coverage
    assert receipt[end:] == f" truncated={str(truncated).lower()}"
    assert "count=1 " in prefix and "index_stale=unknown" in prefix
    assert json.loads(record) == data
    assert "\x1b" not in output.getvalue()


@pytest.mark.usefixtures("path_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize(
    ("arguments", "paths", "coverage", "truncated"),
    [
        (["alpha.md"], ["alpha.md", "notes-extra/alpha.md"], "not_run", False),
        (["al", "--folder", ""], ["alpha.md"], "not_run", False),
        (["al", "--folder", "notes/"], ["notes/alpine.md"], "not_run", False),
        (["al", "--limit", "1"], ["alpha.md"], "not_run", True),
        (["STRASSE"], ["notes/Straße.md"], "not_run", False),
        (["star[1]"], ["notes/star[1].md"], "not_run", False),
        (
            ["al", "--filter", "tags=shared", "--filter", "category=a=b"],
            ["notes/alpine.md"],
            "not_run",
            False,
        ),
        (["alpne", "--fuzzy"], ["notes/alpine.md"], "complete", False),
        (["alpha", "--fuzzy", "--limit", "1"], ["alpha.md"], "complete", True),
        (["STRASSE", "--fuzzy"], ["notes/Straße.md"], "complete", False),
        (["Straße", "--fuzzy"], ["notes/Straße.md"], "not_run", False),
        (["absent", "--fuzzy"], [], "complete", False),
        (["al", "--folder", "absent"], [], "not_run", False),
        (["a" * 256], [], "not_run", False),
    ],
)
def test_path_native_predicates(
    remote: bool, arguments: list[str], paths: list[str], coverage: str, truncated: bool
) -> None:
    """Real tier ordering, folder boundaries and AND predicates survive transport."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [
                "search-path",
                *arguments,
                *connection,
                "--json",
                "--wait-for-pending-writes",
            ],
        )
    assert result.exit_code == 0, (result.output, result.exception)
    data = json.loads(result.stdout)
    assert [item["path"] for item in data["items"]] == paths
    assert data["coverage"] == coverage
    assert data["truncated"] is truncated
    if coverage == "complete":
        assert data["reasons"] == []
    else:
        assert data["reasons"] == [
            "ineligible_query" if "--fuzzy" in arguments else "fuzzy_disabled"
        ]


@pytest.mark.parametrize(
    "arguments",
    [
        [""],
        ["   "],
        ["bad\x00query"],
        ["a" * 257],
        ["a", "--limit", "0"],
        ["a", "--limit", "1001"],
        ["a", "--filter", "tags=x", "--filter", "tags=y"],
        ["a", "--filter", "=x"],
        ["a", "--filter", "tags"],
        ["a", "--json", "--agent"],
        ["a", "--source-dir", "/unused", "--server", "https://vault.example/mcp"],
    ],
)
def test_path_cli_refuses_bad_arguments(arguments: list[str]) -> None:
    """Malformed input is refused before any local vault or network call."""
    result = CliRunner().invoke(app, ["search-path", *arguments])
    assert result.exit_code == 2, (result.output, result.exception)
    assert result.stdout == ""


@pytest.mark.usefixtures("path_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("field", ["status", "stale", "trust_tier", "unindexed"])
def test_path_native_filter_refusals(remote: bool, field: str) -> None:
    """Unknown and computed OKF fields remain native refusals even with no hits."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            ["search-path", "absent", "--filter", field + "=x", *connection, "--json"],
        )
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "ERROR:" in result.stderr


@pytest.mark.parametrize("coverage", [None, 0, False, [], {}])
@pytest.mark.parametrize("truncated", [None, 0, 1, "false", [], {}])
def test_invalid_receipts_are_preserved_only_in_payload(
    coverage: Any, truncated: Any
) -> None:
    """Malformed receipt values must not become trusted typed header fields."""
    data = {
        "items": [],
        "total": 99,
        "offset": 7,
        "limit": 5,
        "coverage": coverage,
        "truncated": truncated,
        "index_stale": False,
    }
    output = io.StringIO()
    with redirect_stdout(output):
        render_reply("search-path", RemoteReply(data), "agent")
    header, record = output.getvalue().splitlines()
    assert "count=1 " in header and "index_stale=unknown" in header
    assert " coverage=" not in header and " truncated=" not in header
    assert json.loads(record) == data


@pytest.mark.parametrize("command", ["search-path", "other"])
def test_absent_receipts_and_other_commands_have_no_projection(command: str) -> None:
    """Absent receipt fields and unrelated object replies preserve the generic header."""
    data = {
        "items": [],
        **({"coverage": "complete", "truncated": False} if command == "other" else {}),
    }
    output = io.StringIO()
    with redirect_stdout(output):
        render_reply(command, RemoteReply(data, {"index_stale": True}), "agent")
    header, record = output.getvalue().splitlines()
    assert "count=1 " in header and "index_stale=true" in header
    assert " coverage=" not in header and " truncated=" not in header
    assert json.loads(record) == data


def test_path_shared_index_suppresses_providers_and_preserves_files(
    path_source: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed keyword snapshot needs no model, sidecar, scan or state rewrite."""
    from dataclasses import replace

    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    index = tmp_path / "paths.sqlite"
    vault = Vault(
        source_dir=path_source,
        settings=replace(
            VaultSettings.from_project_config(ProjectConfig.from_env()),
            index_path=index,
        ),
    )
    try:
        vault.index.build_index()
    finally:
        vault.close()
    state = tmp_path / "state.json"
    vectors = tmp_path / "vectors.db"
    state.write_bytes(b"operator-owned state")
    vectors.write_bytes(b"operator-owned invalid vector sidecar")
    before = {file: file.read_bytes() for file in [index, state, vectors]}
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(vectors))

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("path CLI constructed model or scanned corpus")

    monkeypatch.setattr(
        "markdown_vault_mcp.providers.get_embedding_provider", forbidden
    )
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "https://vault.example/v1"
    )
    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", forbidden)
    monkeypatch.setattr(type(vault.index), "build_index", forbidden)
    result = CliRunner().invoke(app, ["search-path", "STRASSE", "--local", "--json"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert [item["path"] for item in json.loads(result.stdout)["items"]] == [
        "notes/Straße.md"
    ]
    assert {file: file.read_bytes() for file in before} == before


@pytest.mark.parametrize("stored_reader_index", [True], indirect=True)
@pytest.mark.usefixtures("stored_reader_index")
def test_path_completed_empty_index_is_success() -> None:
    """An adopted empty completed snapshot is not an incomplete index."""
    result = CliRunner().invoke(app, ["search-path", "absent", "--local", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "items": [],
        "coverage": "not_run",
        "reasons": ["fuzzy_disabled"],
        "truncated": False,
    }


@pytest.mark.usefixtures("path_source")
def test_remote_path_never_uses_poisoned_local_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hosted path read uses its own population even with unusable client config."""
    with reader_backend(True) as connection:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/missing/client/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "not-a-provider")
        result = CliRunner().invoke(
            app, ["search-path", "STRASSE", *connection, "--json"]
        )
    assert result.exit_code == 0, result.output
    assert [row["path"] for row in json.loads(result.stdout)["items"]] == [
        "notes/Straße.md"
    ]


def test_old_server_path_capability_is_actionable_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real older server missing search_paths produces a clear capability refusal."""
    from fastmcp import FastMCP

    from tests.fixtures.cli_http import serve_http

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/missing/client/vault")
    with serve_http(FastMCP("old-vault").http_app()) as origin:
        result = CliRunner().invoke(
            app, ["search-path", "alpha", "--server", origin + "/mcp", "--json"]
        )
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "support" in result.stderr and "upgrade the server" in result.stderr


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_path_metadata_retains_native_okf(discovery_source: Any, remote: bool) -> None:
    """Path items carry whole-note frontmatter and the same native optional annotation."""
    (discovery_source / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Index\n'
    )
    (discovery_source / "alpha.md").write_text(
        "---\ntitle: Alpha\ntype: Reference\nstatus: draft\n---\n# Alpha\n"
    )
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, ["search-path", "alpha.md", *connection, "--json"]
        )
    assert result.exit_code == 0, result.output
    item = json.loads(result.stdout)["items"][0]
    assert item["frontmatter"]["status"] == "draft"
    assert item["okf"]["status"] == "draft"
    assert item["score"] is None


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_search_read_context_graph_path_workflow(
    populated_vault: Any, monkeypatch: pytest.MonkeyPatch, remote: bool
) -> None:
    """Follow real returned identities through hosted/local readers without new vectors."""
    from tests.conftest import MockEmbeddingProvider

    source = populated_vault.source_dir
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(source.parent / "vectors")
    )

    def no_embedding(_self: Any, _texts: Any) -> Any:
        raise AssertionError("workflow attempted fresh embedding")

    monkeypatch.setattr(MockEmbeddingProvider, "embed", no_embedding)
    monkeypatch.setattr(
        "markdown_vault_mcp.providers.get_embedding_provider",
        lambda _config: MockEmbeddingProvider(),
    )
    with reader_backend(remote) as connection:
        if remote:
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/missing/client/vault")
            monkeypatch.setenv(
                "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "invalid-client-provider"
            )

        def invoke(*arguments: str) -> Any:
            result = CliRunner().invoke(app, [*arguments, *connection, "--json"])
            assert result.exit_code == 0, (arguments, result.output, result.exception)
            return json.loads(result.stdout)

        hits = invoke("search", "foo", "--mode", "keyword", "--limit", "1")
        path = hits[0]["path"]
        assert path == "multi.md"
        note = invoke("read", path)
        assert "foo elsewhere" in note["content"]
        section = invoke("read", path, "--section", "B")
        assert (
            "foo elsewhere" in section["content"]
            and "foo third" not in section["content"]
        )
        similar = invoke("similar", path, "--limit", "1")
        assert [item["path"] for item in similar] == ["other.md"]
        context = invoke("context", path, "--similar-limit", "1", "--link-limit", "1")
        assert context["path"] == path
        assert [item["path"] for item in context["similar"]] == ["other.md"]
        assert invoke("outlinks", path) == []
        assert {item["path"] for item in invoke("orphans")} == {"multi.md", "other.md"}
        paths = invoke("search-path", "mult", "--fuzzy", "--limit", "1")
        assert [item["path"] for item in paths["items"]] == [path]
        assert paths["coverage"] == "complete" and paths["truncated"] is False
        discovered = paths["items"][0]["path"]
        assert (
            invoke("read", discovered, "--section", "B")["content"]
            == section["content"]
        )


_JSON_SCALAR = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
)
_JSON_VALUE = st.recursive(
    _JSON_SCALAR,
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=20,
)


@given(st.lists(_JSON_VALUE), _JSON_VALUE, _JSON_VALUE)
@settings(max_examples=80, deadline=None)
def test_path_envelope_extensions_survive_agent_output(
    items: list[Any], extra: Any, freshness: Any
) -> None:
    """One record preserves the complete native envelope regardless of population shape."""
    data = {
        "items": items,
        "coverage": "limited",
        "truncated": False,
        "reasons": ["candidate_limit"],
        "extension": extra,
    }
    output = io.StringIO()
    with redirect_stdout(output):
        render_reply(
            "search-path", RemoteReply(data, {"index_stale": freshness}), "agent"
        )
    header, record = output.getvalue().splitlines()
    assert "count=1 " in header
    expected = str(freshness).lower() if type(freshness) is bool else "unknown"
    assert f"index_stale={expected} " in header
    assert json.loads(record) == data
    assert 'coverage="limited" truncated=false' in header


@given(st.text(st.integers(0, 0x10FFFF).map(chr)))
@settings(max_examples=100, deadline=None)
def test_raw_query_validation_preserves_codepoints(value: str) -> None:
    """The CLI validates raw length/blank/NUL without rewriting literal Unicode."""
    import typer

    from markdown_vault_mcp._cli_paths import _query

    if not value.strip() or "\0" in value or len(value) > 256:
        with pytest.raises(typer.BadParameter):
            _query(value)
    else:
        assert _query(value) == value


@pytest.mark.usefixtures("discovery_source")
def test_local_typed_request_refusal_does_not_reclassify_backend_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only invalid request construction is an input error; a stored-data fault stays a fault."""
    from markdown_vault_mcp._cli_read import local_read
    from markdown_vault_mcp._read_payloads import ReadPayloadError
    from markdown_vault_mcp.facets.reader import ReaderFacet

    with pytest.raises(ReadPayloadError, match="limit"):
        local_read("search_paths", {"query": "alpha", "limit": True}, None)

    def corrupted(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("corrupt stored JSON")

    monkeypatch.setattr(ReaderFacet, "search_paths", corrupted)
    with pytest.raises(ValueError, match="corrupt stored JSON"):
        local_read("search_paths", {"query": "alpha"}, None)
