"""Evidence selectors operate on supplied OKF metadata."""

from pathlib import Path
from typing import Literal

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.fixture
def evidence_vault(tmp_path: Path):
    notes = {
        "index.md": 'okf_version: "0.2"',
        "log.md": "",
        "missing.md": "tags: [keep]",
        "empty.md": "verified: []",
        "malformed.md": "stale_after: invalid\nverified: broken",
        "future.md": "stale_after: 2999-01-01\nverified: [{by: human:alice, at: 2026-01-01T00:00:00Z}]\ntags: [keep]",
        "past.md": "stale_after: 2000-01-01\nverified: [{by: process:bot, at: 2026-01-01T00:00:00Z}]",
    }
    for path, frontmatter in notes.items():
        (tmp_path / path).write_text(f"---\n{frontmatter}\n---\nzebra\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            indexed_frontmatter_fields=["tags", "freshness", "review"]
        ),
    )
    vault.index.build_index()
    yield vault
    vault.close()


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"freshness": "missing"}, {"missing.md", "empty.md"}),
        ({"freshness": "malformed"}, {"malformed.md"}),
        ({"freshness": "unexpired"}, {"future.md"}),
        ({"freshness": "expired"}, {"past.md"}),
        ({"review": "none"}, {"missing.md", "empty.md"}),
        ({"review": "malformed"}, {"malformed.md"}),
        ({"review": "recorded"}, {"future.md", "past.md"}),
    ],
)
def test_evidence_states(
    evidence_vault: Vault, filters: dict[str, str], expected: set[str]
) -> None:
    assert {
        r.path
        for r in evidence_vault.reader.search("zebra", mode="keyword", filters=filters)
    } == expected
    assert {
        r.path for r in evidence_vault.reader.list_documents(filters=filters)
    } == expected


@pytest.mark.parametrize("purpose", ["current", "historical", "review"])
def test_composition(
    evidence_vault: Vault, purpose: Literal["current", "historical", "review"]
) -> None:
    results = evidence_vault.reader.search(
        "zebra",
        mode="keyword",
        purpose=purpose,
        filters={"freshness": "unexpired", "review": "recorded", "tags": "keep"},
    )
    assert [r.path for r in results] == ["future.md"]


@pytest.mark.parametrize("filters", [{"freshness": "unknown"}, {"review": "empty"}])
def test_invalid_before_empty_search(
    evidence_vault: Vault, filters: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="filter"):
        evidence_vault.reader.search("wordnotpresent", mode="keyword", filters=filters)


def test_inactive_bundle_uses_plain_frontmatter(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "---\nfreshness: unusual\nreview: custom\n---\nzebra"
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            indexed_frontmatter_fields=["tags", "freshness", "review"]
        ),
    )
    vault.index.build_index()
    try:
        assert [
            r.path
            for r in vault.reader.search(
                "zebra",
                mode="keyword",
                filters={"freshness": "unusual", "review": "custom"},
            )
        ] == ["note.md"]
    finally:
        vault.close()
