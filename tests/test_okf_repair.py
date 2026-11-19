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
            "retire_reviews": {"indices": [0], "reason": "superseded"},
            "replace_generated": {"by": "new/2", "reason": "correction"},
        },
    )
    before = parse_frontmatter(text).metadata
    after = parse_frontmatter(result).metadata
    assert after["verified"] == before["verified"][1:]
    assert after["x-mvmcp"]["review_history"] == [
        {"review": before["verified"][0], "reason": "superseded"}
    ]
    assert after["x-mvmcp"]["generated_history"] == [
        {"generated": before["generated"], "reason": "correction"}
    ]
    assert after["x-mvmcp"]["reviews"] == before["x-mvmcp"]["reviews"]
    assert after["x-mvmcp"]["other"] == before["x-mvmcp"]["other"]
    assert after["generated"]["by"] == "new/2"
    assert result.count("0.1234567890123456789") == 4
    assert "2026-01-01T00:00:00.123456789Z" in result
    assert (
        "  - {by: 'process:two', at: 2026-01-02T00:00:00.123456789Z}  # keep" in result
    )


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


def test_append_to_existing_block_and_flow_histories() -> None:
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


@pytest.mark.parametrize(
    "second_time", ["2026-01-01T00:00:00Z", "2025-12-31T16:00:00-08:00"]
)
def test_partial_retirement_of_shared_identity_refused(second_time: str) -> None:
    text = f"---\nverified: [{{by: 'human:a', at: '2026-01-01T00:00:00Z'}}, {{by: 'human:a', at: '{second_time}'}}]\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="identity"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "r"}}
        )


@pytest.mark.parametrize(
    "note",
    ["    at: '2026-01-01T00:00:00Z'", "    note: value  # inline\n  # trailing"],
)
def test_retire_all_block_reviews_before_next_field(note: str) -> None:
    text = f"---\nverified:\n  - by: human:a\n{note}\nother: keep\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
    )
    meta = parse_frontmatter(result).metadata
    assert meta["verified"] == []
    assert meta["other"] == "keep"
    if "# inline" in note:
        assert "# inline" in result and "# trailing" in result


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
    ("text", "indices", "message"),
    [
        (NOTE.replace("other: keep", "other: keep\n  other: lost"), [0], None),
        (NOTE.replace("other: keep", "review_history: broken"), [0], None),
        ("---\nverified: [{by: a, by: b}]\n---\nBody\n", [0], None),
        ("---\nother: &a {by: human:a}\nverified: [*a]\n---\nBody\n", [0], "alias"),
        (
            "---\nother: &x {}\nverified: [{by: human:a}]\nx-mvmcp: *x\n---\nBody\n",
            [0],
            None,
        ),
        (
            "---\nverified:\n  - &review {by: 'human:a', at: yesterday}\n  - *review\n---\nBody\n",
            [1],
            "alias",
        ),
    ],
)
def test_refuses_unsafe_existing_evidence(
    text: str, indices: list[int], message: str | None
) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        author_text(
            text,
            {},
            None,
            repair={"retire_reviews": {"indices": indices, "reason": "fix"}},
        )


def test_retired_bindings_do_not_attach_to_later_reviews(tmp_path) -> None:
    from markdown_vault_mcp._okf_receipts import ReceiptStore, assess_reviews
    from markdown_vault_mcp._okf_review import bind_review, concept_snapshot

    text = "---\ntype: concept\n---\nBody\n"
    entry = {"by": "human:alice", "at": "2026-01-01T00:00:00Z"}
    snapshot = concept_snapshot(text)
    receipts = [{**entry, "check": "schema", "snapshot": snapshot}]
    store = ReceiptStore(None, tmp_path)
    store.append({**receipts[0], "path": "note.md"})
    text = bind_review(text, entry, receipts[0])
    for check in ("links", "source-comparison"):
        text = author_text(
            text,
            {},
            None,
            repair={"retire_reviews": {"indices": [0], "reason": "retired"}},
        )
        metadata = parse_frontmatter(text).metadata
        assert metadata["x-mvmcp"]["review_history"][-1]["binding"] == receipts[-1]
        claim = {**entry, "check": check, "snapshot": snapshot}
        receipts.append(claim)
        store.append({**claim, "path": "note.md"})
        text = bind_review(text, entry, claim)
        meta = parse_frontmatter(text).metadata
        items = assess_reviews(meta, snapshot, lambda: store.receipts("note.md"))
        assert [(item["check"], item["origin"]) for item in items] == [
            (check, "local_receipt")
        ]
        assert assess_reviews(meta, snapshot, lambda: [])[0]["origin"] == "supplied"
    assert len(receipts) == 3
    assert len(store.receipts("note.md")) == 3


