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
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={}, producer="invalid"
            )
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author("note.md", if_match="", fields={})
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


def test_existing_generated_extensions_and_multiline_sources_survive(
    tmp_path: Path,
) -> None:
    (tmp_path / "note.md").write_text(
        "---\ngenerated:\n  by: old/1 # attribution\n  custom: retained\n"
        "sources:\n  - resource: old\nnext: untouched\n---\nBody\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md",
            if_match=note.etag,
            fields={"sources": [{"resource": "new"}]},
            producer="new/1",
        )
        text = (tmp_path / "note.md").read_text()
        meta = frontmatter.loads(text).metadata
        assert meta["generated"]["custom"] == "retained"
        assert meta["generated"]["by"] == "new/1"
        assert "# attribution" in text
        assert meta["next"] == "untouched"
        assert meta["sources"] == [{"resource": "new"}]
    finally:
        vault.close()


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
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "secret-subject")
    monkeypatch.setattr(
        "fastmcp_pvl_core.get_claims", lambda: {"sub": "secret-subject"}
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
        await client.call_tool(
            "okf_author",
            {
                "path": "note.md",
                "if_match": etag,
                "fields": {"type": "Concept"},
                "producer": "codex/6.1",
            },
        )
    text = (tmp_path / "note.md").read_text()
    assert "secret-subject" not in text
    assert frontmatter.loads(text).metadata["generated"]["by"] == "codex/6.1"
    other_text = (tmp_path / "other.md").read_text()
    assert "secret-subject" not in other_text
    assert "generated" not in frontmatter.loads(other_text).metadata
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        assert "okf_author" not in [tool.name for tool in await client.list_tools()]


def test_author_keeps_comments_inside_replaced_block(tmp_path: Path) -> None:
    original = (
        "---\nsources:\n  # context\n  - resource: old # old source\n"
        "# next field comment\ncustom: yes\n---\nBody\n"
    )
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields={"sources": [{"resource": "new"}]}
        )
        text = (tmp_path / "note.md").read_text()
        for comment in ("# context", "# old source", "# next field comment"):
            assert comment in text
        assert frontmatter.loads(text).metadata["sources"] == [{"resource": "new"}]
    finally:
        vault.close()


@pytest.mark.parametrize(
    "producer",
    ["", "human:", "process:", "/1", "agent/", "bad", " a/1", "a\nb/1", "human:a\nb"],
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
    "text",
    [
        "---\ntype: a\ntype: b\n---\nBody\n",
        "---\nshared: &shared value\ntitle: *shared\n---\nBody\n",
        "---\ngenerated: invalid\n---\nBody\n",
    ],
)
def test_ambiguous_frontmatter_is_not_rewritten(tmp_path: Path, text: str) -> None:
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md",
                if_match=note.etag,
                fields={"title": "new"},
                producer="agent/1",
            )
        assert (tmp_path / "note.md").read_text() == text
    finally:
        vault.close()


def test_flow_generation_extensions_preserved() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\ngenerated: {by: old/1, custom: keep}\n---\nBody\n"
    result = author_text(text, {}, "process:job")
    assert frontmatter.loads(result).metadata["generated"]["custom"] == "keep"
    assert result.endswith("Body\n")


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
    assert "if_match" in tool.parameters["required"]


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


@pytest.mark.parametrize(
    "text",
    [
        "---\nbad: [\n---\nBody\n",
        "---\n[one, two]\n---\nBody\n",
        "---\nunterminated\n",
    ],
)
def test_unsupported_frontmatter_refused(text: str) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError):
        author_text(text, {"type": "New"}, None)


def test_empty_frontmatter_and_unsupported_field() -> None:
    from markdown_vault_mcp._okf_author import author_text

    assert (
        frontmatter.loads(
            author_text("---\n---\nBody\n", {"type": "Guide"}, None)
        ).metadata["type"]
        == "Guide"
    )
    with pytest.raises(InvalidRequestError):
        author_text("# Body\n", {"verified": []}, None)


