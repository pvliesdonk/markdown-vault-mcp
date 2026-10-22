"""Exact query commands preserve the native population at both read boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
import yaml
from hypothesis import example, given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_queries import query_index as query_index
from tests.fixtures.cli_queries import query_notes as query_notes


@pytest.mark.parametrize(
    "args,paths,total",
    [
        (["search-metadata", "bodyword"], {"c.md"}, 1),
        (["search-body", "bodyword"], {"a.md", "b.md"}, 2),
        (["search-in", "a.md", "bodyword"], {"a.md"}, 1),
        (["count", "bodyword", "--scope", "body"], None, 2),
        (["aggregate", "bodyword", "--scope", "body", "--by", "tag"], None, 2),
    ],
)
def test_local_query_commands_use_exact_native_population(
    query_notes: Path, args: list[str], paths: set[str] | None, total: int
) -> None:
    """Wrong scope, path or page-local counting changes these literal outcomes."""
    result = CliRunner().invoke(
        app, [*args, "--local", "--source-dir", str(query_notes), "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total"] == total
    assert "index_stale" not in data  # A local process cannot observe a shared writer.
    if paths is not None:
        assert {item["path"] for item in data["items"]} == paths
    if args[0] == "search-metadata":
        assert all("sections" not in item for item in data["items"])
    if args[0] == "search-in":
        assert data["offset"] == 0 and data["limit"] == 1
    if args[0] == "aggregate":
        assert data["buckets"] == [
            {"key": "shared", "count": 2},
            {"key": "blue", "count": 1},
            {"key": "red", "count": 1},
        ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["count", "--mode", "semantic"],
        ["aggregate", "--by", "folder", "--mode", "hybrid"],
        ["aggregate", "--by", "unknown"],
        ["count", "--scope", "unknown"],
        ["count", "--filter", "a=1", "--filter", "a=2"],
        ["count", "--filter", "missing-separator"],
        ["count", "--modified-after", "2026-10-01"],
        [
            "count",
            "--modified-after",
            "2026-10-02T00:00:00Z",
            "--modified-before",
            "2026-10-01T00:00:00Z",
        ],
        ["search-body", "word", "--limit", "1001"],
        ["search-body", "word", "--chunks-per-file", "0"],
        ["search-body", "word", "--snippet-words", "-1"],
        ["search-in", "a.md", "word", "--offset", "1"],
        ["search-in", "a.md", "word", "--limit", "2"],
        ["search-body"],
        ["count", "--json", "--agent"],
        ["search-metadata", "--order", "path"],
        ["count", "--pattern-mode", "prefix"],
    ],
)
def test_invalid_query_input_never_opens_a_backend(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator mistakes refuse before any filesystem or transport access."""

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Invalid query opened a backend")

    monkeypatch.setattr("markdown_vault_mcp._cli_query.run_read", unexpected)
    result = CliRunner().invoke(app, [*arguments, "--local"])
    assert result.exit_code == 2, result.output
    assert result.stdout == ""


@pytest.mark.usefixtures("query_notes")
def test_query_commands_over_real_http() -> None:
    """The hosted server receives the same native scope/path/group controls."""
    from tests.fixtures.cli_discovery import reader_backend

    with reader_backend(True) as connection:
        for arguments, expected in [
            (["search-metadata", "bodyword", "--limit", "1"], 1),
            (
                [
                    "search-body",
                    "bodyword",
                    "--limit",
                    "1",
                    "--offset",
                    "1",
                    "--chunks-per-file",
                    "1",
                    "--snippet-words",
                    "1",
                ],
                2,
            ),
            (["search-in", "a.md", "bodyword", "--snippet-words", "0"], 1),
            (["count", "bodyword", "--scope", "body"], 2),
            (
                [
                    "aggregate",
                    "bodyword",
                    "--scope",
                    "body",
                    "--by",
                    "tag",
                    "--limit",
                    "1",
                ],
                2,
            ),
        ]:
            result = CliRunner().invoke(
                app, [*arguments, *connection, "--wait-for-pending-writes", "--agent"]
            )
            assert result.exit_code == 0, result.output
            header, record = result.stdout.splitlines()
            assert "count=1" in header
            assert "index_stale=false" in header
            data = json.loads(record)
            assert data["total"] == expected
            assert data["index_stale"] is False
            if arguments[0] == "search-body":
                assert data["offset"] == 1 and data["limit"] == 1
                assert len(data["items"]) == 1
                assert all(len(item["sections"]) == 1 for item in data["items"])
                assert all(
                    len(section["content"].split()) <= 1
                    for item in data["items"]
                    for section in item["sections"]
                )
            if arguments[0] == "search-in":
                assert data["items"][0]["path"] == "a.md"
                assert {
                    section["content"].strip()
                    for section in data["items"][0]["sections"]
                } == {"# Alpha\n\nbodyword one two\n\n## More\n\nbodyword three four"}
            if arguments[0] == "aggregate":
                assert data["truncated"] is True
                assert data["buckets"] == [{"key": "shared", "count": 2}]


