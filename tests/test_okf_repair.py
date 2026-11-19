"""Deliberate repair preserves provenance evidence."""

import pytest

from markdown_vault_mcp._okf_author import author_text
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.scanner import parse_frontmatter

NOTE = """---
type: concept
generated: {by: old/1, at: yesterday, extra: keep}
verified:
  - {by: 'human:one', at: yesterday, detail: keep}
  - {by: 'process:two', at: today}
x-mvmcp:
  reviews: [{by: 'human:one', snapshot: claim}]
  other: keep
---
Body\n"""


def test_retire_and_replace_preserve_evidence() -> None:
    result = author_text(
        NOTE,
        {},
        None,
        repair={
            "retire_reviews": {"indices": [0], "reason": "superseded"},
            "replace_generated": {"by": "new/2", "reason": "correction"},
        },
    )
    before = parse_frontmatter(NOTE).metadata
    after = parse_frontmatter(result).metadata
    assert after["verified"] == before["verified"][1:]
    assert after["x-mvmcp"]["review_history"] == [
        {"review": before["verified"][0], "reason": "superseded"}
    ]
    assert after["x-mvmcp"]["generated_history"] == [
        {"generated": before["generated"], "reason": "correction"}
    ]
    assert after["x-mvmcp"]["reviews"] == before["x-mvmcp"]["reviews"]
    assert after["x-mvmcp"]["other"] == "keep"
    assert after["generated"]["by"] == "new/2"


@pytest.mark.parametrize(
    "repair",
    [
        {},
        {"retire_reviews": {"indices": [0, 0], "reason": "x"}},
        {"retire_reviews": {"indices": [2], "reason": "x"}},
        {"retire_reviews": {"indices": [True], "reason": "x"}},
        {"retire_reviews": {"indices": [0], "reason": " "}},
        {"replace_generated": {"by": "new/2", "reason": ""}},
        {"unknown": {}},
    ],
)
def test_refuses_ambiguous_repairs(repair: dict) -> None:
    with pytest.raises(InvalidRequestError):
        author_text(NOTE, {}, None, repair=repair)


@pytest.mark.parametrize(
    "text",
    [
        NOTE.replace("other: keep", "other: keep\n  other: lost"),
        NOTE.replace("other: keep", "other: &anchor keep\n  alias: *anchor"),
        NOTE.replace("review_history", "unused").replace(
            "other: keep", "review_history: broken"
        ),
        NOTE.replace("- {by: 'human:one', at: yesterday, detail: keep}", "- malformed"),
    ],
)
def test_refuses_unsafe_existing_evidence(text: str) -> None:
    with pytest.raises(InvalidRequestError):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "x"}}
        )


def test_facet_repair_uses_etag_and_remaining_reviews(tmp_path) -> None:
    from markdown_vault_mcp.exceptions import ConcurrentModificationError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    path = tmp_path / "note.md"
    path.write_text(NOTE)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    try:
        note = vault.reader.read("note.md")
        assert note is not None
        repair = {"retire_reviews": {"indices": [0], "reason": "wrong"}}
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_author(
                "note.md", if_match="stale", fields={}, repair=repair
            )
        assert path.read_text() == NOTE
        vault.writer.okf_author("note.md", if_match=note.etag, fields={}, repair=repair)
        assert (
            parse_frontmatter(path.read_text()).metadata["verified"][0]["by"]
            == "process:two"
        )
    finally:
        vault.close()


def test_retire_single_mapping_keeps_entire_entry() -> None:
    original = (
        "---\nverified: {by: 'human:one', at: yesterday, extra: retained}\n---\nBody\n"
    )
    result = author_text(
        original,
        {},
        None,
        repair={"retire_reviews": {"indices": [0], "reason": "correction"}},
    )
    metadata = parse_frontmatter(result).metadata
    assert metadata["verified"] == []
    assert metadata["x-mvmcp"]["review_history"] == [
        {
            "review": {"by": "human:one", "at": "yesterday", "extra": "retained"},
            "reason": "correction",
        }
    ]


def test_retirement_changes_trust_tier() -> None:
    from markdown_vault_mcp.okf import derive_trust_tier

    note = NOTE.replace("at: yesterday", "at: 2026-01-01T00:00:00Z").replace(
        "at: today", "at: 2026-01-01T00:00:00Z"
    )
    before = parse_frontmatter(note).metadata
    result = author_text(
        note,
        {},
        None,
        repair={"retire_reviews": {"indices": [0], "reason": "correction"}},
    )
    assert derive_trust_tier(before) == "human-reviewed"
    assert derive_trust_tier(parse_frontmatter(result).metadata) == "machine-confirmed"


@pytest.mark.anyio
async def test_mcp_forwards_repair(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "note.md").write_text(NOTE)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_AUTHOR", "true")
    async with Client(make_server()) as client:
        etag = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))[
            "etag"
        ]
        await client.call_tool(
            "okf_author",
            {
                "path": "note.md",
                "if_match": etag,
                "fields": {},
                "repair": {"retire_reviews": {"indices": [0], "reason": "correction"}},
            },
        )
    meta = parse_frontmatter((tmp_path / "note.md").read_text()).metadata
    assert meta["x-mvmcp"]["review_history"][0]["reason"] == "correction"
