"""Read authored or synthesized folder navigation."""

import errno

import pytest

from markdown_vault_mcp.vault import Vault


@pytest.mark.parametrize("case", ["crlf", "bounded", "cursor"])
def test_authored_body_precedes_listing(tmp_path, case):
    if case == "crlf":
        body = b"\r\n# Heading  \r\n\r\nText\t\r\n"
        data = b'---\r\nokf_version: "0.2"\r\n---\r\n' + body
        kwargs = {}
    elif case == "bounded":
        (tmp_path / "first.md").write_text("first")
        body = data = b"  authored\n"
        kwargs = {"entry_limit": 1}
    else:
        body = data = b"# Authored\n"
        kwargs = {"cursor": "stale.md"}
    (tmp_path / "index.md").write_bytes(data)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("", **kwargs)
        assert (result.source, result.body, result.enumeration, result.next_cursor) == (
            "authored",
            body.decode(),
            "not_inspected",
            None,
        )
    finally:
        vault.close()


def test_authored_empty_metadata_body(tmp_path):
    (tmp_path / "index.md").write_text("---\n---\n# Authored\n")
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("")
        assert (result.source, result.body) == ("authored", "# Authored\n")
    finally:
        vault.close()


def test_synthesis_includes_live_candidates_and_does_not_write(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "untyped.md").write_text("body")
    (tmp_path / "bad.md").write_text("---\ntype: [\n---\n")
    (tmp_path / ".hidden.md").write_text("hidden")
    (tmp_path / "child").mkdir()
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        result = vault.reader.okf_navigation("")
        assert result.source == "synthesized"
        assert "[untyped.md](./untyped.md)" in result.body
        assert "[bad.md](./bad.md)" in result.body
        assert "[.hidden.md](./.hidden.md)" in result.body
        assert "[child](./child) - folder" in result.body
        assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before
        vault.index.build_index()
        indexed = vault.reader.okf_navigation("")
        assert "[untyped.md](./untyped.md)" in indexed.body
    finally:
        vault.close()


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("symlink", "symlink"),
        ("nonregular", "nonregular"),
        ("invalid", "invalid_encoding"),
        ("read", "io_error"),
    ],
)
def test_unreadable_authored_is_not_replaced(tmp_path, monkeypatch, kind, reason):
    path = tmp_path / "index.md"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "nonregular":
        path.mkdir()
    elif kind == "invalid":
        path.write_bytes(b"\xff")
    else:
        from markdown_vault_mcp.facets import reader

        path.write_text("secret")

        def fail(*_args):
            raise PermissionError(errno.EACCES, "denied")

        monkeypatch.setattr(reader, "read_regular_nofollow", fail)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.okf_navigation("")
        assert (result.source, result.body, result.reason) == (None, None, reason)
    finally:
        vault.close()


@pytest.mark.parametrize("policy_kind", ["path", "failure"])
def test_internal_index_is_refused(tmp_path, monkeypatch, policy_kind):
    from markdown_vault_mcp.bundle_inventory import BundleInspector, InspectionPolicy

    index = tmp_path / "index.md"
    index.write_text("secret")
    policy = InspectionPolicy(index_path=index) if policy_kind == "path" else None
    vault = Vault(source_dir=tmp_path, inspection_policy=policy)
    try:
        # An uninspected folder returns before attempting an authored read.
        assert vault.reader.okf_navigation("missing").state == "missing"
        if policy_kind == "failure":

            def fail(*_args):
                raise PermissionError(errno.EACCES, "denied")

            monkeypatch.setattr(BundleInspector, "_prepare_policy", fail)
        result = vault.reader.okf_navigation("")
        state, reason = (
            ("inspected", "internal")
            if policy_kind == "path"
            else (
                "unavailable",
                None,
            )
        )
        assert (result.state, result.reason, result.body) == (state, reason, None)
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
            assert payload["next_cursor"] is not None
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
            assert "[b.md](./b.md)" in continued["body"]
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
        assert "[note-1009.md](./note-1009.md)" in second.body
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
        expected = [f"- [dir-{n}](./dir-{n}) - folder" for n in range(3)] + [
            f"- [note-{n:02}.md](./note-{n:02}.md)" for n in range(25)
        ]
        assert lines == expected
    finally:
        vault.close()


def test_selected_bundle_destinations_resolve_from_its_index(tmp_path):
    import re
    from urllib.parse import urljoin

    bundle = tmp_path / "collection" / "bundle"
    (bundle / "child").mkdir(parents=True)
    (bundle / "note.md").write_text("note")
    (bundle / "mail:note.md").write_text("note")
    vault = Vault(source_dir=tmp_path)
    try:
        page = vault.reader.okf_navigation("collection/bundle")
        links = dict(re.findall(r"\[([^]]+)\]\(([^)]+)\)", page.body))
        index = "https://example.invalid/index.md"
        assert urljoin(index, links["note.md"]) == "https://example.invalid/note.md"
        assert urljoin(index, links["child"]) == "https://example.invalid/child"
        assert (
            urljoin(index, links["mail:note.md"])
            == "https://example.invalid/mail:note.md"
        )
    finally:
        vault.close()


def test_synthesized_walk_reaches_child_folder_and_note(tmp_path):
    import re

    from markdown_vault_mcp.utils.links import decode_link_target

    folder = tmp_path / "dir [a] b"
    folder.mkdir()
    (folder / "n [1].md").write_text("note body")
    vault = Vault(source_dir=tmp_path)
    try:

        def entry(body: str) -> tuple[str, str, str]:
            label, destination, rest = re.search(
                r"- \[(.*)\]\((\S+)\)(.*)", body
            ).groups()
            return re.sub(r"\\([\\\[\]])", r"\1", label), destination, rest

        name, destination, rest = entry(vault.reader.okf_navigation("").body)
        assert (name, destination, rest) == (
            "dir [a] b",
            "./dir%20[a]%20b",
            " - folder",
        )
        assert decode_link_target(destination.removeprefix("./")) == name
        child = vault.reader.okf_navigation(name)
        assert (child.state, child.source) == ("inspected", "synthesized")
        note, _, rest = entry(child.body)
        assert (note, rest) == ("n [1].md", "")
        read = vault.reader.read(f"{name}/{note}")
        assert read is not None and read.content == "note body"
    finally:
        vault.close()


def test_oversized_designated_index_refuses_navigation_but_sections_remain_readable(
    tmp_path,
):
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "deep").mkdir()
    curated = b'---\nokf_version: "0.2"\n---\n# Index\n\n' + b"Curated order\n" * 20
    (tmp_path / "index.md").write_bytes(curated)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="off", okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            max_note_read_bytes=100,
        ),
    )
    try:
        vault.index.build_index()
        curated_result = vault.reader.okf_navigation("")
        assert (curated_result.source, curated_result.body, curated_result.reason) == (
            None,
            None,
            "note_limit",
        )
        assert (tmp_path / "index.md").read_bytes() == curated
        for i in range(8):
            vault.writer.write(f"deep/note-{i}.md", f"# Long title {i}\n")
        wait_for_writer_drain(vault)
        before = (tmp_path / "index.md").read_bytes()
        result = vault.reader.okf_navigation("")
        assert (result.source, result.body, result.reason) == (None, None, "note_limit")
        assert (tmp_path / "index.md").read_bytes() == before
        heading = next(
            entry.heading
            for entry in vault.reader.get_toc("index.md")
            if entry.level == 2
        )
        assert (
            "/deep/note-7.md" in vault.reader.read("index.md", section=heading).content
        )
    finally:
        vault.close()
