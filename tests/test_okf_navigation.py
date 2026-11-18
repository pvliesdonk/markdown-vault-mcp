"""Read authored or synthesized folder navigation."""

import errno
import os

import pytest

from markdown_vault_mcp.vault import Vault


def test_authored_body_preserves_crlf(tmp_path, monkeypatch):
    from markdown_vault_mcp.facets import reader

    original = reader.strip_frontmatter_block
    calls = []

    def capture(text, *, preserve_body=False):
        calls.append(preserve_body)
        return original(text, preserve_body=preserve_body)

    monkeypatch.setattr(reader, "strip_frontmatter_block", capture)
    body = b"\r\n# Heading  \r\n\r\nText\t\r\n"
    (tmp_path / "index.md").write_bytes(b'---\r\nokf_version: "0.2"\r\n---\r\n' + body)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("")
        assert result.source == "authored"
        assert result.body == body.decode()
        assert calls == [True]
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
        vault.index.build_index()
        indexed = vault.reader.okf_navigation("")
        assert "[untyped.md](/untyped.md)" in indexed.body
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


@pytest.mark.parametrize("policy_kind", ["path", "hardlink"])
def test_internal_index_is_refused(tmp_path, policy_kind):
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy

    index = tmp_path / "index.md"
    if policy_kind == "path":
        index.write_text("secret")
        policy = InspectionPolicy(index_path=index)
    else:
        state = tmp_path / "state"
        state.write_text("secret")
        os.link(state, index)
        policy = InspectionPolicy(state_paths=(state,))
    vault = Vault(source_dir=tmp_path, inspection_policy=policy)
    try:
        assert vault.reader.okf_navigation("missing").state == "missing"
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

    folder = tmp_path / "guides"
    folder.mkdir()
    (folder / "a.md").write_text("a")
    (folder / "b.md").write_text("b")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    vault = Vault(source_dir=tmp_path)
    try:
        expected = json.loads(
            json.dumps(asdict(vault.reader.okf_navigation("guides", entry_limit=1)))
        )
        async with Client(make_server()) as client:
            payload = (
                await client.call_tool(
                    "okf_navigation", {"folder": "guides", "entry_limit": 1}
                )
            ).structured_content
            assert payload == expected
            assert payload["next_cursor"] == "guides/a.md"
            continued = (
                await client.call_tool(
                    "okf_navigation",
                    {
                        "folder": "guides",
                        "entry_limit": 1,
                        "cursor": payload["next_cursor"],
                    },
                )
            ).structured_content
            assert "[b.md](/guides/b.md)" in continued["body"]
            with pytest.raises(ToolError, match="cursor"):
                await client.call_tool(
                    "okf_navigation",
                    {"folder": "guides", "cursor": "guides/a/nested.md"},
                )
            with pytest.raises(ToolError) as error:
                await client.call_tool("okf_navigation", {"folder": "."})
            message = str(error.value)
            assert "okf_navigation" in message and '""' in message and ".." in message
    finally:
        vault.close()


def test_partial_synthesis(tmp_path):
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(name)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("", entry_limit=1)
        assert result.source == "synthesized"
        assert result.enumeration == "entry_limit"
        assert result.next_cursor is not None
        assert result.body.count("- [") == 1
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["read", "policy"])
def test_io_error_refuses_index(tmp_path, monkeypatch, failure):
    from markdown_vault_mcp.bundle_inventory import (
        BundleInspectionRequest,
        BundleInspector,
    )
    from markdown_vault_mcp.facets import reader

    (tmp_path / "index.md").write_text("secret")
    vault = Vault(source_dir=tmp_path)
    try:
        if failure == "policy":
            inspection = vault.reader.inspect_bundle(BundleInspectionRequest(""))
            monkeypatch.setattr(
                vault.reader, "inspect_bundle", lambda _request: inspection
            )
            target, name = BundleInspector, "_prepare_policy"
        else:
            target, name = reader, "read_regular_nofollow"

        def fail(*_args):
            raise PermissionError(errno.EACCES, "denied")

        monkeypatch.setattr(target, name, fail)
        result = vault.reader.okf_navigation("")
        assert (result.source, result.reason) == (None, "io_error")
    finally:
        vault.close()


@pytest.mark.parametrize(
    "limit,size,expected",
    [
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


def test_navigation_pages_more_than_thousand_children(tmp_path):
    for number in range(1010):
        (tmp_path / f"note-{number:04}.md").write_text("note")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_navigation("", entry_limit=1000)
        second = vault.reader.okf_navigation(
            "", entry_limit=1000, cursor=first.next_cursor
        )
        assert first.next_cursor is not None
        assert second.next_cursor is None
        assert sum(page.body.count("- [") for page in (first, second)) == 1010
        assert "[note-1009.md](/note-1009.md)" in second.body
    finally:
        vault.close()


def test_pages_only_immediate_children_in_name_order(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    folder = tmp_path / "bundle"
    folder.mkdir()
    for number in range(25):
        (folder / f"note-{number:02}.md").write_text("untyped")
    for number in range(3):
        child = folder / f"dir-{number}"
        child.mkdir()
        for nested in range(20):
            (child / f"nested-{nested}.md").write_text("nested")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        cursor = None
        lines = []
        while True:
            page = vault.reader.okf_navigation("bundle", entry_limit=4, cursor=cursor)
            lines.extend(
                line for line in page.body.splitlines() if line.startswith("- [")
            )
            cursor = page.next_cursor
            if cursor is None:
                assert page.enumeration == "complete"
                break
            assert page.enumeration == "entry_limit"
        expected = [f"- [dir-{n}](/bundle/dir-{n}/)" for n in range(3)] + [
            f"- [note-{n:02}.md](/bundle/note-{n:02}.md)" for n in range(25)
        ]
        assert lines == expected
    finally:
        vault.close()


def test_cursor_directory_and_exact_boundary_at_root(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "nested.md").write_text("nested")
    (tmp_path / "b.md").write_text("b")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_navigation("", entry_limit=1)
        assert first.next_cursor == "a"
        second = vault.reader.okf_navigation(
            "", entry_limit=1, cursor=first.next_cursor
        )
        assert "[b.md](/b.md)" in second.body
        assert second.next_cursor == "b.md"
        last = vault.reader.okf_navigation("", entry_limit=1, cursor=second.next_cursor)
        assert last.body == "# Index\n\n"
        assert (last.enumeration, last.next_cursor) == ("complete", None)
    finally:
        vault.close()


def test_authored_index_with_cursor_ignores_listing(tmp_path):
    (tmp_path / "index.md").write_text("# Authored\n")
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("", cursor="stale.md")
        assert (result.source, result.body, result.next_cursor) == (
            "authored",
            "# Authored\n",
            None,
        )
    finally:
        vault.close()
