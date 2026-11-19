"""Evidence selectors operate on supplied OKF metadata."""

from pathlib import Path
from typing import Any, Literal

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider


@pytest.fixture
def evidence_vault(tmp_path: Path):
    notes = {
        "index.md": 'okf_version: "0.2"',
        "log.md": "",
        "missing.md": "tags: [keep]",
        "empty.md": "verified: []",
        "malformed.md": "stale_after: invalid\nverified: broken",
        "future.md": "stale_after: 2999-01-01\nverified: [{by: human:alice, at: 2026-01-01T00:00:00Z}]\ntags: [keep]",
        "mixed.md": "stale_after: 2000-01-01\nverified: [{by: human:alice, at: 2026-01-01T00:00:00Z}, {by: bad}]",
        "past.md": "stale_after: 2000-01-01\nverified: [{by: process:bot, at: 2026-01-01T00:00:00Z}]",
    }
    for path, frontmatter in notes.items():
        (tmp_path / path).write_text(f"---\n{frontmatter}\n---\nzebra\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(indexed_frontmatter_fields=["tags"]),
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
        ({"freshness": "expired"}, {"past.md", "mixed.md"}),
        ({"review": "none"}, {"missing.md", "empty.md"}),
        ({"review": "malformed"}, {"malformed.md"}),
        ({"review": "recorded"}, {"future.md", "past.md", "mixed.md"}),
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


def test_composition(evidence_vault: Vault) -> None:
    results = evidence_vault.reader.search(
        "zebra",
        mode="keyword",
        purpose="historical",
        filters={"freshness": "unexpired", "review": "recorded", "tags": "keep"},
    )
    assert [r.path for r in results] == ["future.md"]
    results = evidence_vault.reader.search(
        "zebra",
        mode="keyword",
        filters={"review": "recorded", "stale": "true", "trust_tier": "human-reviewed"},
    )
    assert [r.path for r in results] == ["mixed.md"]


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


class _OrderedProvider(MockEmbeddingProvider):
    """Synthetic vectors put the selected note behind all other notes."""

    def __init__(self) -> None:
        super().__init__(dim=2)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [0.8, 0.6] if "selectedmarker" in text else [1.0, 0.0] for text in texts
        ]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)


@pytest.fixture
def crowded_vault(tmp_path: Path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nbundle')
    (tmp_path / "reference.md").write_text("reference zebra")
    for i in range(205):
        (tmp_path / f"near{i:03}.md").write_text(
            "---\nstale_after: 2000-01-01\nverified: []\n---\nzebra near"
        )
    (tmp_path / "selected.md").write_text(
        "---\nstale_after: 2999-01-01\nverified: [{by: human:alice, at: 2026-01-01T00:00:00Z}]\n---\nzebra selectedmarker"
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(embeddings_path=tmp_path / "vectors"),
        embedding_provider=_OrderedProvider(),
    )
    vault.index.build_index()
    vault.index.build_embeddings()
    yield vault
    vault.close()


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_evidence_reaches_beyond_vector_cap(
    crowded_vault: Vault,
    mode: Literal["keyword", "semantic", "hybrid"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    manager = crowded_vault._search_mgr
    initial = manager._fts.search(
        "zebra", limit=300, filters=None, folder=None, snippet_words=None
    )
    near = next(row for row in initial if row.path.startswith("near"))
    selected = next(row for row in initial if row.path == "selected.md")
    fts_rows = [replace(near, score=10.0)] * 4100 + [selected]
    vector_rows: list[dict[str, Any]] = [
        {
            "path": row.path,
            "title": row.title,
            "folder": row.folder,
            "heading": row.heading,
            "content": row.content,
            "score": 1.0,
            "start_line": row.start_line,
        }
        for row in fts_rows
    ]
    fts_caps: list[int] = []
    vector_caps: list[int] = []

    def fts_search(_query: str, *, limit: int, **_kwargs: Any) -> Any:
        fts_caps.append(limit)
        return fts_rows[:limit]

    def vector_search(
        _query: str, limit: int, _folder: str | None, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        vector_caps.append(limit)
        return vector_rows[:limit]

    monkeypatch.setattr(manager._fts, "search", fts_search)
    monkeypatch.setattr(manager, "_search_vectors", vector_search)
    filters = {"freshness": "unexpired", "review": "recorded"}
    results = crowded_vault.reader.search("zebra", mode=mode, limit=1, filters=filters)
    assert [row.path for row in results] == ["selected.md"]
    if mode in ("keyword", "hybrid"):
        assert len(fts_caps) > 1 and fts_caps[-1] > 4100
    if mode in ("semantic", "hybrid"):
        assert len(vector_caps) > 1 and vector_caps[-1] > 4100
    assert [
        row.path
        for row in crowded_vault.reader.get_similar(
            "reference.md", limit=1, filters=filters
        )
    ] == ["selected.md"]


def test_active_bundle_reserves_evidence_keys_but_exact_queries_use_raw_fields(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.query_types import NoteQuery

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nbundle')
    (tmp_path / "note.md").write_text(
        "---\nfreshness: custom\nreview: custom\n---\nzebra"
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(indexed_frontmatter_fields=["freshness", "review"]),
    )
    vault.index.build_index()
    try:
        for key in ("freshness", "review"):
            with pytest.raises(ValueError, match=key + " filter"):
                vault.reader.search("zebra", mode="keyword", filters={key: "custom"})
            page = vault.reader.query_documents(
                NoteQuery(query="zebra", filters={key: "custom"})
            )
            assert [item["path"] for item in page.items] == ["note.md"]
    finally:
        vault.close()


@pytest.mark.parametrize("tool", ["search", "list_documents", "get_similar"])
async def test_evidence_filters_forwarded_by_tools(
    evidence_vault: Vault, tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from fastmcp import Client, FastMCP

    from markdown_vault_mcp._tools import reader
    from tests.conftest import _parse_tool_data

    monkeypatch.setattr(reader, "get_vault", lambda: evidence_vault)
    owner = (
        Mock(return_value=[])
        if tool == "get_similar"
        else Mock(wraps=getattr(evidence_vault.reader, tool))
    )
    monkeypatch.setattr(evidence_vault.reader, tool, owner)
    server = FastMCP("evidence-forwarding-test")
    reader.register(server)
    args: dict = {"filters": {"freshness": "unexpired", "review": "recorded"}}
    if tool == "search":
        args.update(query="zebra", mode="keyword")
    if tool == "get_similar":
        args["path"] = "future.md"
    async with Client(server) as client:
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert [row["path"] for row in result] == (
            [] if tool == "get_similar" else ["future.md"]
        )
    assert owner.call_args.kwargs["filters"] == args["filters"]