def test_retire_all_shared_identity_archives_claims_once() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}, {by: 'human:a', at: '2026-01-01T00:00:00Z'}]\nx-mvmcp: {reviews: [{by: 'human:a', at: '2026-01-01T00:00:00Z', check: links, amount: 0.1234567890123456789}]}\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="identity"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
        )
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0, 1], "reason": "fix"}}
    )
    ext = parse_frontmatter(result).metadata["x-mvmcp"]
    assert ext["reviews"] == []
    assert len(ext["review_history"]) == 3
    assert sum("binding" in item for item in ext["review_history"]) == 1
    assert "0.1234567890123456789" in result


def test_retirement_indices_include_malformed_siblings() -> None:
    text = "---\nverified: [broken, {by: 'human:a', at: '2026-01-01T00:00:00Z'}]\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [1], "reason": "fix"}}
    )
    assert parse_frontmatter(result).metadata["verified"] == ["broken"]


@pytest.mark.parametrize("target", ["verified", "generated", "x-mvmcp"])
@pytest.mark.parametrize("action", ["retire_reviews", "replace_generated"])
def test_merge_keys_refused_for_repairs(target: str, action: str) -> None:
    fields = {
        "verified": "[{by: 'human:a', at: '2026-01-01T00:00:00Z'}]",
        "generated": "{by: old/1}",
        "x-mvmcp": "{}",
    }
    inherited = fields.pop(target)
    text = (
        "---\nbase: &base {"
        + target
        + ": "
        + inherited
        + "}\n<<: *base\n"
        + "".join(f"{key}: {value}\n" for key, value in fields.items())
        + "---\nBody\n"
    )
    request = (
        {"indices": [0], "reason": "fix"}
        if action == "retire_reviews"
        else {"by": "new/1", "reason": "fix"}
    )
    with pytest.raises(InvalidRequestError, match="merge"):
        author_text(text, {}, None, repair={action: request})


def test_retirement_preserves_unmatched_and_malformed_claims() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}]\nx-mvmcp: {reviews: [broken, {by: 'human:b', at: '2026-01-01T00:00:00Z', check: other}, {by: 'human:a', at: '2026-01-01T00:00:00Z', check: links}]}\n---\nBody\n"
    result = author_text(
        text, {}, "tool/1", repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
    )
    meta = parse_frontmatter(result).metadata
    assert meta["generated"]["by"] == "tool/1"
    assert len(meta["x-mvmcp"]["reviews"]) == 2
    assert meta["x-mvmcp"]["reviews"][0] == "broken"
    assert meta["x-mvmcp"]["review_history"][1]["binding"]["check"] == "links"


def test_preserves_alias_in_unselected_bindings() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}]\nx-mvmcp:\n  reviews:\n    - &claim {by: 'human:b', at: '2026-01-01T00:00:00Z'}\n    - *claim\n    - {by: 'human:a', at: '2026-01-01T00:00:00Z', check: links}\n---\nBody\n"
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
    )
    assert "*claim" in result


def test_refuses_alias_of_selected_binding() -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}]\nx-mvmcp:\n  reviews:\n    - &claim {by: 'human:a', at: '2026-01-01T00:00:00Z', check: links}\n    - *claim\n---\nBody\n"
    with pytest.raises(InvalidRequestError, match="alias"):
        author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
        )


@pytest.mark.parametrize("action", ["retire_reviews", "replace_generated"])
def test_merge_key_in_extension_is_refused(action: str) -> None:
    text = "---\nverified: [{by: 'human:a', at: '2026-01-01T00:00:00Z'}]\ngenerated: {by: old/1}\nx-mvmcp:\n  defaults: &defaults {reviews: []}\n  <<: *defaults\n---\nBody\n"
    request = (
        {"indices": [0], "reason": "fix"}
        if action == "retire_reviews"
        else {"by": "new/1", "reason": "fix"}
    )
    with pytest.raises(InvalidRequestError, match="merge"):
        author_text(text, {}, None, repair={action: request})


