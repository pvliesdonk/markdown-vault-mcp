"""Explicit OKF authoring preserves unrelated note data."""

from pathlib import Path

import frontmatter
import pytest

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    InvalidRequestError,
)
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_author_preserves_evidence_and_records_explicit_producer(
    tmp_path: Path,
) -> None:
    original = (
        "---\n# keep comment\ntype: Old\nsources:\n  - resource: /source.md\n"
        "verified:\n  - by: human:reviewer\n    at: 2026-01-01\n"
        "x-custom: {keep: true}\n---\n# Body\n"
    )
    (tmp_path / "note.md").write_text(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"type": "New"}, producer="codex/6.1"
        )
        result = (tmp_path / "note.md").read_text()
        meta = frontmatter.loads(result).metadata
        assert "# keep comment" in result
        assert "sources:\n  - resource: /source.md\n" in result
        assert "verified:\n  - by: human:reviewer\n    at: 2026-01-01\n" in result
        assert "x-custom: {keep: true}" in result
        assert result.endswith("# Body\n")
        assert meta["generated"]["by"] == "codex/6.1"
        assert meta["generated"]["at"].endswith("Z")
    finally:
        vault.close()


def test_author_requires_matching_etag_and_valid_producer(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("# Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author("note.md", if_match="", fields={"title": "T"})
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_author("note.md", if_match="stale", fields={"type": "new"})
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"title": "Title"}
        )
        assert (
            "generated"
            not in frontmatter.loads((tmp_path / "note.md").read_text()).metadata
        )
    finally:
        vault.close()


@pytest.mark.anyio
@pytest.mark.parametrize("configuration_error", [False, True])
async def test_mcp_author_discovery_when_vault_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configuration_error: bool
) -> None:
    from fastmcp import Client

    from markdown_vault_mcp.exceptions import ConfigurationError
    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:

        def unavailable(_context: object) -> None:
            error = ConfigurationError if configuration_error else RuntimeError
            raise error("Vault unavailable")

        monkeypatch.setattr("markdown_vault_mcp.domain.get_vault", unavailable)
        names = {tool.name for tool in await client.list_tools()}
        assert {"read", "okf_author"} <= names


@pytest.mark.anyio
@pytest.mark.parametrize("initially_active", [False, True])
@pytest.mark.parametrize(
    ("configured", "read_only"), [(True, False), (False, False), (True, True)]
)
async def test_mcp_author_discovery_tracks_effective_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initially_active: bool,
    configured: bool,
    read_only: bool,
) -> None:
    from fastmcp import Client

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "auto")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", str(configured).lower())
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", str(read_only).lower())
    declaration = tmp_path / "index.md"
    async with Client(make_server()) as client:
        for active in (initially_active, not initially_active, initially_active):
            if active:
                declaration.write_text('---\nokf_version: "0.2"\n---\n')
            else:
                declaration.unlink(missing_ok=True)
            names = {tool.name for tool in await client.list_tools()}
            assert ("okf_author" in names) == (active and configured and not read_only)


@pytest.mark.anyio
async def test_mcp_author_visibility_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client

    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text("# Body\n")
    (tmp_path / "other.md").write_text("# Other\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setattr(
        "markdown_vault_mcp._subject.get_subject", lambda: "secret-subject"
    )
    monkeypatch.setattr(
        "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "secret-subject"}
    )
    async with Client(make_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "okf_author" in tools
        assert "if_match" in tools["okf_author"].input_schema["required"]
        note = await client.call_tool("read", {"path": "note.md"})
        from tests.conftest import _parse_tool_data

        etag = _parse_tool_data(note)["etag"]
        other = _parse_tool_data(await client.call_tool("read", {"path": "other.md"}))
        await client.call_tool(
            "okf_author",
            {
                "path": "other.md",
                "if_match": other["etag"],
                "fields": {"title": "Other"},
            },
        )
        result = await client.call_tool(
            "okf_author",
            {
                "path": "note.md",
                "if_match": etag,
                "fields": {"type": "Concept"},
                "producer": "codex/6.1",
            },
        )
        payload = _parse_tool_data(result)
        assert payload["path"] == "note.md"
        assert "previous_revision" in payload
        assert payload["unresolved_links"] is None
    text = (tmp_path / "note.md").read_text()
    assert "secret-subject" not in text
    assert frontmatter.loads(text).metadata["generated"]["by"] == "codex/6.1"
    other_text = (tmp_path / "other.md").read_text()
    assert "secret-subject" not in other_text
    assert "generated" not in frontmatter.loads(other_text).metadata
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]


