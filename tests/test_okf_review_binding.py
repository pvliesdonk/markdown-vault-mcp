"""Review bindings retain portable metadata and the reviewed bytes."""

from datetime import UTC, datetime

import frontmatter

from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.okf import append_okf_verification


def test_review_binding_preserves_body_and_unrelated_header() -> None:
    original = "---\r\ntype: concept # keep\r\ntitle: 'A'\r\n---\r\n\r\nBody\r\n"
    snapshot = concept_snapshot(original)
    result = append_okf_verification(
        original,
        subject="alice",
        now=datetime(2026, 1, 2, tzinfo=UTC),
        reviewed_snapshot=snapshot,
        check="human-review",
    )
    metadata = frontmatter.loads(result).metadata
    assert metadata["verified"] == [{"by": "human:alice", "at": "2026-01-02T00:00:00Z"}]
    assert metadata["x-mvmcp"]["reviews"] == [
        {
            "by": "human:alice",
            "at": "2026-01-02T00:00:00Z",
            "check": "human-review",
            "snapshot": snapshot,
        }
    ]
    assert "type: concept # keep\r\ntitle: 'A'\r\n" in result
    assert result.endswith("---\r\n\r\nBody\r\n")
    assert concept_snapshot(result) == snapshot
