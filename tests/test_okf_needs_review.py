"""Review selectors use freshness and bindings independently."""

from pathlib import Path

from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_search_finds_expired_even_with_matching_human_review(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    original = (
        "---\ntype: instruction\nstale_after: 2000-01-01\n---\nzebra instructions\n"
    )
    (tmp_path / "expired.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_verify("expired.md", "alice", concept_snapshot(original))
        vault.index.build_index()
        hits = vault.reader.search(
            "zebra", mode="keyword", filters={"needs_review": "true"}
        )
        assert [hit.path for hit in hits] == ["expired.md"]
        assert hits[0].needs_review["conditions"] == ["freshness_expired"]
    finally:
        vault.close()


def test_review_conditions_and_check_selection(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    originals = {
        "fresh.md": "---\ntype: instruction\nstale_after: 2999-01-01\n---\nzebra\n",
        "schema.md": "---\ntype: instruction\nstale_after: 2999-01-01\n---\nzebra\n",
        "missing.md": "---\ntype: instruction\n---\nzebra\n",
        "bad.md": "---\ntype: instruction\nstale_after: nonsense\n---\nzebra\n",
    }
    for name, content in originals.items():
        (tmp_path / name).write_text(content)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_verify(
            "fresh.md", "alice", concept_snapshot(originals["fresh.md"])
        )
        vault.writer.okf_verify(
            "schema.md",
            "scanner",
            concept_snapshot(originals["schema.md"]),
            check="schema",
        )
        vault.index.build_index()
        hits = vault.reader.search(
            "zebra", mode="keyword", filters={"needs_review": "true"}
        )
        reasons = {hit.path: hit.needs_review["conditions"] for hit in hits}
        assert reasons == {
            "schema.md": ["no_matching_review"],
            "missing.md": ["freshness_missing", "no_matching_review"],
            "bad.md": ["freshness_malformed", "no_matching_review"],
        }
        hits = vault.reader.search(
            "zebra", mode="keyword", filters={"needs_review": "false"}
        )
        assert [hit.path for hit in hits] == ["fresh.md"]
        hits = vault.reader.search(
            "zebra",
            mode="keyword",
            filters={"needs_review": "false", "required_check": "schema"},
        )
        assert [hit.path for hit in hits] == ["schema.md"]
        (tmp_path / "fresh.md").write_text(
            (tmp_path / "fresh.md").read_text() + "changed\n"
        )
        hits = vault.reader.search(
            "zebra", mode="keyword", filters={"needs_review": "true"}
        )
        assert "fresh.md" in [hit.path for hit in hits]
    finally:
        vault.close()


def test_invalid_filters_are_rejected_before_search(tmp_path: Path) -> None:
    import pytest

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        with pytest.raises(ValueError, match="needs_review filter"):
            vault.reader.search(
                "absent", mode="keyword", filters={"needs_review": "maybe"}
            )
        for filters in (
            {"needs_review": "maybe"},
            {"required_check": "schema"},
            {"needs_review": "true", "required_check": "bogus"},
        ):
            with pytest.raises(ValueError):
                vault.reader.search("absent", mode="keyword", filters=filters)
    finally:
        vault.close()


def test_listing_and_all_search_modes_report_conditions(tmp_path: Path) -> None:
    from tests.conftest import MockEmbeddingProvider

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nzebra')
    (tmp_path / "instruction.md").write_text("---\ntype: instruction\n---\nzebra")
    (tmp_path / "reference.md").write_text("reference")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(embeddings_path=tmp_path / "vectors"),
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        for mode in ("keyword", "semantic", "hybrid"):
            hits = vault.reader.search(
                "zebra",
                mode=mode,
                filters={"needs_review": "true", "type": "instruction"},
            )
            assert [hit.path for hit in hits] == ["instruction.md"]
            assert hits[0].needs_review["conditions"] == [
                "freshness_missing",
                "no_matching_review",
            ]
        notes = vault.reader.list_documents(filters={"needs_review": "true"})
        assert {note.path for note in notes} == {"instruction.md", "reference.md"}
        assert all(
            note.needs_review["required_check"] == "human-review" for note in notes
        )
        similar = vault.reader.get_similar(
            "reference.md", filters={"needs_review": "true"}
        )
        assert [hit.path for hit in similar] == ["instruction.md"]
        assert similar[0].needs_review["conditions"] == [
            "freshness_missing",
            "no_matching_review",
        ]
    finally:
        vault.close()


async def test_mcp_results_include_review_conditions(
    tmp_path: Path, monkeypatch
) -> None:
    from fastmcp import Client, FastMCP

    from markdown_vault_mcp._tools import reader
    from tests.conftest import MockEmbeddingProvider, _parse_tool_data

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text("---\ntype: instruction\n---\nzebra")
    (tmp_path / "reference.md").write_text("reference")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(embeddings_path=tmp_path / "vectors"),
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        monkeypatch.setattr(reader, "get_vault", lambda: vault)
        server = FastMCP("triage")
        reader.register(server)
        async with Client(server) as client:
            for tool, extra in (
                ("search", {"query": "zebra", "mode": "keyword"}),
                ("list_documents", {}),
                ("get_similar", {"path": "reference.md"}),
            ):
                result = _parse_tool_data(
                    await client.call_tool(
                        tool,
                        {
                            **extra,
                            "filters": {"needs_review": "true", "type": "instruction"},
                        },
                    )
                )
                assert [hit["path"] for hit in result] == ["note.md"]
                assert result[0]["needs_review"]["conditions"] == [
                    "freshness_missing",
                    "no_matching_review",
                ]
    finally:
        vault.close()