def test_reserved_and_missing_notes_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import DocumentNotFoundError

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author("index.md", if_match="etag", fields={})
        with pytest.raises(DocumentNotFoundError):
            vault.writer.okf_author("missing.md", if_match="etag", fields={})
    finally:
        vault.close()


def test_generated_block_value_keeps_comment_and_neighbor() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\ngenerated:\n  by:\n    - old/1 # earlier\n  custom: keep\n---\nBody\n"
    result = author_text(text, {}, "new/1")
    assert "# earlier" in result
    assert frontmatter.loads(result).metadata["generated"]["custom"] == "keep"


@pytest.mark.parametrize(
    ("text", "fields", "producer"),
    [
        (
            "---\ntype:\nverified: [{by: human:alice}]\n---\nBody\n",
            {"type": "Concept"},
            None,
        ),
        ("---\ntype: Concept\n...\n---\nBody\n", {"title": "Hello"}, None),
        (
            "---\ntype: Concept\ngenerated: {by: old/1, # keep\n  custom: retained}\n---\nBody\n",
            {},
            "new/1",
        ),
        (
            "---\r\ntype: Concept\r\nverified: [{by: human:alice}]\r\n---\r\nBody\r\n",
            {"title": "Hello"},
            None,
        ),
        (
            "---\nx-template: &custom retained\nx-copy: *custom\n---\nBody\n",
            {"type": "Guide"},
            None,
        ),
        ("---\ngenerated:\n  by:\n  custom: keep\n---\nBody\n", {}, "new/1"),
    ],
)
def test_roundtrip_review_cases(
    tmp_path: Path, text: str, fields: dict[str, object], producer: str | None
) -> None:
    path = tmp_path / "note.md"
    path.write_bytes(text.encode())
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md", if_match=note.etag, fields=fields, producer=producer
        )
        result = path.read_bytes().decode()
        parsed = frontmatter.loads(result).metadata
        for key, value in fields.items():
            assert parsed[key] == value
        if producer:
            assert parsed["generated"]["by"] == producer
        if "# keep" in text:
            assert "# keep" in result and "custom: retained" in result
        if "verified:" in text:
            assert (
                text[
                    text.index("verified:") : text.index("verified:")
                    + len("verified: [{by: human:alice}]")
                ]
                in result
            )
        if "\r\n" in text:
            assert result.count("\n") == result.count("\r\n")
            assert result.endswith("Body\r\n")
        if "&custom" in text:
            assert "x-template: &custom retained\nx-copy: *custom" in result
    finally:
        vault.close()


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


def test_flow_generated_keeps_unrelated_anchor_and_comment() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\nx: &shared keep\ngenerated: {by: old/1, custom: *shared # keep\n}\n---\nBody\n"
    result = author_text(text, {}, "new/1")
    assert "custom: *shared # keep\n" in result
    assert frontmatter.loads(result).metadata["generated"]["custom"] == "keep"
    assert frontmatter.loads(result).metadata["generated"]["by"] == "new/1"


def test_splice_validation_refuses_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import _okf_author

    original = "---\ntype: old\nverified: [{by: human:a}]\n---\nBody\n"
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        monkeypatch.setattr(_okf_author, "_render", lambda _value: "wrong")
        with pytest.raises(InvalidRequestError, match="safely"):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={"type": "new"}
            )
        assert (tmp_path / "note.md").read_text() == original
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_invalid_producer_leaves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    original = "---\ntype: Guide\nverified: [{by: human:a}]\n---\nBody\n"
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text(original)
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
                {
                    "path": "note.md",
                    "if_match": etag,
                    "fields": {"title": "New"},
                    "producer": "a\nb/1",
                },
            )
    assert (tmp_path / "note.md").read_text() == original


