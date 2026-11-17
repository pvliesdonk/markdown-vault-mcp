"""Missing-type filtering covers the indexed note population."""

from pathlib import Path
from typing import Any, Literal

import pytest
from fastmcp import Client

from tests.conftest import _CLEAR_VARS, _parse_tool_data, wait_for_mcp_writer_drain
from tests.server_factory import make_server


@pytest.mark.parametrize("tool", ["search", "list_documents"])
async def test_missing_type_filter(
    tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only admitted non-reserved notes without a non-empty type match."""
    notes = {
        "index.md": '---\nokf_version: "0.2"\n---\nZebra',
        "log.md": "Zebra",
        "nested/index.md": "Zebra",
        "absent.md": "Zebra",
        "null.md": "---\ntype: null\n---\nZebra",
        "empty.md": '---\ntype: ""\n---\nZebra',
        "spaces.md": '---\ntype: "  "\n---\nZebra',
        "number.md": "---\ntype: 42\n---\nZebra",
        "typed.md": "---\ntype: Playbook\n---\nZebra",
        "excluded.md": "Zebra",
    }
    for path, content in notes.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "excluded.md")
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        args: dict[str, Any] = {"filters": {"type": None}}
        if tool == "search":
            args.update(query="zebra", mode="keyword", limit=50)
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert {row["path"] for row in result} == {
            "absent.md",
            "null.md",
            "empty.md",
            "spaces.md",
            "number.md",
        }
        args["filters"] = {"type": "Playbook"}
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert [row["path"] for row in result] == ["typed.md"]
        args["filters"] = {"type": "42"}
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert [row["path"] for row in result] == ["number.md"]


@pytest.mark.parametrize("crowded", [True, False])
@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_missing_type_search_reaches_beyond_candidate_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["keyword", "semantic", "hybrid"],
    crowded: bool,
) -> None:
    """Typed hits cannot crowd the eligible notes out of either search channel."""
    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.scanner import scan_directory
    from tests.conftest import MockEmbeddingProvider

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex')
    for i in range(300):
        (tmp_path / f"typed{i:03}.md").write_text(
            "---\ntype: Playbook\ntags: [keep]\n---\n" + "zebra " * 20
        )
    (tmp_path / "missing.md").write_text("---\ntags: [keep]\n---\nzebra")
    (tmp_path / "other.md").write_text("---\ntags: [other]\n---\nzebra")
    fts = FTSIndex(db_path=":memory:", indexed_frontmatter_fields=["type", "tags"])
    for note in scan_directory(tmp_path):
        fts.upsert_note(note)
    mgr = SearchManager(
        fts=fts,
        source_dir=tmp_path,
        indexed_frontmatter_fields=["type", "tags"],
        okf_detector=OkfDetector(tmp_path),
        embedding_provider=MockEmbeddingProvider(),
    )
    paths = [f"typed{i % 300:03}.md" for i in range(4100)] + ["other.md", "missing.md"]
    if not crowded:
        paths.insert(0, paths.pop())
    ranking = [
        {
            "path": path,
            "title": path,
            "folder": "",
            "heading": None,
            "content": "zebra",
            "score": 1 - i / 10000,
            "start_line": 1,
        }
        for i, path in enumerate(paths)
    ]
    calls: list[int] = []

    def fake_vectors(
        _query: str, limit: int, _folder: str | None
    ) -> list[dict[str, Any]]:
        calls.append(limit)
        return ranking[:limit]

    monkeypatch.setattr(mgr, "_search_vectors", fake_vectors)
    results = mgr.search(
        "zebra", mode=mode, limit=1, filters={"type": None, "tags": "keep"}
    )
    assert [row.path for row in results] == ["missing.md"]
    if mode != "keyword":
        assert len(calls) == (2 if crowded else 1)


@pytest.mark.parametrize(
    "active,filters",
    [
        (False, {"type": None}),
        (True, {"tags": None}),
        (True, {"type": None, "stale": None}),
    ],
)
def test_unsupported_null_is_rejected(
    tmp_path: Path, active: bool, filters: dict[str, str | None]
) -> None:
    """Only null type on an active bundle has missing-type semantics."""
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.okf import OkfDetector

    mgr = SearchManager(
        fts=FTSIndex(db_path=":memory:"),
        source_dir=tmp_path,
        okf_detector=OkfDetector(tmp_path, mode="on" if active else "off"),
    )
    with pytest.raises(InvalidRequestError, match="null is supported only for type"):
        mgr.search("zebra", mode="keyword", filters=filters)
    with pytest.raises(InvalidRequestError, match="null is supported only for type"):
        mgr.list(filters=filters)
