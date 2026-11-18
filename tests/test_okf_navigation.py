"""Read authored or synthesized folder navigation."""

import pytest

from markdown_vault_mcp.vault import Vault


def test_authored_body_preserves_crlf(tmp_path):
    body = b"\r\n# Heading  \r\n\r\nText\t\r\n"
    (tmp_path / "index.md").write_bytes(b'---\r\nokf_version: "0.2"\r\n---\r\n' + body)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("")
        assert result.source == "authored"
        assert result.body == body.decode()
    finally:
        vault.close()


def test_synthesis_includes_live_candidates_and_does_not_write(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "untyped.md").write_text("body")
    (tmp_path / "bad.md").write_text("---\ntype: [\n---\n")
    (tmp_path / "a[b].md").write_text("---\ntype: concept\n---\n")
    (tmp_path / ".hidden.md").write_text("hidden")
    (tmp_path / "child").mkdir()
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        result = vault.reader.okf_navigation("")
        assert result.source == "synthesized"
        assert "[untyped.md](/untyped.md)" in result.body
        assert "[bad.md](/bad.md)" in result.body
        assert "[.hidden.md](/.hidden.md)" in result.body
        assert "[child](/child/)" in result.body
        assert r"[a\[b\].md]" in result.body
        assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before
    finally:
        vault.close()


def test_authored_precedes_bounded_inventory(tmp_path):
    (tmp_path / "first.md").write_text("first")
    (tmp_path / "index.md").write_text("  authored\n")
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("", entry_limit=1)
        assert result.source == "authored"
        assert result.body == "  authored\n"
        assert result.enumeration == "not_inspected"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("symlink", "symlink"),
        ("nonregular", "nonregular"),
        ("invalid", "invalid_encoding"),
    ],
)
def test_unreadable_authored_is_not_replaced(tmp_path, kind, reason):
    path = tmp_path / "index.md"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "nonregular":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("")
        assert (result.source, result.body, result.reason) == (None, None, reason)
    finally:
        vault.close()


def test_scope_and_internal_index_policy(tmp_path):
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy

    (tmp_path / "index.md").write_text("secret")
    (tmp_path / ".markdown_vault_mcp").mkdir()
    vault = Vault(
        source_dir=tmp_path,
        inspection_policy=InspectionPolicy(index_path=tmp_path / "index.md"),
    )
    try:
        assert vault.reader.okf_navigation("missing").state == "missing"
        assert vault.reader.okf_navigation(".markdown_vault_mcp").state == "unavailable"
        result = vault.reader.okf_navigation("")
        assert (result.source, result.reason, result.body) == (None, "internal", None)
    finally:
        vault.close()