def test_empty_final_generated_by() -> None:
    from markdown_vault_mcp._okf_author import author_text

    text = "---\ngenerated:\n  by:\n---\nBody\n"
    result = author_text(text, {}, "new/1")
    assert frontmatter.loads(result).metadata["generated"]["by"] == "new/1"


def test_replacing_anchored_or_aliased_field_is_refused() -> None:
    from markdown_vault_mcp._okf_author import author_text

    for text, fields, producer in [
        ("---\ntitle: &v value\nx-copy: *v\n---\nBody\n", {"title": "new"}, None),
        ("---\nx: &v {by: old/1}\ngenerated: *v\n---\nBody\n", {}, "new/1"),
    ]:
        with pytest.raises(InvalidRequestError):
            author_text(text, fields, producer)


def test_invalid_splice_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import _okf_author

    original = "---\ntype: old\n---\nBody\n"
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        monkeypatch.setattr(_okf_author, "_render", lambda _value: "[")
        with pytest.raises(InvalidRequestError, match="safely"):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={"type": "new"}
            )
        assert (tmp_path / "note.md").read_text() == original
    finally:
        vault.close()


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


def test_empty_generated_placeholder_accepts_producer() -> None:
    from markdown_vault_mcp._okf_author import author_text

    result = author_text(
        "---\ngenerated:\nverified: [{by: human:a}]\n---\nBody\n", {}, "new/1"
    )
    assert frontmatter.loads(result).metadata["generated"]["by"] == "new/1"
    assert "verified: [{by: human:a}]" in result


def test_invalid_new_field_is_refused_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import _okf_author

    original = "---\ntype: old\n---\nBody\n"
    (tmp_path / "note.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        monkeypatch.setattr(_okf_author, "_render", lambda _value: "[")
        with pytest.raises(InvalidRequestError, match="safely"):
            vault.writer.okf_author(
                "note.md", if_match=note.etag, fields={"type": "new", "title": "new"}
            )
        assert (tmp_path / "note.md").read_text() == original
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
    "field,old,value",
    [
        ("tags", "- one\n- two", ["new"]),
        ("sources", "- resource: old", [{"resource": "new", "extra": 1}]),
    ],
)
def test_replan_indentless_sequences(field: str, old: str, value: object) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text(
        f"---\n{field}:\n{old}\nother: keep\n---\nBody\n", {field: value}, None
    )
    assert parse_frontmatter(result).metadata == {field: value, "other": "keep"}
    assert "other: keep\n---\nBody\n" in result


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
    ],
)
def test_replan_invalid_values(fields: dict) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError):
        author_text("---\ntype: Existing\n---\nBody", fields, None)


def test_replan_existing_invalid_sources_untouched_and_date_valid() -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text(
        "---\nsources: [legacy]\n---\nBody", {"stale_after": "2027-03-06"}, None
    )
    assert "sources: [legacy]" in result
    assert parse_frontmatter(result).metadata["stale_after"] == "2027-03-06"


def test_replan_unrelated_anchor_and_extension_stay_intact() -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    text = "---\nshared: &x value\ncopy: *x\ngenerated:\n  by: old/1 # attribution\n  extension: &e [a, b] # untouched\n---\nBody\n"
    result = author_text(text, {"type": "Guide"}, "human:alice")
    assert "shared: &x value\ncopy: *x\n" in result
    assert "  extension: &e [a, b] # untouched\n" in result
    assert "# attribution" in result
    assert parse_frontmatter(result).metadata["generated"]["by"] == "human:alice"


def test_replan_bom_stays_at_file_start_and_reader_body_is_unchanged() -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    original = "\ufeff# Body\n\ufeffinside\n"
    result = author_text(original, {"title": "New"}, None)
    assert result.startswith("\ufeff---\n")
    assert result.endswith("# Body\n\ufeffinside\n")
    assert (
        parse_frontmatter(result.removeprefix("\ufeff")).content
        == parse_frontmatter(original.removeprefix("\ufeff")).content
    )


