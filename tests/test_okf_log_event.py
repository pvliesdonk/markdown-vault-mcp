"""Editorial events group related concept changes into one log entry."""

from datetime import date

import pytest

from markdown_vault_mcp._okf_log_event import (
    deferred_event,
)
from markdown_vault_mcp._okf_log_event import (
    record_event as _record_event,
)
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.utils.fs import is_regular_file
from markdown_vault_mcp.vault import Vault, VaultSettings


def record_event(  # noqa: PLR0913, RUF100 - mirrors the event owner and test date
    doc_mgr, paths, description, folders, proposed_by=None, *, today=None
):
    return _record_event(
        doc_mgr,
        paths,
        description,
        (folders, lambda: {"effective": True}),
        proposed_by,
        today=today,
    )


def test_event_groups_paths_and_keeps_same_day_entries(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "area").mkdir()
    calls = []
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=ContentConfig(
                okf_log="events", okf_folders=("area",)
            ).okf_capabilities,
        ),
        on_write=lambda path, _content, _op: calls.append(path.name),
    )
    try:
        for name in ("a one.md", "b#.md", "c.md"):
            vault.writer.write("area/" + name, "# Concept\n")
        assert not is_regular_file(tmp_path / "area" / "log.md")
        paths = ["area/a one.md", "area/b#.md", "area/c.md"]
        record_event(
            vault._doc_mgr, paths, "Changed policy", ("area",), today=date(2026, 1, 2)
        )
        record_event(
            vault._doc_mgr,
            [paths[0]],
            "Second event",
            ("area",),
            today=date(2026, 1, 2),
        )
        log = (tmp_path / "area" / "log.md").read_text()
        assert not log.startswith("---")
        assert log.count("## 2026-01-02") == 1
        assert log.count("\n- ") == 2
        assert (
            "/area/a%20one.md" in log and "/area/b%23.md" in log and "/area/c.md" in log
        )
        assert "Changed policy" in log and "Second event" in log
        assert vault._write_callback.drain()
        assert calls.count("log.md") == 2
    finally:
        vault.close()


@pytest.mark.parametrize(
    "description",
    ["", "x\ny", "x\x00y", "x\u2028y", "x\u2029y", "x" * 501, "bad\ud800"],
)
def test_invalid_description_refused(tmp_path, monkeypatch, description):
    (tmp_path / "a.md").write_text("# A\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        if description == "bad\ud800":

            def unexpected():
                pytest.fail("filesystem accessed before validation")

            monkeypatch.setattr(vault._doc_mgr, "ensure_writable", unexpected)
        with pytest.raises(InvalidRequestError, match="Event description"):
            record_event(vault._doc_mgr, ["a.md"], description, (".",))
    finally:
        vault.close()


def test_deferred_event_suppresses_log_but_keeps_index(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        with deferred_event():
            vault.writer.write("a.md", "# A\n")
        assert not is_regular_file(tmp_path / "log.md")
        assert "a.md" in (tmp_path / "index.md").read_text()
        vault.writer.write("b.md", "# B\n")
        assert "b.md" in (tmp_path / "log.md").read_text()
    finally:
        vault.close()


def test_refuses_non_markdown_and_control_in_path(tmp_path):
    (tmp_path / "note.txt").write_text("text")
    (tmp_path / "bad\nname.md").write_text("text")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        for path in ("note.txt", "bad\nname.md"):
            with pytest.raises(InvalidRequestError):
                record_event(vault._doc_mgr, [path], "Change", (".",))
        assert not is_regular_file(tmp_path / "log.md")
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_event_capability_and_result(tmp_path, monkeypatch):
    from fastmcp import Client

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "a.md").write_text("# A\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "events")
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_log_event",
            {"paths": ["a.md"], "description": "Change", "proposed_by": "Alice"},
        )
        assert _parse_tool_data(result)["path"] == "log.md"
    assert "Change" in (tmp_path / "log.md").read_text()
    assert "proposed by Alice" in (tmp_path / "log.md").read_text()


def test_routes_to_common_ancestor_and_preserves_existing_log(tmp_path):
    (tmp_path / "team" / "one").mkdir(parents=True)
    (tmp_path / "team" / "two").mkdir()
    (tmp_path / "team" / "one" / "a.md").write_text("# A\n")
    (tmp_path / "team" / "two" / "b.md").write_text("# B\n")
    log = tmp_path / "team" / "log.md"
    log.write_text(
        "---\ntitle: History\n---\n# Log\n\n## 2020-01-01\n\n- Older entry\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        result = record_event(
            vault._doc_mgr,
            ["team/one/a.md", "team/two/b.md"],
            "Coordinated change",
            (".",),
            today=date(2026, 1, 2),
        )
        assert result == "team/log.md"
        content = log.read_text()
        assert "- Older entry" in content and "Coordinated change" in content
    finally:
        vault.close()


def test_refuses_missing_reserved_and_aliased_log(tmp_path):
    (tmp_path / "index.md").write_text("# Index\n")
    (tmp_path / "note.md").write_text("# Note\n")
    (tmp_path / "other.md").write_text("# Other\n")
    (tmp_path / "log.md").symlink_to("other.md")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="concept notes"):
            record_event(vault._doc_mgr, ["index.md"], "Change", (".",))
        for paths in ([], ["missing.md"], ["note.md"]):
            with pytest.raises(InvalidRequestError):
                record_event(vault._doc_mgr, paths, "Change", (".",))
        assert (tmp_path / "other.md").read_text() == "# Other\n"
        (tmp_path / "log.md").unlink()
        (tmp_path / "log.md").symlink_to("gone.md")
        with pytest.raises(InvalidRequestError, match="alias"):
            record_event(vault._doc_mgr, ["note.md"], "Change", (".",))
        assert (tmp_path / "log.md").is_symlink()
        assert not is_regular_file(tmp_path / "gone.md")
    finally:
        vault.close()


