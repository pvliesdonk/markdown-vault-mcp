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
        NOTE.replace("review_history", "unused").replace(
            "other: keep", "review_history: broken"
        ),
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


def test_repair_preserves_precision_in_all_evidence() -> None:
    text = """---
generated: {by: old/1, weight: 0.1234567890123456789}
verified:
  - {by: 'human:one', at: 2026-01-01T00:00:00.123456789Z, value: 0.1234567890123456789}
  - {by: 'process:two', at: 2026-01-02T00:00:00.123456789Z}  # keep
x-mvmcp:
  other: 0.1234567890123456789
  reviews: [{value: 0.1234567890123456789}]
---
Body
"""
    result = author_text(
        text,
        {},
        None,
        repair={
            "retire_reviews": {"indices": [0], "reason": "r"},
            "replace_generated": {"by": "new/1", "reason": "r"},
        },
    )
    assert result.count("0.1234567890123456789") == 4
    assert "2026-01-01T00:00:00.123456789Z" in result
    assert (
        "  - {by: 'process:two', at: 2026-01-02T00:00:00.123456789Z}  # keep" in result
    )
    again = author_text(
        result, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "again"}}
    )
    assert again.count("0.1234567890123456789") == 4


def test_refuse_partial_retirement_of_duplicate_identity() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}, {by: 'human:a', at: '2026-01-01T00:00:00Z'}]\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="identity"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "r"}}
        )


def test_replacement_rejects_invalid_actor_and_repair_type() -> None:
    with pytest.raises(InvalidRequestError):
        author_text(NOTE, {}, "tool/1", repair=123)  # type: ignore[arg-type]


def test_flow_and_block_histories_keep_raw_precision() -> None:
    text = """---
generated:
  by: old/1
  amount: 0.1234567890123456789
verified: [{by: 'human:a', at: '2026-01-01T00:00:00Z', amount: 0.1234567890123456789}, {by: 'process:b', at: '2026-01-02T00:00:00Z'}]
x-mvmcp:
  review_history:
    - {review: {amount: 0.1234567890123456789}, reason: old}
  generated_history: [{generated: {amount: 0.1234567890123456789}, reason: old}]
---
Body
"""
    result = author_text(
        text,
        {},
        None,
        repair={
            "retire_reviews": {"indices": [0], "reason": "r"},
            "replace_generated": {"by": "new/1", "reason": "r"},
        },
    )
    assert result.count("0.1234567890123456789") == 4
    assert "{by: 'process:b', at: '2026-01-02T00:00:00Z'}" in result
    assert len(parse_frontmatter(result).metadata["x-mvmcp"]["review_history"]) == 2


def test_duplicate_identity_with_equivalent_offset_refused() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}, {by: 'human:a', at: '2025-12-31T16:00:00-08:00'}]\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="identity"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "r"}}
        )


def test_retire_all_block_reviews_before_next_field() -> None:
    text = "---\nverified:\n  - by: 'human:a'\n    at: '2026-01-01T00:00:00Z'\nother: keep\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "r"}}
    )
    meta = parse_frontmatter(result).metadata
    assert meta["verified"] == []
    assert meta["other"] == "keep"


@pytest.mark.parametrize("note", ["x, y", "x]", "line one\n      line two"])
def test_archive_flow_sensitive_strings(note: str) -> None:
    value = "|\n      " + note if "\n" in note else note
    text = f"---\ngenerated:\n  by: old/1\n  note: {value}\nverified:\n  - by: human:a\n    note: {value}\n---\nBody\n"
    original = parse_frontmatter(text).metadata
    result = author_text(
        text,
        {},
        None,
        repair={
            "retire_reviews": {"indices": [0], "reason": "fix"},
            "replace_generated": {"by": "new/1", "reason": "fix"},
        },
    )
    ext = parse_frontmatter(result).metadata["x-mvmcp"]
    assert ext["review_history"][0]["review"] == original["verified"][0]
    assert ext["generated_history"][0]["generated"] == original["generated"]


@pytest.mark.parametrize(
    "extra", ["custom: {2026: annual}", "custom: &v {hello: world}\nother: *v"]
)
def test_unrelated_metadata_does_not_block_repair(extra: str) -> None:
    text = f"---\ngenerated: old\nverified: [malformed]\n{extra}\n---\nBody\n"
    result = author_text(
        text,
        {},
        None,
        repair={
            "retire_reviews": {"indices": [0], "reason": "fix"},
            "replace_generated": {"by": "new/1", "reason": "fix"},
        },
    )
    meta = parse_frontmatter(result).metadata
    assert meta["x-mvmcp"]["review_history"][0]["review"] == "malformed"
    assert meta["x-mvmcp"]["generated_history"][0]["generated"] == "old"
    assert meta["custom"] == parse_frontmatter(text).metadata["custom"]


def test_distinct_invalid_review_times_do_not_collide() -> None:
    text = "---\nverified: [{by: 'human:a', at: yesterday}, {by: 'human:a', at: today}]\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
    )
    assert len(parse_frontmatter(result).metadata["verified"]) == 1


@pytest.mark.parametrize("actor", ["org/team/1.0", "a:b/c"])
def test_replacement_accepts_original_actor_grammar(actor: str) -> None:
    result = author_text(
        NOTE, {}, None, repair={"replace_generated": {"by": actor, "reason": "fix"}}
    )
    assert parse_frontmatter(result).metadata["generated"]["by"] == actor


@pytest.mark.parametrize(
    "text",
    [
        "---\nverified: [{by: a, by: b}]\n---\nBody\n",
        "---\nother: &a {by: human:a}\nverified: [*a]\n---\nBody\n",
        "---\nother: &x {}\nverified: [{by: human:a}]\nx-mvmcp: *x\n---\nBody\n",
    ],
)
def test_refuse_ambiguous_touched_evidence(text: str) -> None:
    with pytest.raises(InvalidRequestError):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
        )


def test_trailing_review_comment_survives_retirement() -> None:
    text = "---\nverified:\n  - by: human:a\n    note: value  # inline\n  # trailing\nother: keep\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
    )
    assert "# inline" in result and "# trailing" in result
    assert parse_frontmatter(result).metadata["other"] == "keep"


def test_refuse_selected_alias_of_an_earlier_review() -> None:
    text = "---\nverified:\n  - &review {by: 'human:a', at: yesterday}\n  - *review\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="alias"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [1], "reason": "fix"}}
        )