@pytest.mark.parametrize(
    ("text", "fields", "expected", "retained", "absent"),
    [
        (
            "---\nsources:\n  # context\n  - resource: old # old source\n# next field comment\ncustom: yes\n---\nBody\n",
            {"sources": [{"resource": "new"}]},
            {"sources": [{"resource": "new"}], "custom": True},
            ("# context", "# old source", "# next field comment"),
            (),
        ),
        (
            "---\ntags:\n- one\n- two\nother: keep\n---\nBody\n",
            {"tags": ["new"]},
            {"tags": ["new"], "other": "keep"},
            ("other: keep\n---\nBody\n",),
            (),
        ),
        (
            "---\nsources:\n- resource: old\nother: keep\n---\nBody\n",
            {"sources": [{"resource": "new", "extra": 1}]},
            {"sources": [{"resource": "new", "extra": 1}], "other": "keep"},
            ("other: keep\n---\nBody\n",),
            (),
        ),
        (
            "---\nsources: [legacy]\n---\nBody",
            {"stale_after": "2027-03-06"},
            {"sources": ["legacy"], "stale_after": "2027-03-06"},
            ("sources: [legacy]",),
            (),
        ),
        (
            "---\ndescription: >2- # editorial context\n  original # data\n  # scalar data\nverified: [{by: human:bob}]\n---\nBody\n",
            {"description": "New"},
            {"description": "New", "verified": [{"by": "human:bob"}]},
            ("# editorial context", "verified: [{by: human:bob}]\n---\nBody\n"),
            ("# scalar data",),
        ),
    ],
)
def test_selected_entry_edits(
    text: str,
    fields: dict,
    expected: dict,
    retained: tuple[str, ...],
    absent: tuple[str, ...],
) -> None:
    from markdown_vault_mcp._okf_author import author_text

    result = author_text(text, fields, None)
    assert frontmatter.loads(result).metadata == expected
    assert all(fragment in result for fragment in retained)
    assert all(fragment not in result for fragment in absent)


@pytest.mark.parametrize("producer", ["team:finance-fpa", "custom:opaque"])
def test_author_accepts_producer_actor_forms(producer: str) -> None:
    from markdown_vault_mcp._okf_author import author_text

    result = author_text("---\ngenerated: {by: old/1}\n---\nBody\n", {}, producer)
    assert frontmatter.loads(result).metadata["generated"]["by"] == producer


@pytest.mark.parametrize(
    "producer",
    [
        "",
        "human:",
        ":id",
        "/1",
        "agent/",
        "bad",
        " a/1",
        "a\nb/1",
        "human:a\nb",
        "team:bad id",
    ],
)
def test_invalid_actor_refused_without_write(tmp_path: Path, producer: str) -> None:
    (tmp_path / "note.md").write_text("# Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={}, producer=producer
            )
        assert (tmp_path / "note.md").read_text() == "# Body\n"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("text", "fields", "producer"),
    [
        ("---\ntype: a\ntype: b\n---\nBody\n", {"title": "new"}, "agent/1"),
        (
            "---\nshared: &shared value\ntitle: *shared\n---\nBody\n",
            {"title": "new"},
            "agent/1",
        ),
        ("---\ngenerated: invalid\n---\nBody\n", {"title": "new"}, "agent/1"),
        ("---\nbad: [\n---\nBody\n", {"type": "New"}, None),
        ("---\n[one, two]\n---\nBody\n", {"type": "New"}, None),
        ("---\nunterminated\n", {"type": "New"}, None),
        ("+++\ntitle = 'Old'\n+++\nBody", {"type": "New"}, None),
        ('{\n"title": "Old"\n}\nBody', {"type": "New"}, None),
        ("---\ntitle: &v value\nx-copy: *v\n---\nBody\n", {"title": "new"}, None),
        ("---\nx: &v {by: old/1}\ngenerated: *v\n---\nBody\n", {}, "new/1"),
        ("---\n<<: {generated: []}\ntype: Guide\n---\nBody\n", {}, "human:alice"),
    ],
)
def test_unsafe_frontmatter_refused(
    text: str, fields: dict, producer: str | None
) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError):
        author_text(text, fields, producer)