def test_replan_bom_on_existing_header_uses_reader(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_bytes("\ufeff---\ntype: Old\n---\nBody\n".encode())
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        before = vault.reader.read("note.md")
        assert before is not None
        vault.writer.okf_author(
            "note.md", if_match=before.etag, fields={"title": "New"}
        )
        after = vault.reader.read("note.md")
        assert after is not None
        assert after.frontmatter["type"] == "Old"
        assert after.frontmatter["title"] == "New"
        assert path.read_bytes().startswith(b"\xef\xbb\xbf---")
    finally:
        vault.close()


@pytest.mark.parametrize(
    "text", ["+++\ntitle = 'Old'\n+++\nBody", '{\n"title": "Old"\n}\nBody']
)
def test_replan_non_yaml_headers_are_refused(text: str) -> None:
    from markdown_vault_mcp._okf_author import author_text

    with pytest.raises(InvalidRequestError, match="YAML"):
        author_text(text, {"title": "New"}, None)


@pytest.mark.parametrize(
    "value", ["2027-03-06", "2027-03-06T12:30:00Z", "2027-03-06T12:30:00+01:00"]
)
def test_replan_supported_stale_after_values(value: str) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text("---\ntype: Guide\n---\nBody", {"stale_after": value}, None)
    assert parse_frontmatter(result).metadata["stale_after"] == value


@pytest.mark.parametrize(
    ("fields", "producer"),
    [
        ({"title": "New"}, None),
        ({"type": "New"}, None),
        ({}, "human:alice"),
        ({"title": "New"}, "human:alice"),
    ],
)
def test_round3_flow_root_read_author_read(
    tmp_path: Path, fields: dict, producer: str | None
) -> None:
    text = "---\n{type: Old, sources: [{resource: /source.md}],\n # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}, generated: {by: old/1, extension: yes}}\n---\nBody\n"
    path = tmp_path / "note.md"
    path.write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        before = vault.reader.read("note.md")
        assert before is not None
        vault.writer.okf_author(
            "note.md", if_match=before.etag, fields=fields, producer=producer
        )
        after = vault.reader.read("note.md")
        assert after is not None
        assert after.frontmatter["type"] == fields.get("type", "Old")
        if "title" in fields:
            assert after.frontmatter["title"] == "New"
        assert after.frontmatter["sources"] == [{"resource": "/source.md"}]
        assert after.frontmatter["verified"] == [{"by": "human:bob"}]
        assert after.frontmatter["generated"]["by"] == (producer or "old/1")
        result = path.read_text()
        assert (
            "sources: [{resource: /source.md}],\n # evidence\n verified: [{by: human:bob}], x-extra: {keep: true}"
            in result
        )
        assert "extension: yes" in result
        assert result.endswith("---\nBody\n")
    finally:
        vault.close()


def test_round3_header_newline_is_independent_of_body() -> None:
    from markdown_vault_mcp._okf_author import author_text

    original = "---\ntype: Guide\n---\nline1\r\nline2\r\n"
    result = author_text(original, {"title": "New"}, None)
    assert result == "---\ntype: Guide\ntitle: New\n---\nline1\r\nline2\r\n"


def test_round3_inherited_invalid_generated_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    original = "---\n<<: {generated: []}\ntype: Guide\n---\nBody\n"
    path.write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        before = vault.reader.read("note.md")
        assert before is not None
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_author(
                "note.md", if_match=before.etag, fields={}, producer="human:alice"
            )
        assert path.read_text() == original
    finally:
        vault.close()


@pytest.mark.parametrize("mapping", ["{}", "{type: Guide,}"])
def test_round3_flow_root_adds_producer(mapping: str) -> None:
    from markdown_vault_mcp._okf_author import author_text
    from markdown_vault_mcp.scanner import parse_frontmatter

    result = author_text(f"---\n{mapping}\n---\nBody\n", {}, "process:job")
    assert parse_frontmatter(result).metadata["generated"]["by"] == "process:job"
    assert result.endswith("---\nBody\n")