def test_library_event_uses_configured_scope_and_mode(tmp_path):
    (tmp_path / "area").mkdir()
    (tmp_path / "area" / "a.md").write_text("# A\n")
    caps = ContentConfig(okf_log="events", okf_folders=("area",)).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        assert vault.writer.okf_log_event(["area/a.md"], "Policy") == "area/log.md"
    finally:
        vault.close()


def test_unreadable_concept_refuses_event_without_writing(tmp_path, monkeypatch):
    from pathlib import Path

    from markdown_vault_mcp.exceptions import DocumentUnreadableError

    (tmp_path / "a.md").write_text("# A\n")
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path.name == "a.md":
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        monkeypatch.setattr(Path, "open", denied)
        with pytest.raises(DocumentUnreadableError):
            record_event(vault._doc_mgr, ["a.md"], "Change", (".",))
        assert not is_regular_file(tmp_path / "log.md")
    finally:
        vault.close()


def test_event_preserves_existing_crlf_bytes(tmp_path):
    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    old = b"\xef\xbb\xbf---\r\ntitle: History\r\n---\r\n# Log\r\n\r\n## 2020-01-01\r\n\r\n- Old\r\n"
    log.write_bytes(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        record_event(vault._doc_mgr, ["a.md"], "Change", (".",), today=date(2026, 1, 2))
        result = log.read_bytes()
        inserted = b"## 2026-01-02\r\n\r\n- Change \xe2\x80\x94 [a.md](/a.md)\r\n\r\n"
        assert result.replace(inserted, b"") == old
        assert b"Change" in result
        assert b"\n" not in result.replace(b"\r\n", b"")
    finally:
        vault.close()


def test_event_refuses_unclosed_fence(tmp_path):
    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    old = b"# Log\n\n```\nunfinished\n"
    log.write_bytes(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError):
            record_event(vault._doc_mgr, ["a.md"], "Change", (".",))
        assert log.read_bytes() == old
    finally:
        vault.close()


def test_event_refuses_external_append(tmp_path, monkeypatch):
    from markdown_vault_mcp.exceptions import ConcurrentModificationError

    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    log.write_text("# Log\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    original = vault._doc_mgr.replace_body

    def concurrent(*args, **kwargs):
        log.write_text("# Log\nExternal\n")
        return original(*args, **kwargs)

    try:
        monkeypatch.setattr(vault._doc_mgr, "replace_body", concurrent)
        with pytest.raises(ConcurrentModificationError):
            record_event(vault._doc_mgr, ["a.md"], "Change", (".",))
        assert log.read_text() == "# Log\nExternal\n"
    finally:
        vault.close()


@pytest.mark.parametrize("proposed_by", ["", " ", "x" * 101, "bad\x00", "bad\ud800"])
def test_bad_proposer_refused(tmp_path, proposed_by):
    (tmp_path / "a.md").write_text("# A\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="Proposed by"):
            record_event(vault._doc_mgr, ["a.md"], "Change", (".",), proposed_by)
    finally:
        vault.close()


@pytest.mark.parametrize("old", [b"# Log\n\n\n", b"  \n\n", b"# Log\r\n\r\n\r\n"])
def test_seeded_whitespace_preserved_and_event_visible(tmp_path, old):
    from markdown_it import MarkdownIt

    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    log.write_bytes(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        record_event(vault._doc_mgr, ["a.md"], "Change", (".",))
        result = log.read_bytes()
        assert result.startswith(old)
        assert '<a href="/a.md">' in MarkdownIt("commonmark").render(result.decode())
    finally:
        vault.close()


def test_off_capability_message_has_reason(tmp_path):
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    try:
        with pytest.raises(InvalidRequestError, match="logging is off"):
            vault.writer.okf_log_event(["a.md"], "Change")
    finally:
        vault.close()
    caps = ContentConfig(okf_log="events").okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_capabilities=caps),
    )
    try:
        with pytest.raises(InvalidRequestError, match="no OKF bundle is active"):
            vault.writer.okf_log_event(["a.md"], "Change")
    finally:
        vault.close()


def test_case_mismatched_existing_path_never_produces_dead_link(tmp_path):
    (tmp_path / "area").mkdir()
    (tmp_path / "area" / "a.md").write_text("# A\n")
    alternate = tmp_path / "AREA" / "A.md"
    if not alternate.is_file():
        pytest.skip("requires a case-insensitive filesystem")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        try:
            result = record_event(vault._doc_mgr, ["AREA/A.md"], "Change", (".",))
        except InvalidRequestError:
            assert not is_regular_file(tmp_path / "log.md")
        else:
            assert "/area/a.md" in (tmp_path / result).read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    "description",
    [
        "2026. Policy now stricter",
        "1. first",
        "> quoted",
        "# h",
        "- i",
        "*urgent* x",
        "1) p",
        "<script>",
        "```",
        "[fake](/evil.md)",
        "**Deprecation**",
        "**Deprecation** Retired.",
        "**Deprecation** *urgent* &amp; _policy_",
        "  **Deprecation** <script> [fake](/evil) `unclosed  ",
        "**Deprecation**: retired *now*",
        "Aligned access, review rules (v2) - don't merge: see #12!",
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_leading_markers_render_as_one_event(tmp_path, description, existing):
    from markdown_it import MarkdownIt

    names = ["a`1.md", "b`2.md", "*odd*_&amp;.md"]
    for name in names:
        (tmp_path / name).write_text("# A\n")
    log = tmp_path / "log.md"
    old = b"# Log\n\n## 2025-01-01\n\n- Old\n"
    if existing:
        log.write_bytes(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        record_event(
            vault._doc_mgr,
            names,
            description,
            (".",),
            "*Alice* &amp;"
            if "_policy_" in description
            else (
                "Ann (ops)" if description.startswith("Aligned access") else "> Alice"
            ),
            today=date(2026, 3, 3),
        )
        record_event(
            vault._doc_mgr, [names[0]], "Next.", (".",), today=date(2026, 3, 3)
        )
        text = log.read_text()
        tokens = MarkdownIt("commonmark").parse(text)
        assert not any(
            t.type in {"ordered_list_open", "blockquote_open", "hr"} for t in tokens
        )
        rendered = MarkdownIt("commonmark").render(text)
        from html import escape

        if description.startswith("**Deprecation**") or description.strip().startswith(
            "**Deprecation**"
        ):
            assert "<strong>Deprecation</strong>" in rendered
        else:
            assert escape(description, quote=False) in rendered
        if "_policy_" in description:
            assert (
                "<strong>Deprecation</strong> *urgent* &amp;amp; _policy_" in rendered
            )
            assert "(proposed by *Alice* &amp;amp;)" in rendered
        elif description.startswith("Aligned access"):
            assert "(proposed by Ann (ops))" in rendered
        else:
            assert "(proposed by &gt; Alice)" in rendered
        assert "Next.:" not in text
        assert rendered.count("<a href=") == 4
        assert 'href="/evil.md"' not in rendered
        assert ">*odd*_&amp;amp;.md</a>" in rendered
        if description.startswith("Aligned access"):
            assert f"- {description} — " in text
        if description.startswith("**Deprecation**:"):
            assert "- **Deprecation**: retired" in text
        if existing:
            assert b"- Old\n" in log.read_bytes()
    finally:
        vault.close()


@pytest.mark.parametrize(
    "name",
    [
        "a#1.md",
        "a?x.md",
        "a%23.md",
        "a\\\\b.md",
        "a space.md",
        "a (1).md",
        "é.md",
        "a&amp;.md",
        "a%20b.md",
        "a&b.md",
        "my-note_v2.md",
    ],
)
def test_event_url_destinations_are_paths_not_query_or_fragment(tmp_path, name):
    from urllib.parse import unquote, urlsplit

    from markdown_it import MarkdownIt

    from markdown_vault_mcp.scanner import extract_links
    from markdown_vault_mcp.utils.links import decode_link_target

    (tmp_path / "team").mkdir()
    (tmp_path / "team" / name).write_text("# A\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        result = record_event(vault._doc_mgr, ["team/" + name], "Change", (".",))
        text = (tmp_path / result).read_text()
        tokens = MarkdownIt("commonmark").parse(text)
        link = next(
            c for t in tokens if t.children for c in t.children if c.type == "link_open"
        )
        url = urlsplit(link.attrGet("href"))
        assert not url.query and not url.fragment
        assert unquote(url.path) == "/team/" + name
        assert decode_link_target(url.path) == "/team/" + name
        links = extract_links(text, result)
        assert [x.target_path for x in links] == ["team/" + name]
        assert is_regular_file(tmp_path / unquote(url.path).lstrip("/"))
        if name == "my-note_v2.md":
            assert links[0].link_text == "team/" + name
    finally:
        vault.close()


def test_writes_mode_keeps_automatic_and_editorial_entries(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_log="writes").okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.writer.write("a.md", "# A\n")
        vault.writer.okf_log_event(["a.md"], "Editorial")
        text = (tmp_path / "log.md").read_text()
        assert "**Update**" in text and "Editorial" in text
    finally:
        vault.close()


@pytest.mark.parametrize("block", ["<!--", "<script>"])
@pytest.mark.parametrize("older", [False, True])
@pytest.mark.parametrize("day", [3, 4])
def test_hidden_date_heading_never_owns_event(tmp_path, block, older, day):
    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    end = "-->" if block == "<!--" else "</script>"
    prior = "2020-01-01\n----------\n\n- Previous\n\n" if older else ""
    old = f"# Log\n\n{prior}{block}\n## 2026-03-03\n- Hidden\n{end}\n".encode()
    log.write_bytes(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="unchanged"):
            record_event(
                vault._doc_mgr, ["a.md"], "Change", (".",), today=date(2026, 3, day)
            )
        assert log.read_bytes() == old
    finally:
        vault.close()


def test_case_lookup_scans_shared_parent_once(tmp_path, monkeypatch):
    import markdown_vault_mcp._okf_log_event as event

    (tmp_path / "area").mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / "area" / name).write_text("# A\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    actual_scandir = event.os.scandir
    counts = []

    def track(path):
        if path == tmp_path / "area":
            counts.append(path)
        return actual_scandir(path)

    try:
        monkeypatch.setattr(event.os, "scandir", track)
        record_event(
            vault._doc_mgr, ["area/a.md", "area/b.md", "area/c.md"], "Change", (".",)
        )
        assert len(counts) == 1
    finally:
        vault.close()


def test_real_dated_section_with_subheading_accepts_event(tmp_path):
    from markdown_it import MarkdownIt

    (tmp_path / "a.md").write_text("# A\n")
    log = tmp_path / "log.md"
    old = "# Log\n\n## 2026-03-03\n\n### Detail\n\n- Earlier\n"
    log.write_text(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        record_event(vault._doc_mgr, ["a.md"], "New", (".",), today=date(2026, 3, 3))
        result = log.read_text()
        assert result.startswith(old)
        assert "New" in MarkdownIt("commonmark").render(result)
    finally:
        vault.close()