@pytest.mark.parametrize(
    ("text", "fields", "unchanged", "comments"),
    [
        (
            "---\ngenerated: {by: old/1, custom: keep}\n---\nBody\n",
            {},
            "custom: keep",
            (),
        ),
        (
            "---\nx: &shared keep\ngenerated: {by: old/1, custom: *shared # keep\n}\n---\nBody\n",
            {},
            "custom: *shared # keep\n",
            (),
        ),
        (
            "---\ngenerated:\n  by: old/1 # attribution\n  custom: retained\nsources:\n  - resource: old\nnext: untouched\n---\nBody\n",
            {"sources": [{"resource": "new"}]},
            "  custom: retained\n",
            ("# attribution", "next: untouched"),
        ),
        (
            "---\ngenerated:\n  by:\n    - old/1 # earlier\n  custom: keep\n---\nBody\n",
            {},
            "  custom: keep\n",
            ("# earlier",),
        ),
        ("---\ngenerated:\n  by:\n---\nBody\n", {}, "---\nBody\n", ()),
        (
            "---\ngenerated:\nverified: [{by: human:a}]\n---\nBody\n",
            {},
            "verified: [{by: human:a}]",
            (),
        ),
        (
            "---\nshared: &x value\ncopy: *x\ngenerated:\n  by: old/1 # attribution\n  extension: &e [a, b] # untouched\n---\nBody\n",
            {"type": "Guide"},
            "shared: &x value\ncopy: *x\n",
            ("# attribution", "  extension: &e [a, b] # untouched\n"),
        ),
        (
            "---\ngenerated:\n  by: > # attribution\n    old/1 # data\n  extension: keep # retained\n---\nBody\n",
            {},
            "  extension: keep # retained\n",
            ("# attribution",),
        ),
        (
            "---\ntype: Concept\ngenerated: {by: old/1, # keep\n  custom: retained}\n---\nBody\n",
            {},
            "custom: retained",
            ("# keep",),
        ),
        (
            "---\ngenerated:\n  by:\n  custom: keep\n---\nBody\n",
            {},
            "  custom: keep\n",
            (),
        ),
    ],
)
def test_generated_child_preservation(
    text: str, fields: dict, unchanged: str, comments: tuple[str, ...]
) -> None:
    from markdown_vault_mcp._okf_author import author_text

    result = author_text(text, fields, "new/1")
    metadata = frontmatter.loads(result).metadata
    assert metadata["generated"]["by"] == "new/1"
    assert metadata["generated"]["at"].endswith("Z")
    for key, value in fields.items():
        assert metadata[key] == value
    assert unchanged in result
    assert all(comment in result for comment in comments)
    assert "# data" not in result
    assert result.endswith("---\nBody\n")


@pytest.mark.anyio
async def test_full_registry_author_metadata() -> None:
    from fastmcp import FastMCP

    from markdown_vault_mcp.tools import register_tools

    mcp = FastMCP("registry")
    register_tools(mcp)
    tool = next(t for t in await mcp._list_tools() if t.name == "okf_author")
    assert {"write", "okf", "okf-author"} <= tool.tags
    assert tool.annotations and tool.annotations.title
    assert tool.icons
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False


