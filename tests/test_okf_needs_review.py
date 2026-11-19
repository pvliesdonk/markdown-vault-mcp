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
        for filters, message in (
            ({"needs_review": "maybe"}, "needs_review filter"),
            ({"required_check": "schema"}, "required_check"),
            ({"needs_review": "true", "required_check": "bogus"}, "Unknown check"),
            ({"needs_review": True}, "needs_review filter"),
            ({"needs_review": "true", "required_check": 3}, "Unknown check"),
        ):
            with pytest.raises(ValueError, match=message):
                vault.reader.search("absent", mode="keyword", filters=filters)
    finally:
        vault.close()


def test_listing_and_all_search_modes_report_conditions(
    tmp_path: Path, monkeypatch
) -> None:
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
        manager = vault._search_mgr
        original = manager._needs_review
        calls = []

        def count_assessments(path, filters):
            calls.append(path)
            return original(path, filters)

        monkeypatch.setattr(manager, "_needs_review", count_assessments)
        for mode in ("keyword", "semantic", "hybrid"):
            calls.clear()
            hits = vault.reader.search(
                "zebra",
                mode=mode,
                filters={"needs_review": "true", "type": "instruction"},
            )
            assert [hit.path for hit in hits] == ["instruction.md"]
            assert calls.count("instruction.md") == 1
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
                assert "needs_review" in result[0]
                assert result[0]["needs_review"]["needed"] is True
    finally:
        vault.close()


def test_unfiltered_results_preserve_payload_shape(tmp_path: Path) -> None:
    from dataclasses import asdict

    (tmp_path / "note.md").write_text("zebra")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        assert "needs_review" not in asdict(
            vault.reader.search("zebra", mode="keyword")[0]
        )
        assert "needs_review" not in asdict(vault.reader.list_documents()[0])
    finally:
        vault.close()


def test_listing_existing_filter_does_not_refetch(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text("zebra")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()

        def unexpected(*_args, **_kwargs):
            raise AssertionError("listing refetched a note")

        monkeypatch.setattr(vault._search_mgr._fts, "get_note", unexpected)
        assert [
            n.path for n in vault.reader.list_documents(filters={"status": "stable"})
        ] == ["index.md", "note.md"]
    finally:
        vault.close()


def test_selected_result_reuses_single_assessment(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "note.md").write_text("zebra")
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        manager = vault._search_mgr
        original = manager._needs_review
        calls = []

        def count_assessment(path, filters):
            calls.append(path)
            assessment = original(path, filters)
            if len(calls) > 1:
                raise AssertionError("assessment repeated")
            return assessment

        monkeypatch.setattr(manager, "_needs_review", count_assessment)
        hits = vault.reader.search(
            "zebra", mode="keyword", filters={"needs_review": "true"}
        )
        assert hits[0].needs_review["needed"] is True
        assert calls == ["note.md"]
    finally:
        vault.close()


def test_inactive_bundle_keeps_literal_selectors(tmp_path: Path) -> None:
    from dataclasses import asdict

    (tmp_path / "note.md").write_text(
        "---\nneeds_review: custom\nrequired_check: literal\n---\nzebra"
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            indexed_frontmatter_fields=["needs_review", "required_check"]
        ),
    )
    try:
        vault.index.build_index()
        hits = vault.reader.search(
            "zebra",
            mode="keyword",
            filters={"needs_review": "custom", "required_check": "literal"},
        )
        assert [hit.path for hit in hits] == ["note.md"]
        assert "needs_review" not in asdict(hits[0])
    finally:
        vault.close()


def test_custom_check_and_stale_filter_compose(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    text = "---\nstale_after: 2999-01-01\n---\nzebra\n"
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_verify(
            "note.md", "checker", concept_snapshot(text), check="x-custom"
        )
        vault.index.build_index()
        hits = vault.reader.list_documents(
            filters={
                "needs_review": "false",
                "required_check": "x-custom",
                "stale": "false",
            }
        )
        assert [hit.path for hit in hits] == ["note.md"]
        assert not vault.reader.list_documents(
            filters={
                "needs_review": "false",
                "required_check": "x-custom",
                "stale": "true",
            }
        )
        note = tmp_path / "note.md"
        note.write_text(note.read_text().replace("2999-01-01", "2000-01-01"))
        hits = vault.reader.list_documents(
            filters={"freshness": "unexpired", "needs_review": "true"}
        )
        assert [hit.path for hit in hits] == ["note.md"]
        assert "freshness_expired" in hits[0].needs_review["conditions"]
    finally:
        vault.close()


def test_unavailable_reviewed_and_unreviewed_candidates_across_consumers(
    tmp_path: Path,
) -> None:
    from tests.conftest import MockEmbeddingProvider

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    text = "---\nstale_after: 2999-01-01\n---\nzebra\n"
    for name in (
        "reviewed.md",
        "unreviewed.md",
        "recursive.md",
        "broken.md",
        "gone.md",
        "healthy.md",
        "reference.md",
    ):
        (tmp_path / name).write_text(text)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, embeddings_path=tmp_path / "vectors"),
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        vault.writer.okf_verify("reviewed.md", "alice", concept_snapshot(text))
        vault.index.build_index()
        vault.index.build_embeddings()
        for name in ("reviewed.md", "unreviewed.md"):
            (tmp_path / name).write_bytes(b"\xff")
        vault.writer.okf_verify("healthy.md", "alice", concept_snapshot(text))
        (tmp_path / "broken.md").write_bytes(b"---\nbroken: [\n---\nzebra")
        (tmp_path / "gone.md").unlink()
        (tmp_path / "recursive.md").write_text("---\nloop: &a [*a]\n---\nzebra")
        filters = {"needs_review": "true"}
        for hits in (
            vault.reader.search("zebra", mode="keyword", filters=filters),
            vault.reader.list_documents(filters=filters),
            vault.reader.get_similar("reference.md", filters=filters),
        ):
            found = {
                hit.path: hit.needs_review["conditions"]
                for hit in hits
                if hit.path != "reference.md"
            }
            assert found == {
                "reviewed.md": ["snapshot_unavailable"],
                "unreviewed.md": ["snapshot_unavailable"],
                "recursive.md": ["snapshot_unavailable"],
                "broken.md": ["snapshot_unavailable"],
            }
        assert {
            hit.path
            for hit in vault.reader.list_documents(filters={"needs_review": "false"})
        } == {"healthy.md"}
    finally:
        vault.close()


def test_reviewed_unexpired_bom_note(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    text = "---\nstale_after: 2999-01-01\n---\nzebra\n"
    for name in ("bom.md",):
        (tmp_path / name).write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        for name in ("bom.md",):
            vault.writer.okf_verify(name, "alice", concept_snapshot(text))
        bom = tmp_path / "bom.md"
        bom.write_bytes(b"\xef\xbb\xbf" + bom.read_bytes())
        vault.index.build_index()
        assert {
            hit.path
            for hit in vault.reader.list_documents(filters={"needs_review": "false"})
        } == {"bom.md"}
        assert not vault.reader.list_documents(filters={"needs_review": "true"})
    finally:
        vault.close()