@pytest.mark.parametrize("layout", ["flow", "indented", "flat"])
@pytest.mark.parametrize("indices", [[0], [0, 1]])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_review_and_binding_sequence_layouts(
    layout: str, indices: list[int], newline: str
) -> None:
    from markdown_vault_mcp.okf import derive_trust_tier

    review_a = "{by: 'human:a', at: '2026-01-01T00:00:00Z'}"
    review_b = "{by: 'process:b', at: '2026-01-02T00:00:00Z'}"

    def render(key: str, rows: list[str], column: int) -> str:
        prefix = " " * column
        if layout == "flow":
            return prefix + key + ": [" + ", ".join(rows) + "]\n"
        indent = prefix + ("  " if layout == "indented" else "")
        return (
            prefix + key + ":\n" + "".join(indent + "- " + row + "\n" for row in rows)
        )

    review = render("verified", [review_a, review_b], 0)
    claims = render("reviews", [review_a, review_b], 2)
    text = ("---\n" + review + "x-mvmcp:\n" + claims + "---\nBody\n").replace(
        "\n", newline
    )
    result = author_text(
        text, {}, None, repair={"retire_reviews": {"indices": indices, "reason": "fix"}}
    )
    meta = parse_frontmatter(result).metadata
    assert len(meta["verified"]) == 2 - len(indices)
    assert len(meta["x-mvmcp"]["reviews"]) == 2 - len(indices)
    assert len(meta["x-mvmcp"]["review_history"]) == 2 * len(indices)
    assert derive_trust_tier(meta) == (
        "unverified" if len(indices) == 2 else "machine-confirmed"
    )
    if newline == "\r\n":
        assert "\n" not in result.replace("\r\n", "")


def test_library_write_then_retire_all_reviews(tmp_path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.write(
            "note.md",
            "Body\n",
            frontmatter={"verified": [{"by": "human:a", "at": "2026-01-01T00:00:00Z"}]},
        )
        note = vault.reader.read("note.md")
        assert note is not None
        vault.writer.okf_author(
            "note.md",
            if_match=note.etag,
            fields={},
            repair={"retire_reviews": {"indices": [0], "reason": "fix"}},
        )
        updated = vault.reader.read("note.md")
        assert updated is not None
        assert (
            parse_frontmatter((tmp_path / "note.md").read_text()).metadata["verified"]
            == []
        )
    finally:
        vault.close()


@pytest.mark.parametrize("retire", [True, False])
def test_review_can_follow_block_claims_with_sibling(retire: bool) -> None:
    from markdown_vault_mcp._okf_review import bind_review

    snapshot = {"algo": "mvmcp-concept-v1", "digest": "0" * 64}
    text = (
        "---\ntype: concept\nverified:\n"
        "  - {by: 'human:a', at: '2026-01-01T00:00:00Z'}\n"
        "  - {by: 'human:b', at: '2026-01-02T00:00:00Z'}\n"
        "x-mvmcp:\n  reviews:\n"
        "    - {by: 'human:a', at: '2026-01-01T00:00:00Z', check: schema}\n"
        "    - {by: 'human:b', at: '2026-01-02T00:00:00Z', check: links}\n"
        + ("" if retire else "  other: keep\n")
        + "---\nBody\n"
    )
    if retire:
        text = author_text(
            text, {}, None, repair={"retire_reviews": {"indices": [0], "reason": "fix"}}
        )
    entry = {"by": "human:c", "at": "2026-01-03T00:00:00Z"}
    result = bind_review(
        text, entry, {**entry, "check": "schema", "snapshot": snapshot}
    )
    meta = parse_frontmatter(result).metadata
    assert meta["verified"][-1] == entry
    assert meta["x-mvmcp"]["reviews"][-1]["check"] == "schema"
    if retire:
        assert len(meta["x-mvmcp"]["review_history"]) == 2
    else:
        assert meta["x-mvmcp"]["other"] == "keep"