@pytest.mark.anyio
async def test_inactive_vault_refuses_authoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "note.md").write_text("# Body\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:
        etag = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))[
            "etag"
        ]
        with pytest.raises(ToolError):
            await client.call_tool(
                "okf_author",
                {"path": "note.md", "if_match": etag, "fields": {"type": "Concept"}},
            )
    assert (tmp_path / "note.md").read_text() == "# Body\n"


def test_empty_frontmatter_and_unsupported_field() -> None:
    from markdown_vault_mcp._okf_author import author_text

    assert (
        frontmatter.loads(
            author_text("---\n---\nBody\n", {"type": "Guide"}, None)
        ).metadata["type"]
        == "Guide"
    )


def test_reserved_and_missing_notes_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import DocumentNotFoundError

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(DocumentNotFoundError):
            vault.writer.okf_author("missing.md", if_match="etag", fields={})
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("text", "fields", "expected"),
    [
        (
            "---\ntype:\nverified: [{by: human:alice}]\n---\nBody\n",
            {"type": "Concept"},
            "---\ntype: Concept\nverified: [{by: human:alice}]\n---\nBody\n",
        ),
        (
            "---\ntype: Concept\n...\n---\nBody\n",
            {"title": "Hello"},
            "---\ntype: Concept\ntitle: Hello\n...\n---\nBody\n",
        ),
        (
            "---\r\ntype: Concept\r\nverified: [{by: human:alice}]\r\n---\r\nBody\r\n",
            {"title": "Hello"},
            "---\r\ntype: Concept\r\nverified: [{by: human:alice}]\r\ntitle: Hello\r\n---\r\nBody\r\n",
        ),
        (
            "---\nx-template: &custom retained\nx-copy: *custom\n---\nBody\n",
            {"type": "Guide"},
            "---\nx-template: &custom retained\nx-copy: *custom\ntype: Guide\n---\nBody\n",
        ),
        (
            "---\ntype: Guide\n---\nline1\r\nline2\r\n",
            {"title": "New"},
            "---\ntype: Guide\ntitle: New\n---\nline1\r\nline2\r\n",
        ),
    ],
)
def test_author_preserves_untouched_bytes(
    text: str, fields: dict, expected: str
) -> None:
    from markdown_vault_mcp._okf_author import author_text

    assert author_text(text, fields, None) == expected


def test_author_refuses_resolved_reserved_alias(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Navigation\n")
    (tmp_path / "alias.md").symlink_to("index.md")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("alias.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "alias.md", if_match=note.etag, fields={"type": "Guide"}
            )
        assert (tmp_path / "index.md").read_text() == "# Navigation\n"
    finally:
        vault.close()


def test_author_updates_index_and_log(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig

    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("note.md", "---\ntitle: Before\n---\nBody\n")
        old_log = (tmp_path / "log.md").read_text()
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"title": "After"}
        )
        assert "After" in (tmp_path / "index.md").read_text()
        assert (tmp_path / "log.md").read_text() != old_log
    finally:
        vault.close()


@pytest.mark.parametrize("rendered", ["wrong", "["])
def test_splice_validation_refuses_invalid_render(
    monkeypatch: pytest.MonkeyPatch, rendered: str
) -> None:
    from markdown_vault_mcp import _okf_author

    monkeypatch.setattr(_okf_author, "_render", lambda _value: rendered)
    with pytest.raises(InvalidRequestError, match="safely"):
        _okf_author.author_text(
            "---\ntype: old\nverified: [{by: human:a}]\n---\nBody\n",
            {"type": "new"},
            None,
        )


def test_author_snapshot_read_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.exceptions import DocumentUnreadableError

    (tmp_path / "note.md").write_text("# Body\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        monkeypatch.setattr(vault.writer._doc_mgr, "read", lambda _path: note)

        def fail_read(_path: Path) -> bytes:
            raise OSError("fixture read failure")

        monkeypatch.setattr(Path, "read_bytes", fail_read)
        with pytest.raises(DocumentUnreadableError):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={"title": "New"}
            )
    finally:
        vault.close()


