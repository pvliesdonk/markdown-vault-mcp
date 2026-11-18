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
        assert {e.path for e in result.entries} >= {
            "untyped.md",
            "bad.md",
            "a[b].md",
            ".hidden.md",
            "child",
        }
        assert "[untyped.md](/untyped.md)" in result.body
        assert "[child](/child/)" in result.body
        assert r"[a\[b\].md]" in result.body
        assert (
            next(e for e in result.entries if e.path == "bad.md").parse_state
            == "malformed_metadata"
        )
        assert next(e for e in result.entries if e.path == ".hidden.md").hidden_path
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
        assert result.enumeration == "entry_limit"
        assert result.entries == ()
    finally:
        vault.close()


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("symlink", "symlink"),
        ("nonregular", "nonregular"),
        ("oversize", "note_limit"),
        ("invalid", "invalid_encoding"),
    ],
)
def test_unreadable_authored_is_not_replaced(tmp_path, kind, reason):
    path = tmp_path / "index.md"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "nonregular":
        path.mkdir()
    elif kind == "oversize":
        path.write_bytes(b"x" * 65537)
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

    (tmp_path / "index.md").write_text("# authored\n")
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
        assert len(result.entries) == 1

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
        entry = next(entry for entry in result.entries if entry.path == "missing.md")
        assert entry.search_state == "non_admitted"
        assert "[missing.md](/missing.md)" in result.body
    finally:
        vault.close()
