"""Reader commands preserve native payloads and policies at both boundaries."""

from __future__ import annotations

import base64
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
from tests.fixtures.git import _run_git


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("output", ["json", "text", "agent"])
@pytest.mark.parametrize(
    ("arguments", "projection", "expected"),
    [
        (["read", "alpha.md"], "path", "alpha.md"),
        (["list", "--filter", "tags=shared"], "paths", ["alpha.md"]),
        (["folders"], "identity", ["", "notes"]),
        (["tags"], "identity", ["other", "shared"]),
        (["stats"], "document_count", 2),
        (["recent", "--limit", "1"], "paths", ["notes/beta.md"]),
        (["toc", "alpha.md", "--max-level", "1"], "headings", ["Alpha"]),
        (["context", "alpha.md", "--similar-limit", "0"], "path", "alpha.md"),
    ],
)
def test_reader_command_values(
    remote: bool, output: str, arguments: list[str], projection: str, expected: Any
) -> None:
    """Wrong command mapping, predicate, native cap or payload shaping changes values."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [*arguments, *connection, *([] if output == "text" else ["--" + output])],
        )
    assert result.exit_code == 0, result.output
    if output == "text" and arguments[0] == "read":
        assert "# Alpha\n" in result.stdout and "Details here." in result.stdout
        return
    if output == "agent":
        header, *records = result.stdout.splitlines()
        assert header.startswith("OK " + arguments[0] + " ")
        assert "index_stale=" in header
        values = [json.loads(record) for record in records]
        data = values[0] if projection in {"path", "document_count"} else values
    else:
        data = json.loads(result.stdout)
    if projection == "paths":
        observed = [item["path"] for item in data]
    elif projection == "headings":
        observed = [item["heading"] for item in data]
    else:
        observed = data if projection == "identity" else data[projection]
    assert observed == expected


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_similar_unavailable_is_an_error(remote: bool) -> None:
    """Absent stored vectors must not become successful empty similarity results."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["similar", "alpha.md", *connection, "--json"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert "ERROR" in result.stderr


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_note_and_section_payloads(discovery_source: Path, remote: bool) -> None:
    """JSON carries full raw notes while section reads preserve the native empty frontmatter."""
    with reader_backend(remote) as connection:
        full = CliRunner().invoke(app, ["read", "alpha.md", *connection, "--json"])
        section = CliRunner().invoke(
            app, ["read", "alpha.md", "--section", "Detail", *connection, "--json"]
        )
    assert full.exit_code == section.exit_code == 0, (full.output, section.output)
    assert (
        json.loads(full.stdout)["content"]
        == (discovery_source / "alpha.md").read_text()
    )
    data = json.loads(section.stdout)
    assert data["content"].strip() == "Details here."
    assert data["frontmatter"] == {}
    assert "okf" not in data


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("output", ["json", "text", "agent"])
def test_attachment_output_is_not_terminal_bytes(
    discovery_source: Path, remote: bool, output: str
) -> None:
    """Only JSON returns attachment base64; human and agent reads never dump binary content."""
    arguments = [] if output == "text" else ["--" + output]
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, ["read", "sample.png", *connection, *arguments]
        )
    assert result.exit_code == 0, result.output
    encoded = base64.b64encode((discovery_source / "sample.png").read_bytes()).decode()
    if output == "json":
        assert json.loads(result.stdout)["content_base64"] == encoded
    else:
        assert "sample.png" in result.stdout
        assert encoded not in result.stdout
        assert "BINARY" not in result.stdout
        assert "\x1b" not in result.stdout


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("path", ["missing.md", "nope.exe"])
def test_missing_or_disallowed_reads_fail(
    discovery_source: Path, remote: bool, path: str
) -> None:
    """Missing notes and disallowed attachments retain explicit failure outcomes."""
    (discovery_source / "nope.exe").write_bytes(b"not allowed")
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["read", path, *connection, "--json"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""


@given(st.text())
@settings(max_examples=20, deadline=None)
def test_note_json_round_trip_preserves_unicode(body: str) -> None:
    """JSON read output preserves arbitrary note text under native newline normalization."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as monkeypatch:
        source = Path(directory)
        content = "# Generated\n\n" + body
        (source / "note.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(source / "config"))
        result = CliRunner().invoke(
            app, ["read", "note.md", "--source-dir", directory, "--local", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["content"] == content.replace(
            "\r\n", "\n"
        ).replace("\r", "\n")


@pytest.mark.parametrize("selection", ["option", "environment"])
@pytest.mark.parametrize("spelling", ["symlink", "relative"])
@pytest.mark.parametrize(
    "arguments",
    [["read", "note.md"], ["read", "note.md", "--section", "Detail"], ["list"]],
)
def test_local_reader_accepts_equivalent_source_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    spelling: str,
    arguments: list[str],
) -> None:
    """An aliased source root must retain valid note identities for native reads."""
    source = tmp_path / "actual"
    source.mkdir()
    (source / "note.md").write_text("# Note\n\n## Detail\n\nAlias-safe.\n")
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    root = str(alias) if spelling == "symlink" else "actual"
    connection = ["--source-dir", root] if selection == "option" else []
    if selection == "environment":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", root)
    else:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path / "absent"))
    result = CliRunner().invoke(app, [*arguments, *connection, "--local", "--json"])
    assert result.exit_code == 0, (result.output, result.exception)
    data = json.loads(result.stdout)
    if arguments[0] == "list":
        assert [note["path"] for note in data] == ["note.md"]
    else:
        assert data["path"] == "note.md"
        assert "Alias-safe." in data["content"]


@pytest.mark.parametrize("path", ["../outside.md", "linked.md"])
def test_aliased_local_source_still_refuses_escape(tmp_path: Path, path: str) -> None:
    """Canonicalizing the source root must not admit notes outside its tree."""
    source = tmp_path / "actual"
    source.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private fixture content")
    (source / "linked.md").symlink_to(outside)
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    result = CliRunner().invoke(
        app, ["read", path, "--source-dir", str(alias), "--local", "--json"]
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "private fixture content" not in result.output


def test_looped_local_source_retains_configuration_error(tmp_path: Path) -> None:
    """A root symlink loop remains a configuration refusal on every Python lane."""
    source = tmp_path / "loop"
    source.symlink_to("loop", target_is_directory=True)
    result = CliRunner().invoke(
        app, ["read", "note.md", "--source-dir", str(source), "--local", "--json"]
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "does not exist" in result.stderr


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_attachment_cap_boundary(
    discovery_source: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: bool,
    extra_bytes: int,
) -> None:
    """An attachment at the byte cap is readable; one byte above it is refused."""
    (discovery_source / "sample.png").write_bytes(b"x" * (16 + extra_bytes))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", str(16 / 1048576))
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["read", "sample.png", *connection, "--json"])
    assert result.exit_code == (1 if extra_bytes else 0), result.output
    if not extra_bytes:
        assert json.loads(result.stdout)["size_bytes"] == 16
    else:
        assert result.stdout == ""


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_zero_attachment_cap_is_unlimited(
    discovery_source: Path, monkeypatch: pytest.MonkeyPatch, remote: bool
) -> None:
    """The native zero-cap setting must retain its explicit disable-limit meaning."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0")
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["read", "sample.png", *connection, "--json"])
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.stdout)["size_bytes"]
        == (discovery_source / "sample.png").stat().st_size
    )


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_note_cap_keeps_section_escape_hatch(
    monkeypatch: pytest.MonkeyPatch, remote: bool
) -> None:
    """A refused whole-note read can still return its requested section."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "16")
    with reader_backend(remote) as connection:
        full = CliRunner().invoke(app, ["read", "alpha.md", *connection, "--json"])
        section = CliRunner().invoke(
            app, ["read", "alpha.md", "--section", "Detail", *connection, "--json"]
        )
    assert full.exit_code == 1 and full.stdout == "", full.output
    assert section.exit_code == 0, section.output
    assert json.loads(section.stdout)["content"].strip() == "Details here."


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_historical_read_retains_revision_shape(
    discovery_source: Path, remote: bool
) -> None:
    """--revision returns historical content without a live etag or OKF annotation."""
    _run_git(discovery_source, "init")
    _run_git(discovery_source, "config", "user.name", "Synthetic")
    _run_git(discovery_source, "config", "user.email", "synthetic@example.com")
    _run_git(discovery_source, "add", "alpha.md")
    _run_git(discovery_source, "commit", "-m", "test: seed note")
    revision = _run_git(discovery_source, "rev-parse", "HEAD").strip()
    (discovery_source / "alpha.md").write_text("# Changed\n")
    _run_git(discovery_source, "add", "alpha.md")
    _run_git(discovery_source, "commit", "-m", "test: change note")
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, ["read", "alpha.md", "--revision", revision, *connection, "--json"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "Details here." in data["content"]
    assert data["revision"] == revision
    assert "etag" not in data and "okf" not in data


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_okf_read_sources_context_conventions_and_stats(
    discovery_source: Path, remote: bool
) -> None:
    """The CLI preserves native active-bundle enrichment without annotating sections."""
    (discovery_source / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Index\n'
    )
    (discovery_source / "_conventions.md").write_text("Use short titles.\n")
    (discovery_source / "alpha.md").write_text(
        "---\ntitle: Alpha\ntype: Reference\nstatus: draft\nsources:\n  - resource: https://example.com/source\n---\n# Alpha\n\n## Detail\n\nDetails here.\n"
    )
    with reader_backend(remote) as connection:
        runner = CliRunner()
        note = runner.invoke(app, ["read", "alpha.md", *connection, "--json"])
        section = runner.invoke(
            app, ["read", "alpha.md", "--section", "Detail", *connection, "--json"]
        )
        context = runner.invoke(
            app, ["context", "alpha.md", "--similar-limit", "0", *connection, "--json"]
        )
        stats = runner.invoke(app, ["stats", *connection, "--json"])
    assert all(result.exit_code == 0 for result in (note, section, context, stats)), (
        note.output,
        section.output,
        context.output,
        stats.output,
    )
    assert json.loads(note.stdout)["okf"]["sources"] == [
        {"resource": "https://example.com/source"}
    ]
    assert "okf" not in json.loads(section.stdout)
    packet = json.loads(context.stdout)
    assert packet["okf"]["status"] == "draft"
    assert packet["conventions"][0]["content"] == "Use short titles."
    assert "okf" in json.loads(stats.stdout)


@pytest.mark.parametrize(
    "arguments",
    [
        ["read", "alpha.md", "--json", "--agent"],
        ["list", "--filter", "tags=x", "--filter", "tags=y"],
        ["toc", "alpha.md", "--max-notes", "0"],
        ["similar", "alpha.md", "--chunks-per-file", "0"],
        [
            "read",
            "alpha.md",
            "--source-dir",
            "/unused",
            "--server",
            "https://example.com/mcp",
        ],
    ],
)
def test_invalid_controls_fail_before_backend(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad controls cannot open local files or start a network request."""

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("backend evaluated")

    monkeypatch.setattr("markdown_vault_mcp._cli_discovery.local_read", forbidden)
    monkeypatch.setattr("markdown_vault_mcp._cli_remote.call_read", forbidden)
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_toc_subtree_truncation_receipt(discovery_source: Path, remote: bool) -> None:
    """A capped folder outline preserves the native explicit truncation receipt."""
    (discovery_source / "notes/gamma.md").write_text("# Gamma\n")
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, ["toc", "notes", "--max-notes", "1", *connection, "--json"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["truncated"] is True
    assert [note["path"] for note in data["notes"]] == ["notes/beta.md"]


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_frontmatter_dates_are_native_json(
    discovery_source: Path, remote: bool
) -> None:
    """YAML dates and timestamps retain the MCP JSON form through local readers too."""
    (discovery_source / "alpha.md").write_text(
        "---\ntitle: Alpha\npublished: 2026-10-21\nchecked: 2026-10-21T12:00:00Z\n---\n# Alpha\n"
    )
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(app, ["read", "alpha.md", *connection, "--json"])
    assert result.exit_code == 0, result.output
    fields = json.loads(result.stdout)["frontmatter"]
    assert fields["published"] == "2026-10-21"
    assert fields["checked"] == "2026-10-21T12:00:00Z"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": None},
        {"path": "a.png", "size_bytes": "\x1b]0;owned\x07", "content_base64": "eA=="},
    ],
)
def test_malformed_remote_read_fails_before_output(
    payload: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed hosted read never leaks terminal commands or incidental exceptions."""
    from markdown_vault_mcp._cli_remote import RemoteReply

    monkeypatch.setattr(
        "markdown_vault_mcp._cli_remote.call_read", lambda *_args: RemoteReply(payload)
    )
    result = CliRunner().invoke(
        app, ["read", "a.md", "--server", "https://example.com/mcp"]
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ERROR:" in result.stderr
    assert "\x1b" not in result.stderr


@pytest.mark.usefixtures("discovery_source")
def test_local_read_cannot_clone_managed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local reads operate on the existing checkout even when serve uses managed Git."""
    from markdown_vault_mcp.config_sections import _assembly

    original = _assembly._build_git_strategy

    def without_clone(*args: Any, **kwargs: Any) -> Any:
        assert kwargs.get("managed") is False, "read attempted a corpus clone"
        return original(*args, **kwargs)

    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://example.com/synthetic.git"
    )
    monkeypatch.setattr(_assembly, "_build_git_strategy", without_clone)
    result = CliRunner().invoke(app, ["read", "alpha.md", "--local", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["path"] == "alpha.md"


@pytest.mark.usefixtures("discovery_source")
def test_local_read_diagnostic_escapes_untrusted_path(discovery_source: Path) -> None:
    """A native unreadable-note refusal cannot issue terminal commands on stderr."""
    path = "bad\x1b]0;owned\x07.md"
    (discovery_source / path).write_bytes(b"\xff")
    result = CliRunner().invoke(app, ["read", path, "--local"])
    assert result.exit_code == 1
    assert "ERROR:" in result.stderr
    assert "\x1b" not in result.stderr and "\x07" not in result.stderr


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("output", ["json", "text", "agent"])
def test_similar_uses_only_stored_vectors(
    populated_vault: Any, monkeypatch: pytest.MonkeyPatch, remote: bool, output: str
) -> None:
    """Existing synthetic vectors supply actual neighbors without embedding any input."""
    from tests.conftest import MockEmbeddingProvider

    source = populated_vault.source_dir
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(source.parent / "vectors")
    )

    def no_embedding(_self: Any, _texts: Any) -> Any:
        raise AssertionError("reader attempted fresh embedding")

    monkeypatch.setattr(MockEmbeddingProvider, "embed", no_embedding)
    monkeypatch.setattr(
        "markdown_vault_mcp.providers.get_embedding_provider",
        lambda _config: MockEmbeddingProvider(),
    )
    selected_output = [] if output == "text" else ["--" + output]
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [
                "similar",
                "multi.md",
                "--limit",
                "1",
                "--chunks-per-file",
                "1",
                *connection,
                *selected_output,
            ],
        )
        scoped = CliRunner().invoke(
            app,
            [
                "similar",
                "multi.md",
                "--folder",
                "missing",
                *connection,
                *selected_output,
            ],
        )
    assert result.exit_code == scoped.exit_code == 0, (result.output, scoped.output)
    if output == "agent":
        header, *records = result.stdout.splitlines()
        assert header.startswith("OK similar count=1 ")
        data = [json.loads(record) for record in records]
        assert len(scoped.stdout.splitlines()) == 1
        assert scoped.stdout.startswith("OK similar count=0 ")
    else:
        data = json.loads(result.stdout)
        assert json.loads(scoped.stdout) == []
    assert [hit["path"] for hit in data] == ["other.md"]
    assert len(data[0]["sections"]) == 1


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_reader_optional_scopes(remote: bool) -> None:
    """Root/subtree predicates, attachment metadata and native link limits survive dispatch."""
    with reader_backend(remote) as connection:
        runner = CliRunner()

        def read(arguments: list[str]) -> Any:
            result = runner.invoke(app, [*arguments, *connection, "--json"])
            assert result.exit_code == 0, result.output
            return json.loads(result.stdout)

        assert [item["path"] for item in read(["list", "--folder", ""])] == ["alpha.md"]
        assert [item["path"] for item in read(["list", "--pattern", "notes/*.md"])] == [
            "notes/beta.md"
        ]
        assert {item["path"] for item in read(["list", "--include-attachments"])} == {
            "alpha.md",
            "notes/beta.md",
            "sample.png",
        }
        assert [item["path"] for item in read(["recent", "--folder", "notes"])] == [
            "notes/beta.md"
        ]
        assert read(["tags", "--field", "tags"]) == ["other", "shared"]
        context = read(
            ["context", "alpha.md", "--similar-limit", "0", "--link-limit", "0"]
        )
        assert (
            context["backlinks"] == []
            and context["outlinks"] == []
            and context["similar"] == []
        )
        linked = read(
            ["context", "alpha.md", "--similar-limit", "0", "--link-limit", "1"]
        )
        assert [link["source_path"] for link in linked["backlinks"]] == [
            "notes/beta.md"
        ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["read", "alpha.md"],
        ["list"],
        ["folders"],
        ["tags"],
        ["stats"],
        ["recent"],
        ["toc", "alpha.md"],
        ["similar", "alpha.md"],
        ["context", "alpha.md"],
    ],
)
def test_remote_failures_never_construct_local_vault(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote refusals cannot fall back to broken local configuration or providers."""
    from markdown_vault_mcp._cli_client import ClientError

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("remote inspected local configuration")

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise ClientError("Synthetic remote refusal.", 1)

    monkeypatch.setattr("markdown_vault_mcp.config.ProjectConfig.from_env", forbidden)
    monkeypatch.setattr("markdown_vault_mcp._cli_discovery.local_read", forbidden)
    monkeypatch.setattr("markdown_vault_mcp._cli_remote.call_read", refused)
    result = CliRunner().invoke(
        app, [*arguments, "--server", "https://example.com/mcp", "--json"]
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Synthetic remote refusal." in result.stderr


@given(st.dates())
@settings(max_examples=20, deadline=None)
def test_yaml_date_wire_roundtrip(value: Any) -> None:
    """The full Python date domain survives local note JSON as an ISO date string."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as monkeypatch:
        source = Path(directory)
        (source / "note.md").write_text(
            f"---\npublished: {value.isoformat()}\n---\n# Note\n"
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(source / "config"))
        result = CliRunner().invoke(
            app, ["read", "note.md", "--source-dir", directory, "--local", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert (
            json.loads(result.stdout)["frontmatter"]["published"] == value.isoformat()
        )


@given(
    st.one_of(
        st.none(),
        st.booleans(),
        st.floats(),
        st.text(),
        st.lists(st.none()),
        st.integers(max_value=-1),
    ),
    st.sampled_from(["text", "json", "agent"]),
)
@settings(max_examples=30, deadline=None)
def test_attachment_wrong_size_is_always_refused(size: Any, output: str) -> None:
    """Every noninteger/negative attachment size fails before any output mode writes."""
    from markdown_vault_mcp._cli_remote import RemoteReply

    with pytest.MonkeyPatch.context() as monkeypatch:
        data = {
            "path": "sample.png",
            "size_bytes": size,
            "content_base64": "eA==",
            "mime_type": "image/png",
        }
        monkeypatch.setattr(
            "markdown_vault_mcp._cli_remote.call_read", lambda *_args: RemoteReply(data)
        )
        result = CliRunner().invoke(
            app,
            [
                "read",
                "sample.png",
                "--server",
                "https://example.com/mcp",
                *([] if output == "text" else ["--" + output]),
            ],
        )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "ERROR: Server returned an invalid read payload." in result.stderr


@given(st.text())
@settings(max_examples=30, deadline=None)
def test_native_diagnostic_is_terminal_safe(path: str) -> None:
    """All Unicode path values retain the failure meaning without emitting controls."""
    import unicodedata

    from markdown_vault_mcp.exceptions import DocumentUnreadableError

    def unreadable(*_args: Any) -> Any:
        raise DocumentUnreadableError(path, "invalid UTF8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("markdown_vault_mcp._cli_discovery.local_read", unreadable)
        result = CliRunner().invoke(app, ["read", "note.md", "--local"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "invalid UTF8" in result.stderr
    assert all(
        char == "\n"
        or (
            not unicodedata.category(char).startswith("C")
            and char not in "\u2028\u2029"
        )
        for char in result.stderr
    )


def test_disk_readers_preserve_index_and_state(
    stored_reader_index: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All reader commands leave a configured shared index and state file untouched."""
    index = stored_reader_index
    state = tmp_path / "shared-state.json"
    state.write_text('{"synthetic": "unchanged"}')
    before = index.read_bytes(), state.read_bytes()
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    commands = [
        ["read", "alpha.md"],
        ["list"],
        ["folders"],
        ["tags"],
        ["stats"],
        ["recent"],
        ["toc", "alpha.md"],
        ["context", "alpha.md", "--similar-limit", "0"],
    ]
    for arguments in commands:
        result = CliRunner().invoke(app, [*arguments, "--local", "--json"])
        assert result.exit_code == 0, result.output
    unavailable = CliRunner().invoke(app, ["similar", "alpha.md", "--local", "--json"])
    assert unavailable.exit_code == 1
    assert (index.read_bytes(), state.read_bytes()) == before


@pytest.mark.usefixtures("discovery_source")
def test_failed_local_construction_closes_collaborators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store-construction fault closes collaborators before propagating as a fault."""
    from markdown_vault_mcp.config_sections._assembly import VaultInstances

    closed: list[VaultInstances] = []
    original = VaultInstances.close

    def close(instances: VaultInstances) -> None:
        closed.append(instances)
        original(instances)

    def failed(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Synthetic constructor fault")

    monkeypatch.setattr(VaultInstances, "close", close)
    monkeypatch.setattr("markdown_vault_mcp.vault.Vault.__init__", failed)
    result = CliRunner().invoke(app, ["read", "alpha.md", "--local"])
    assert isinstance(result.exception, RuntimeError)
    assert len(closed) == 1


@pytest.mark.parametrize("busy", [False, True], ids=["incomplete", "active-writer"])
def test_disk_readers_refuse_unready_index(
    stored_reader_index: Path, monkeypatch: pytest.MonkeyPatch, busy: bool
) -> None:
    """Partial indexes and active local writers fail explicitly without a build or write."""
    from markdown_vault_mcp.fts_index import FTSIndex

    if busy:
        monkeypatch.setattr(
            "markdown_vault_mcp.indexing.IndexWriteCoordinator.is_drained",
            lambda _self: False,
        )
    else:
        index = FTSIndex(stored_reader_index)
        try:
            index.clear_build_completed()
        finally:
            index.close()
    before = stored_reader_index.read_bytes()
    result = CliRunner().invoke(app, ["toc", "alpha.md", "--local", "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert ("writer is active" if busy else "no completed build") in result.stderr
    assert stored_reader_index.read_bytes() == before


@pytest.mark.parametrize("stored_reader_index", [True], indirect=True)
def test_complete_empty_index_is_readable(stored_reader_index: Path) -> None:
    """A completed empty population is successful, without a cold rebuild."""
    before = stored_reader_index.read_bytes()
    runner = CliRunner()
    for command in ["list", "recent"]:
        result = runner.invoke(app, [command, "--local", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == []
    toc = runner.invoke(app, ["toc", "missing-folder", "--local", "--json"])
    assert toc.exit_code == 0, toc.output
    assert json.loads(toc.stdout) == {
        "path": "missing-folder",
        "notes": [],
        "truncated": False,
    }
    assert stored_reader_index.read_bytes() == before


@pytest.mark.usefixtures("discovery_source")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
def test_reader_wait_flag_keeps_results_and_observed_freshness(remote: bool) -> None:
    """Native drain requests preserve results; local reads still report unknown freshness."""
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app, ["list", "--wait-for-pending-writes", *connection, "--agent"]
        )
    assert result.exit_code == 0, result.output
    header, *records = result.stdout.splitlines()
    assert {json.loads(record)["path"] for record in records} == {
        "alpha.md",
        "notes/beta.md",
    }
    assert ("index_stale=false" if remote else "index_stale=unknown") in header


def test_local_read_missing_source_is_explicit(tmp_path: Path) -> None:
    """A missing local checkout fails without creating or cloning it."""
    missing = tmp_path / "missing-source"
    result = CliRunner().invoke(
        app, ["read", "alpha.md", "--source-dir", str(missing), "--local", "--json"]
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert "ERROR:" in result.stderr and "does not exist" in result.stderr
    assert not missing.exists()


def test_reader_interrupt_is_exit_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interrupting the selected backend produces no partial read payload."""

    def interrupted(*_args: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr("markdown_vault_mcp._cli_discovery.local_read", interrupted)
    result = CliRunner().invoke(app, ["read", "alpha.md", "--local", "--json"])
    assert result.exit_code == 130 and result.stdout == ""


@pytest.mark.usefixtures("discovery_source")
def test_context_omits_refused_optional_conventions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native optional convention lookup refusal leaves the usable dossier intact."""

    def refused(*_args: Any) -> Any:
        raise ValueError("Synthetic optional lookup refusal")

    monkeypatch.setattr(
        "markdown_vault_mcp.conventions.ConventionsResolver.for_path", refused
    )
    result = CliRunner().invoke(
        app, ["context", "alpha.md", "--similar-limit", "0", "--local", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["path"] == "alpha.md" and data["frontmatter"]["tags"] == ["shared"]
    assert "conventions" not in data


@pytest.mark.parametrize("remote", [False, True], ids=["local", "http"])
@pytest.mark.parametrize("output", ["json", "text", "agent"])
def test_unserializable_yaml_has_safe_read_failure(
    discovery_source: Path, remote: bool, output: str
) -> None:
    """Legal YAML with non-UTF8 binary metadata fails at the wire boundary with a diagnostic."""
    (discovery_source / "alpha.md").write_text(
        "---\npayload: !!binary /w==\n---\n# Alpha\n"
    )
    with reader_backend(remote) as connection:
        result = CliRunner().invoke(
            app,
            [
                "read",
                "alpha.md",
                *connection,
                *([] if output == "text" else ["--" + output]),
            ],
        )
    assert result.exit_code == 1 and result.stdout == ""
    assert "ERROR:" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.usefixtures("discovery_source")
def test_unknown_wire_value_is_serialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported Python metadata receives a safe server-fault diagnostic."""
    monkeypatch.setattr(
        "markdown_vault_mcp._cli_read._reader_payload",
        lambda *_args: {
            "path": "alpha.md",
            "content": "# Alpha",
            "frontmatter": {"unsupported": object()},
        },
    )
    result = CliRunner().invoke(app, ["read", "alpha.md", "--local", "--json"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "ERROR: Read payload is not JSON-compatible." in result.stderr


@pytest.mark.usefixtures("discovery_source")
def test_reader_backend_valueerror_is_not_input_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the serializer's known failures are wrapped; unrelated backend faults propagate."""

    def failed(*_args: Any) -> Any:
        raise ValueError("Synthetic stored-data fault")

    monkeypatch.setattr("markdown_vault_mcp._cli_read._reader_payload", failed)
    result = CliRunner().invoke(app, ["read", "alpha.md", "--local", "--json"])
    assert type(result.exception) is ValueError
    assert str(result.exception) == "Synthetic stored-data fault"
    assert result.stdout == ""