@pytest.mark.parametrize("delimiter", ["----", "--- "])
def test_replan_reader_delimiters_preserve_metadata(delimiter: str) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    text = f"\n{delimiter}\ntype: Old\nverified: [{{by: human:a}}]\nsources: [{{resource: x}}]\n{delimiter}\nBody\n"
    result = author_text(text, {"type": "New"}, None)
    assert result.startswith(f"\n{delimiter}\n")
    assert parse_frontmatter(result).metadata == {
        "type": "New",
        "verified": [{"by": "human:a"}],
        "sources": [{"resource": "x"}],
    }
    assert result.endswith(f"{delimiter}\nBody\n")


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"sources": ["bad"]},
        {"sources": [{}]},
        {"sources": [{"resource": ""}]},
        {"tags": [1]},
        {"status": "bogus"},
        {"stale_after": "soon"},
        {"title": None},
        {"verified": []},
    ],
)
def test_replan_invalid_values(fields: dict) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError):
        author_text("---\ntype: Existing\n---\nBody", fields, None)


@pytest.mark.parametrize(
    "original", ["\ufeff# Body\n\ufeffinside\n", "\ufeff---\ntype: Old\n---\nBody\n"]
)
def test_bom_stays_at_file_start_and_reader_body_is_unchanged(original: str) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text(original, {"title": "New"}, None)
    assert result.startswith("\ufeff---\n")
    before = parse_frontmatter(original.removeprefix("\ufeff"))
    after = parse_frontmatter(result.removeprefix("\ufeff"))
    assert after.content == before.content
    assert after.metadata == {**before.metadata, "title": "New"}


@pytest.mark.parametrize(
    "value", ["2027-03-06", "2027-03-06T12:30:00Z", "2027-03-06T12:30:00+01:00"]
)
def test_replan_supported_stale_after_values(value: str) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text("---\ntype: Guide\n---\nBody", {"stale_after": value}, None)
    assert parse_frontmatter(result).metadata["stale_after"] == value


@pytest.mark.parametrize(
    ("text", "fields", "producer", "expected_type", "expected_by"),
    [
        (
            "---\n{type: Old, sources: [{resource: /source.md}], # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}, generated: {by: old/1, extension: yes}}\n---\nBody\n",
            {"title": "New"},
            None,
            "Old",
            "old/1",
        ),
        (
            "---\n{type: Old, sources: [{resource: /source.md}], # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}, generated: {by: old/1, extension: yes}}\n---\nBody\n",
            {"type": "New"},
            None,
            "New",
            "old/1",
        ),
        (
            "---\n{type: Old, sources: [{resource: /source.md}], # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}, generated: {by: old/1, extension: yes}}\n---\nBody\n",
            {},
            "human:alice",
            "Old",
            "human:alice",
        ),
        (
            "---\n{type: Old, sources: [{resource: /source.md}], # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}, generated: {by: old/1, extension: yes}}\n---\nBody\n",
            {"title": "New"},
            "human:alice",
            "Old",
            "human:alice",
        ),
        ("---\n{}\n---\nBody\n", {}, "process:job", None, "process:job"),
        ("---\n{type: Guide,}\n---\nBody\n", {}, "process:job", "Guide", "process:job"),
    ],
)
def test_flow_root_authoring(
    text: str,
    fields: dict,
    producer: str | None,
    expected_type: str | None,
    expected_by: str,
) -> None:
    from markdown_vault_mcp._okf_author import author_text

    result = author_text(text, fields, producer)
    metadata = frontmatter.loads(result).metadata
    assert metadata.get("type") == expected_type
    assert metadata["generated"]["by"] == expected_by
    for key, value in fields.items():
        assert metadata[key] == value
    if "# evidence" in text:
        assert (
            "sources: [{resource: /source.md}], # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}"
            in result
        )
        assert "extension: yes" in result
    assert result.endswith("---\nBody\n")