@pytest.mark.parametrize(
    "tool", ["search-metadata", "search-body", "search-in", "count", "aggregate"]
)
def test_malformed_remote_query_is_refused(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An arbitrary object must not be presented as a successful exact total."""
    from markdown_vault_mcp._cli_read import RemoteReply

    monkeypatch.setattr(
        "markdown_vault_mcp._cli_query.run_read",
        lambda *_args, **_kwargs: RemoteReply({"total": True}),
    )
    args = [tool]
    if tool == "search-body":
        args += ["word"]
    elif tool == "search-in":
        args += ["note.md", "word"]
    elif tool == "aggregate":
        args += ["--by", "folder"]
    result = CliRunner().invoke(app, [*args, "--local", "--json"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "payload" in result.stderr


@given(
    records=st.lists(
        st.tuples(
            st.booleans(),
            st.booleans(),
            st.booleans(),
            st.one_of(st.none(), st.text()),
        ),
    ),
    scope=st.sampled_from(["all", "body", "metadata"]),
    filter_first=st.booleans(),
    size=st.integers(min_value=1, max_value=1000),
)
@example(
    records=[(False, True, True, "Straße=值"), (False, True, False, "")],
    scope="body",
    filter_first=True,
    size=1,
)
@example(
    records=[(True, False, False, None), (True, True, True, "")],
    scope="metadata",
    filter_first=False,
    size=1,
)
@example(
    records=[(False, True, False, ""), (False, True, True, "different")],
    scope="body",
    filter_first=True,
    size=1,
)
@example(
    records=[(False, True, False, "𐀀")], scope="body", filter_first=True, size=1000
)
@settings(max_examples=8, deadline=None)
def test_generated_corpora_preserve_exact_cli_populations(
    records: list[tuple[bool, bool, bool, str | None]],
    scope: str,
    filter_first: bool,
    size: int,
) -> None:
    """An independent note-set oracle pins paging/count/group totals and predicates."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as patch:
        source = Path(directory).resolve()
        # Bound notes when materializing the corpus to respect the shared host.
        records = records[:7]
        selected_tag = records[0][3] if records and filter_first else None
        expected: set[str] = set()
        expected_folders: dict[str, int] = {}
        for index, (metadata, body, nested, tag) in enumerate(records):
            folder = "nested" if nested else ""
            path = f"{folder + '/' if folder else ''}note{index}.md"
            note = source / path
            note.parent.mkdir(exist_ok=True)
            frontmatter: dict[str, Any] = {"title": "needle" if metadata else "Other"}
            if tag is not None:
                frontmatter["tags"] = [tag]
            text = "needle repeated" if body else "Unrelated content"
            note.write_text(
                "---\n"
                + yaml.safe_dump(frontmatter, allow_unicode=True)
                + "\n---\n# Heading\n\n"
                + text
                + "\n\n## Second\n\n"
                + text,
                encoding="utf-8",
            )
            matches = (
                body
                if scope == "body"
                else metadata
                if scope == "metadata"
                else metadata or body
            )
            if matches and (selected_tag is None or tag == selected_tag):
                expected.add(path)
                expected_folders[folder] = expected_folders.get(folder, 0) + 1
        for key, value in {
            "SOURCE_DIR": str(source),
            "INDEX_PATH": ":memory:",
            "INDEXED_FIELDS": "tags",
        }.items():
            patch.setenv("MARKDOWN_VAULT_MCP_" + key, value)
        patch.setenv("XDG_CONFIG_HOME", str(source / "config"))
        common = ["--local", "--json"]
        if selected_tag is not None:
            common += ["--filter", "tags=" + selected_tag]
        runner = CliRunner()

        def invoke(arguments: list[str]) -> dict[str, Any]:
            result = runner.invoke(app, [*arguments, *common])
            assert result.exit_code == 0, result.output
            return json.loads(result.stdout)

        count = invoke(["count", "needle", "--scope", scope])
        groups = invoke(["aggregate", "needle", "--scope", scope, "--by", "folder"])
        assert count["total"] == groups["total"] == len(expected)
        assert {
            bucket["key"]: bucket["count"] for bucket in groups["buckets"]
        } == expected_folders
        # search-metadata/body are fixed scopes; all-scope agreement is covered by
        # count/group above, while the public page commands deliberately fix scope.
        if scope == "all":
            return
        paths: list[str] = []
        for offset in range(0, max(1, len(expected)), size):
            page = invoke(
                [
                    "search-" + scope,
                    "needle",
                    "--limit",
                    str(size),
                    "--offset",
                    str(offset),
                ]
            )
            assert page["total"] == len(expected)
            assert page["offset"] == offset and page["limit"] == size
            assert page["truncated"] == (offset + len(page["items"]) < len(expected))
            paths.extend(item["path"] for item in page["items"])
        assert len(paths) == len(set(paths)) == len(expected)
        assert set(paths) == expected


@pytest.mark.parametrize(
    "payload,metadata",
    [
        ({"total": 0, "index_stale": False}, {"index_stale": True}),
        ({"total": 0}, {"index_stale": False}),
        ({"total": 0, "index_stale": False}, {}),
        ({"total": 0, "index_stale": "false"}, {"index_stale": False}),
    ],
)
def test_remote_freshness_cannot_be_missing_or_contradictory(
    payload: dict[str, Any], metadata: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote known freshness must describe the same observation in both forms."""
    from markdown_vault_mcp._cli_read import RemoteReply

    monkeypatch.setattr(
        "markdown_vault_mcp._cli_query.run_read",
        lambda *_a, **_kw: RemoteReply(payload, metadata),
    )
    result = CliRunner().invoke(
        app, ["count", "--server", "https://vault.example/mcp", "--agent"]
    )
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "payload" in result.stderr


@pytest.mark.parametrize(
    "error",
    [
        ValueError("private backend detail"),
        OSError("private path"),
        OverflowError("private integer"),
    ],
)
def test_backend_fault_is_safe_server_failure(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only constructor failures are input errors; backend faults remain code one."""

    def fail(*_a: object, **_kw: object) -> None:
        raise error

    monkeypatch.setattr("markdown_vault_mcp._cli_query.run_read", fail)
    result = CliRunner().invoke(app, ["count", "--local", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ERROR:" in result.stderr
    assert "private" not in result.stderr


@pytest.mark.parametrize(
    "tool,data",
    [
        (
            "aggregate_documents",
            {
                "total": 1,
                "missing": 0,
                "truncated": False,
                "buckets": [{"key": "one", "count": 2}],
            },
        ),
        (
            "aggregate_documents",
            {
                "total": 1,
                "missing": 2,
                "truncated": True,
                "buckets": [{"key": "one", "count": 1}],
            },
        ),
        (
            "query_documents",
            {
                "total": 0,
                "offset": 0,
                "limit": 1,
                "truncated": False,
                "items": [{"path": "a.md"}],
            },
        ),
        (
            "query_documents",
            {"total": 10, "offset": 0, "limit": 20, "truncated": False, "items": []},
        ),
        (
            "query_documents",
            {
                "total": 1,
                "offset": 0,
                "limit": 1,
                "truncated": False,
                "items": [
                    {
                        "path": "a.md",
                        "title": "A",
                        "folder": "",
                        "frontmatter": {},
                        "modified_at": "yesterday",
                        "score": True,
                        "sections": "broken",
                    }
                ],
            },
        ),
        (
            "aggregate_documents",
            {
                "total": 1,
                "missing": 50,
                "truncated": False,
                "buckets": [{"key": None, "count": 99}],
            },
        ),
        (
            "aggregate_documents",
            {"total": 1, "missing": 0, "truncated": False, "buckets": [{"count": 1}]},
        ),
    ],
)
def test_incoherent_query_envelopes_are_refused(
    tool: str, data: dict[str, Any]
) -> None:
    """Native shape and population constraints cannot silently become valid output."""
    from markdown_vault_mcp._read_payloads import (
        ReadPayloadError,
        validate_query_payload,
    )

    with pytest.raises(ReadPayloadError, match="payload"):
        validate_query_payload(tool, data)


def test_completed_local_query_index_is_never_rebuilt(
    query_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queries adopt an existing read-only completed index without changing bytes."""
    import hashlib

    from markdown_vault_mcp.facets.index import IndexFacet

    before = hashlib.sha256(query_index.read_bytes()).digest()

    def unexpected(*_a: object, **_kw: object) -> None:
        pytest.fail("Query attempted to rebuild a completed index")

    monkeypatch.setattr(IndexFacet, "build_index", unexpected)
    for arguments in [
        ["search-body", "bodyword", "--limit", "1"],
        ["count", "bodyword", "--scope", "body"],
        ["aggregate", "bodyword", "--scope", "body", "--by", "tag"],
    ]:
        result = CliRunner().invoke(app, [*arguments, "--local", "--agent"])
        assert result.exit_code == 0, result.output
        header, record = result.stdout.splitlines()
        assert "count=1" in header and "index_stale=unknown" in header
        data = json.loads(record)
        assert data["total"] == 2
        assert "index_stale" not in data
    assert hashlib.sha256(query_index.read_bytes()).digest() == before


def test_query_populations_exceed_page_and_bucket_caps(query_notes: Path) -> None:
    """Even more than one default page of matching notes is counted exhaustively."""
    for index in range(25):
        (query_notes / f"extra{index}.md").write_text(
            "---\ntags: [many]\n---\n# Extra\n\nbodyword\n", encoding="utf-8"
        )
    runner = CliRunner()
    results = [
        runner.invoke(
            app, ["search-body", "bodyword", "--local", "--limit", "1", "--json"]
        ),
        runner.invoke(
            app, ["count", "bodyword", "--scope", "body", "--local", "--json"]
        ),
        runner.invoke(
            app,
            [
                "aggregate",
                "bodyword",
                "--scope",
                "body",
                "--by",
                "tag",
                "--limit",
                "1",
                "--local",
                "--json",
            ],
        ),
    ]
    for result in results:
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["total"] == 27
    page, _, groups = [json.loads(result.stdout) for result in results]
    assert len(page["items"]) == 1 and page["truncated"] is True
    assert groups["buckets"] == [{"key": "many", "count": 25}]
    assert groups["truncated"] is True


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_all_groupings_use_complete_population(query_notes: Path, remote: bool) -> None:
    """Missing tags and multi-value tags retain native distinct-note bucket semantics."""
    import os

    from tests.fixtures.cli_discovery import reader_backend

    (query_notes / "missing.md").write_text("# No tags\n\nOther\n", encoding="utf-8")
    for note in query_notes.glob("*.md"):
        os.utime(note, (1790812800, 1790812800))
    with reader_backend(remote) as connection:
        results: dict[str, Any] = {}
        for by in ["folder", "month", "tag"]:
            result = CliRunner().invoke(
                app,
                [
                    "aggregate",
                    "--by",
                    by,
                    *connection,
                    "--json",
                    "--wait-for-pending-writes",
                ],
            )
            assert result.exit_code == 0, result.output
            results[by] = json.loads(result.stdout)
    assert results["folder"]["buckets"] == [{"key": "", "count": 4}]
    assert results["month"]["buckets"] == [{"key": "2026-10", "count": 4}]
    tags = results["tag"]
    assert tags["missing"] == 1
    assert {bucket["key"]: bucket["count"] for bucket in tags["buckets"]} == {
        "shared": 2,
        None: 1,
        "blue": 1,
        "other": 1,
        "red": 1,
    }
    assert sum(bucket["count"] for bucket in tags["buckets"]) == 6 > tags["total"] == 4


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_query_predicates_intersect_without_dropping_values(
    query_notes: Path, remote: bool
) -> None:
    """Empty folder, literal equals, glob and RFC3339 bounds apply together."""
    import os

    from tests.fixtures.cli_discovery import reader_backend

    (query_notes / "a.md").write_text(
        '---\ntags: ["Straße=值"]\n---\n# One\n\nbodyword\n', encoding="utf-8"
    )
    os.utime(query_notes / "a.md", (1790812800, 1790812800))
    predicates = [
        "--folder",
        "",
        "--path",
        "a.md",
        "--pattern",
        "[ab].md",
        "--filter",
        "tags=Straße=值",
        "--modified-after",
        "2026-09-30T00:00:00Z",
        "--modified-before",
        "2026-10-02T00:00:00Z",
    ]
    with reader_backend(remote) as connection:
        for command in [
            ["search-body", "bodyword"],
            ["count", "bodyword", "--scope", "body"],
            ["aggregate", "bodyword", "--scope", "body", "--by", "tag"],
        ]:
            result = CliRunner().invoke(
                app,
                [
                    *command,
                    *predicates,
                    *connection,
                    "--json",
                    "--wait-for-pending-writes",
                ],
            )
            assert result.exit_code == 0, result.output
            assert json.loads(result.stdout)["total"] == 1
        for option, value in {
            "--folder": "absent",
            "--path": "b.md",
            "--pattern": "missing*.md",
            "--filter": "tags=absent",
            "--modified-after": "2026-10-01T00:00:01Z",
            "--modified-before": "2026-10-01T00:00:00Z",
        }.items():
            narrowed = list(predicates)
            narrowed[narrowed.index(option) + 1] = value
            excluded = CliRunner().invoke(
                app,
                [
                    "count",
                    "bodyword",
                    "--scope",
                    "body",
                    *narrowed,
                    *connection,
                    "--json",
                ],
            )
            assert excluded.exit_code == 0, excluded.output
            assert json.loads(excluded.stdout)["total"] == 0, option
        wrong_case = CliRunner().invoke(
            app,
            [
                "search-in",
                "A.md",
                "bodyword",
                *connection,
                "--json",
                "--wait-for-pending-writes",
            ],
        )
        assert wrong_case.exit_code == 0, wrong_case.output
        assert json.loads(wrong_case.stdout)["total"] == 0


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_query_okf_annotations_are_optional_pages_only(
    query_notes: Path, remote: bool
) -> None:
    """An active bundle enriches pages without changing totals or group payloads."""
    from tests.fixtures.cli_discovery import reader_backend

    (query_notes / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Index\n', encoding="utf-8"
    )
    with reader_backend(remote) as connection:
        for command in [
            ["search-body", "bodyword"],
            ["count", "bodyword", "--scope", "body"],
            ["aggregate", "bodyword", "--scope", "body", "--by", "tag"],
        ]:
            result = CliRunner().invoke(
                app, [*command, *connection, "--json", "--wait-for-pending-writes"]
            )
            assert result.exit_code == 0, result.output
            data = json.loads(result.stdout)
            assert data["total"] == 2
            if command[0] == "search-body":
                assert all("okf" in item for item in data["items"])
            else:
                assert "okf" not in data
        for field in ["status", "unknown"]:
            result = CliRunner().invoke(
                app, ["count", "--filter", field + "=value", *connection, "--json"]
            )
            assert result.exit_code == 1 and result.stdout == "", result.output


def test_hosted_query_never_loads_local_configuration(
    query_notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote routing remains usable with a broken local environment and no fallback."""
    from tests.fixtures.cli_discovery import reader_backend

    def unexpected(*_a: object, **_kw: object) -> None:
        pytest.fail("Hosted query touched the local adapter")

    with reader_backend(True) as connection:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(query_notes / "absent"))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "invalid-provider")
        monkeypatch.setattr("markdown_vault_mcp._cli_query.local_read", unexpected)
        result = CliRunner().invoke(
            app,
            [
                "count",
                "bodyword",
                "--scope",
                "body",
                *connection,
                "--json",
                "--wait-for-pending-writes",
            ],
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["total"] == 2


@pytest.mark.parametrize("unsupported_options", [False, True])
def test_old_query_server_refuses_without_local_fallback(
    unsupported_options: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capability discovery refuses missing tools or schemas without dropping predicates."""
    from fastmcp import FastMCP

    from tests.fixtures.cli_http import serve_http

    server = FastMCP("old-query-server")
    called: list[bool] = []

    if unsupported_options:

        @server.tool()
        def count_documents() -> dict[str, int]:
            called.append(True)
            return {"total": 999}

    def unexpected(*_a: object, **_kw: object) -> None:
        pytest.fail("Unsupported server caused local fallback")

    monkeypatch.setattr("markdown_vault_mcp._cli_query.local_read", unexpected)
    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            [
                "count",
                "word",
                "--filter",
                "tags=keep",
                "--server",
                origin + "/mcp",
                "--json",
            ],
        )
    assert result.exit_code == 1 and result.stdout == "", result.output
    assert "upgrade" in result.stderr
    assert called == []


def test_query_deadline_and_interrupt_have_no_partial_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared transport deadline and interrupt outcome reach the new command."""
    import asyncio

    from fastmcp import FastMCP

    from tests.fixtures.cli_http import serve_http

    server = FastMCP("slow-query-server")

    @server.tool()
    async def count_documents(
        query: str = "", scope: str = "all", filters: dict[str, str] | None = None
    ) -> dict[str, Any]:
        del query, scope, filters
        await asyncio.sleep(0.3)
        return {"total": 0, "index_stale": False}

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            [
                "count",
                "word",
                "--server",
                origin + "/mcp",
                "--timeout",
                "0.05",
                "--json",
            ],
        )
    assert result.exit_code == 1 and result.stdout == "", result.output
    assert "timed out" in result.stderr

    def interrupt(*_a: object, **_kw: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("markdown_vault_mcp._cli_query.run_read", interrupt)
    interrupted = CliRunner().invoke(app, ["count", "--local", "--json"])
    assert interrupted.exit_code == 130 and interrupted.stdout == ""


@pytest.mark.parametrize("output", ["text", "json", "agent"])
def test_query_output_preserves_receipts_and_escapes_controls(
    output: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All output modes preserve native envelope values and contain terminal controls."""
    from markdown_vault_mcp._cli_read import RemoteReply

    data = {
        "items": [
            {
                "path": "note\x1b[2J\n.md",
                "title": "値\u2028title",
                "folder": "",
                "frontmatter": {},
                "modified_at": 0.0,
                "score": None,
            }
        ],
        "total": 1,
        "offset": 0,
        "limit": 20,
        "truncated": False,
        "extra_native": "kept",
    }
    monkeypatch.setattr(
        "markdown_vault_mcp._cli_query.run_read", lambda *_a, **_kw: RemoteReply(data)
    )
    result = CliRunner().invoke(
        app,
        ["search-metadata", "--local", *([] if output == "text" else ["--" + output])],
    )
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stdout
    if output == "agent":
        header, record = result.stdout.splitlines()
        assert "count=1" in header and "index_stale=unknown" in header
        assert "\u2028" not in record
        observed = json.loads(record)
    else:
        observed = json.loads(result.stdout)
    assert observed == data


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), object()])
def test_query_payload_non_json_values_fail_before_output(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even extra native fields must be representable without partial terminal output."""
    from markdown_vault_mcp._cli_read import RemoteReply

    monkeypatch.setattr(
        "markdown_vault_mcp._cli_query.run_read",
        lambda *_a, **_kw: RemoteReply({"total": 0, "extra": value}),
    )
    result = CliRunner().invoke(app, ["count", "--local", "--json"])
    assert result.exit_code == 1 and result.stdout == "", result.output
    assert "payload" in result.stderr


def test_remote_source_override_conflicts_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source directory never silently changes a selected hosted query into a local one."""

    def unexpected(*_a: object, **_kw: object) -> None:
        pytest.fail("Conflicting source/server opened a backend")

    monkeypatch.setattr("markdown_vault_mcp._cli_query.run_read", unexpected)
    result = CliRunner().invoke(
        app,
        [
            "count",
            "--server",
            "https://vault.example/mcp",
            "--source-dir",
            "absent",
            "--json",
        ],
    )
    assert result.exit_code == 2 and result.stdout == "", result.output
    assert "--source-dir" in result.stderr


@pytest.mark.parametrize(
    "data",
    [
        {
            "total": 0,
            "missing": 0,
            "truncated": False,
            "buckets": [{"key": "impossible", "count": 0}],
        },
        {"total": 2, "missing": 0, "truncated": False, "buckets": []},
        {
            "total": 2,
            "missing": 0,
            "truncated": False,
            "buckets": [{"key": "one", "count": 1}],
        },
        {
            "total": 2,
            "missing": 0,
            "truncated": False,
            "buckets": [{"key": None, "count": 2}],
        },
        {
            "total": 2,
            "missing": 1,
            "truncated": False,
            "buckets": [{"key": "one", "count": 2}],
        },
        {"total": 2, "missing": 0, "truncated": True, "buckets": []},
    ],
)
def test_aggregate_coherence_rejects_incomplete_or_contradictory_buckets(
    data: dict[str, Any],
) -> None:
    """Complete aggregate receipts cover every note and null counts match missing."""
    from markdown_vault_mcp._read_payloads import (
        ReadPayloadError,
        validate_query_payload,
    )

    with pytest.raises(ReadPayloadError, match="payload"):
        validate_query_payload("aggregate_documents", data)


@pytest.mark.parametrize("output", ["text", "json", "agent"])
def test_matching_stale_remote_receipts_remain_successful(
    output: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale read is usable and explicitly stale in the same native observation."""
    from markdown_vault_mcp._cli_read import RemoteReply

    data = {"total": 0, "index_stale": True}
    monkeypatch.setattr(
        "markdown_vault_mcp._cli_query.run_read",
        lambda *_a, **_kw: RemoteReply(data, {"index_stale": True}),
    )
    result = CliRunner().invoke(
        app,
        [
            "count",
            "--server",
            "https://vault.example/mcp",
            *([] if output == "text" else ["--" + output]),
        ],
    )
    assert result.exit_code == 0, result.output
    if output == "agent":
        header, record = result.stdout.splitlines()
        assert "index_stale=true" in header and "count=1" in header
        assert json.loads(record) == data
    else:
        assert json.loads(result.stdout) == data
        assert "WARNING" in result.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", None),
        ("folder", None),
        ("frontmatter", []),
        ("modified_at", "yesterday"),
        ("score", True),
        ("score", float("nan")),
        ("sections", "broken"),
        ("sections", [{"heading": None, "content": "body", "score": True}]),
        ("sections", [{"content": "body", "score": 1.0}]),
    ],
)
def test_each_native_item_field_is_validated_independently(
    field: str, value: object
) -> None:
    """One corrupt field cannot hide behind a different malformed field."""
    from markdown_vault_mcp._read_payloads import (
        ReadPayloadError,
        validate_query_payload,
    )

    item: dict[str, Any] = {
        "path": "a.md",
        "title": "A",
        "folder": "",
        "frontmatter": {},
        "modified_at": 0.0,
        "score": 1.0,
        "sections": [{"heading": None, "content": "body", "score": 1.0}],
    }
    item[field] = value
    with pytest.raises(ReadPayloadError, match="payload"):
        validate_query_payload(
            "query_documents",
            {"total": 1, "items": [item], "offset": 0, "limit": 1, "truncated": False},
        )


@pytest.mark.parametrize("kind", ["page", "bucket"])
def test_duplicate_native_keys_are_refused(kind: str) -> None:
    """A distinct-note population and its group keys cannot contain duplicates."""
    from markdown_vault_mcp._read_payloads import (
        ReadPayloadError,
        validate_query_payload,
    )

    if kind == "page":
        item = {
            "path": "a.md",
            "title": "A",
            "folder": "",
            "frontmatter": {},
            "modified_at": 0.0,
            "score": None,
        }
        data = {
            "total": 2,
            "items": [item, item],
            "offset": 0,
            "limit": 2,
            "truncated": False,
        }
        tool = "query_documents"
    else:
        data = {
            "total": 2,
            "missing": 0,
            "buckets": [{"key": "same", "count": 1}, {"key": "same", "count": 1}],
            "truncated": False,
        }
        tool = "aggregate_documents"
    with pytest.raises(ReadPayloadError, match="payload"):
        validate_query_payload(tool, data)


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_nondefault_aggregate_field_uses_its_own_values(
    query_notes: Path, remote: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selected indexed field must replace tags rather than being ignored."""
    from tests.fixtures.cli_discovery import reader_backend

    (query_notes / "a.md").write_text(
        "---\ntags: [wrong]\nlabels: [wanted]\n---\n# A\n\nbodyword\n", encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags,labels")
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [
                "aggregate",
                "--path",
                "a.md",
                "--by",
                "tag",
                "--field",
                "labels",
                *connection,
                "--json",
                "--wait-for-pending-writes",
            ],
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["buckets"] == [{"key": "wanted", "count": 1}]


@pytest.mark.parametrize(
    "command",
    [
        ["search-metadata", "bodyword"],
        ["search-body", "bodyword"],
        ["search-in", "a.md", "bodyword"],
        ["count", "bodyword", "--scope", "body"],
        ["aggregate", "bodyword", "--scope", "body", "--by", "tag"],
    ],
)
def test_keyword_queries_do_not_construct_configured_summarizer(
    query_index: Path, command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed keyword read needs no summarizer even when one is configured."""
    import hashlib

    before = hashlib.sha256(query_index.read_bytes()).digest()
    calls: list[bool] = []

    def unexpected(*_args: object, **_kwargs: object) -> None:
        calls.append(True)
        raise AssertionError("Keyword query constructed a summarizer")

    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "https://vault.example/v1"
    )
    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", unexpected)
    result = CliRunner().invoke(app, [*command, "--local", "--json"])
    assert result.exit_code == 0, (result.output, calls)
    assert calls == []
    assert json.loads(result.stdout)["total"] == (
        1 if command[0] in {"search-metadata", "search-in"} else 2
    )
    assert hashlib.sha256(query_index.read_bytes()).digest() == before


def test_keyword_count_does_not_construct_configured_embedding_provider(
    query_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored keyword index must not activate an explicitly configured embedder."""
    sidecar = query_index.parent / "stored-vectors"
    files = {
        query_index: query_index.read_bytes(),
        sidecar.with_suffix(".npy"): b"not vectors",
        sidecar.with_suffix(".json"): b"not json",
    }
    for path, data in files.items():
        if path != query_index:
            path.write_bytes(data)
    calls: list[bool] = []

    def unexpected(*_args: object, **_kwargs: object) -> None:
        calls.append(True)
        raise AssertionError("Keyword count constructed an embedder")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(sidecar))
    monkeypatch.setattr(
        "markdown_vault_mcp.providers.get_embedding_provider", unexpected
    )
    monkeypatch.setattr(
        "markdown_vault_mcp.summarizer.get_summarizer", lambda *_a, **_kw: None
    )
    result = CliRunner().invoke(
        app, ["count", "bodyword", "--scope", "body", "--local", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert calls == []
    assert json.loads(result.stdout)["total"] == 2
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.usefixtures("query_notes")
def test_query_provider_suppression_preserves_existing_reader_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact-query snapshot must not change the existing read adapter's policy."""
    calls: list[bool] = []

    def observe(_config: object) -> None:
        calls.append(True)

    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "https://vault.example/v1"
    )
    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", observe)
    result = CliRunner().invoke(app, ["read", "a.md", "--local", "--json"])
    assert result.exit_code == 0, result.output
    assert calls == [True]
    assert json.loads(result.stdout)["path"] == "a.md"