async def test_navigation_tool(tmp_path, monkeypatch):
    import json
    from dataclasses import asdict

    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    (tmp_path / "index.md").write_bytes(
        b"-----\r\nokf_version: 0.2\r\n------ \r\n\r\n# authored\r\n"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    vault = Vault(source_dir=tmp_path)
    try:
        expected = json.loads(json.dumps(asdict(vault.reader.okf_navigation(""))))
        async with Client(make_server()) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert tools["okf_navigation"].annotations.read_only_hint
            payload = (await client.call_tool("okf_navigation", {})).structured_content
            assert payload == expected
            with pytest.raises(ToolError, match="relative folder"):
                await client.call_tool("okf_navigation", {"folder": "../bad"})
    finally:
        vault.close()


def test_internal_index_hardlink_and_incomplete_listing(tmp_path):
    import os

    from markdown_vault_mcp.bundle_inventory import InspectionPolicy

    (tmp_path / "state").write_text("secret")
    os.link(tmp_path / "state", tmp_path / "index.md")
    vault = Vault(
        source_dir=tmp_path,
        inspection_policy=InspectionPolicy(state_paths=(tmp_path / "state",)),
    )
    try:
        result = vault.reader.okf_navigation("", entry_limit=1)
        assert result.source is None
        assert result.reason == "internal"
    finally:
        vault.close()


def test_partial_synthesis_and_io_error(tmp_path, monkeypatch):
    from markdown_vault_mcp import okf_navigation

    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(name)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("", entry_limit=1)
        assert result.source == "synthesized"
        assert result.enumeration == "entry_limit"
        assert result.body.count("- [") == 1

        def fail(*_args):
            raise PermissionError("denied")

        monkeypatch.setattr(okf_navigation, "read_regular_nofollow", fail)
        result = vault.reader.okf_navigation("")
        assert (result.source, result.reason) == (None, "io_error")
    finally:
        vault.close()


def test_policy_failure_refuses_index(tmp_path, monkeypatch):
    from markdown_vault_mcp.bundle_inventory import (
        BundleInspectionRequest,
        BundleInspector,
    )

    (tmp_path / "index.md").write_text("secret")
    vault = Vault(source_dir=tmp_path)
    try:
        inspection = vault.reader.inspect_bundle(BundleInspectionRequest(""))
        monkeypatch.setattr(vault.reader, "inspect_bundle", lambda _request: inspection)

        def fail(*_args):
            raise OSError("policy unavailable")

        monkeypatch.setattr(BundleInspector, "_prepare_policy", fail)
        result = vault.reader.okf_navigation("")
        assert (result.source, result.reason) == (None, "io_error")
    finally:
        vault.close()


def test_synthesis_includes_non_admitted_after_index_build(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "missing.md").write_text("untyped")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        result = vault.reader.okf_navigation("")
        from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest

        inspection = vault.reader.inspect_bundle(BundleInspectionRequest(""))
        entry = next(
            entry for entry in inspection.entries if entry.path == "missing.md"
        )
        assert entry.search_state == "non_admitted"
        assert "[missing.md](/missing.md)" in result.body
    finally:
        vault.close()


@pytest.mark.parametrize("opening,closing", [("--- ", "--- "), ("-----", "------")])
def test_authored_frontmatter_variants_preserve_body(tmp_path, opening, closing):
    body = "\r\n  # Heading  \r\n\t\r\n"
    (tmp_path / "index.md").write_bytes(
        f'{opening}\r\nokf_version: "0.2"\r\n{closing}\r\n{body}'.encode()
    )
    vault = Vault(source_dir=tmp_path)
    try:
        assert vault.reader.okf_navigation("").body == body
    finally:
        vault.close()


@pytest.mark.parametrize(
    "text",
    [
        "---\n\nIntro\n\n---\n\nBody\n",
        "---\ntype: [\n---\nBody\n",
        "---\n- one\n---\nBody\n",
    ],
)
def test_thematic_or_invalid_metadata_is_verbatim(tmp_path, text):
    (tmp_path / "index.md").write_text(text)
    vault = Vault(source_dir=tmp_path)
    try:
        assert vault.reader.okf_navigation("").body == text
    finally:
        vault.close()


@pytest.mark.parametrize(
    "limit,size,expected",
    [
        (262144, 70000, "authored"),
        (0, 300000, "authored"),
        (70000, 70001, None),
        (70000, 70000, "authored"),
    ],
)
def test_authored_full_note_limit(tmp_path, limit, size, expected):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "index.md").write_bytes(b"x" * size)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(max_note_read_bytes=limit)
    )
    try:
        result = vault.reader.okf_navigation("")
        assert result.source == expected
        assert result.body == ("x" * size if expected else None)
        assert result.reason == (None if expected else "note_limit")
    finally:
        vault.close()


async def test_invalid_folder_error_explains_root(tmp_path, monkeypatch):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("okf_navigation", {"folder": "."})
        message = str(error.value)
        assert "okf_navigation" in message and '""' in message and ".." in message


def test_whitespace_before_authored_metadata_preserves_suffix(tmp_path):
    body = "\r\n  Body\t\r\n"
    (tmp_path / "index.md").write_bytes(
        (" \r\n  --- \r\nkey: value\r\n--- \r\n" + body).encode()
    )
    vault = Vault(source_dir=tmp_path)
    try:
        assert vault.reader.okf_navigation("").body == body
    finally:
        vault.close()


@pytest.mark.parametrize(
    "text,body",
    [
        ('{\n"title": "Index"\n}\n\nBody\n', "\nBody\n"),
        ("---\nkey: value\n---", ""),
        ("---\nkey: value\n", "---\nkey: value\n"),
    ],
)
def test_other_frontmatter_boundaries(tmp_path, text, body):
    (tmp_path / "index.md").write_text(text)
    vault = Vault(source_dir=tmp_path)
    try:
        assert vault.reader.okf_navigation("").body == body
    finally:
        vault.close()