@pytest.mark.parametrize(
    ("header_newline", "body"),
    [
        (b"\n", b"# Body\nsecond\n"),
        (b"\r\n", b"# Body\r\nsecond\r\n"),
        (b"\n", b"# Body\r\nsecond\nthird\r\n"),
    ],
    ids=["lf", "crlf", "mixed"],
)
def test_author_preserves_bytes_with_windows_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    header_newline: bytes,
    body: bytes,
) -> None:
    from typing import Any

    from markdown_vault_mcp.managers import _write_kernel

    nl = header_newline
    evidence = (
        b"sources: [{resource: /source.md}]"
        + nl
        + b"verified: [{by: human:alice}] # review"
        + nl
    )
    original = b"---" + nl + b"type: Old" + nl + evidence + b"---" + nl + body
    path = tmp_path / "note.md"
    path.write_bytes(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    real_tempfile = _write_kernel.tempfile.NamedTemporaryFile

    def windows_tempfile(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("mode") == "w":
            kwargs["newline"] = "\r\n"
        return real_tempfile(*args, **kwargs)

    try:
        before = vault.reader.read("note.md")
        assert before is not None
        monkeypatch.setattr(
            _write_kernel.tempfile, "NamedTemporaryFile", windows_tempfile
        )
        vault.writer.okf_author("note.md", if_match=before.etag, fields={"type": "New"})
        result = path.read_bytes()
        assert result == original.replace(b"type: Old", b"type: New")
        assert evidence in result
        assert result.endswith(body)
    finally:
        vault.close()


def test_author_failed_staging_preserves_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from markdown_vault_mcp.managers import _write_kernel

    path = tmp_path / "note.md"
    original = b"---\r\ntype: Old\r\nverified: [{by: human:alice}]\r\n---\r\nBody\r\n"
    path.write_bytes(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))

    def fail_staging(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("staging failed")

    try:
        before = vault.reader.read("note.md")
        assert before is not None
        monkeypatch.setattr(_write_kernel.tempfile, "NamedTemporaryFile", fail_staging)
        with pytest.raises(OSError, match="staging failed"):
            vault.writer.okf_author(
                "note.md", if_match=before.etag, fields={"type": "New"}
            )
        assert path.read_bytes() == original
    finally:
        vault.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (b"---\ntype: Old\ntype: New\n---\nBody\n", "duplicate keys"),
        (b"---\nshared: &g {by: old/1}\ngenerated: *g\n---\nBody\n", "aliased field"),
        (b"---\ngenerated: incorrect\n---\nBody\n", "must be a mapping"),
    ],
    ids=["duplicate-keys", "aliased-generated", "nonmapping-generated"],
)
async def test_mcp_author_specific_input_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: bytes, expected: str
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    path = tmp_path / "note.md"
    path.write_bytes(original)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:
        etag = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))[
            "etag"
        ]
        with pytest.raises(ToolError) as error:
            await client.call_tool(
                "okf_author",
                {
                    "path": "note.md",
                    "if_match": etag,
                    "fields": {},
                    "producer": "codex/6.1",
                },
            )
        assert path.read_bytes() == original
        assert expected in str(error.value)


@pytest.mark.anyio
async def test_mcp_author_malformed_existing_yaml_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    path = tmp_path / "note.md"
    path.write_bytes(b"---\ntype: Guide\n---\nBody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:
        etag = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))[
            "etag"
        ]
        malformed = b"---\ntype: [\n---\nBody\n"
        path.write_bytes(malformed)
        with pytest.raises(ToolError) as error:
            await client.call_tool(
                "okf_author",
                {"path": "note.md", "if_match": etag, "fields": {"title": "New"}},
            )
        assert path.read_bytes() == malformed
        message = str(error.value).lower()
        assert "note.md" in message
        assert "frontmatter" in message
        assert "retry later" not in message
