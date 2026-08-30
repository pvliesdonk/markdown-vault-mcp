"""Embedding convergence at boot (#665, Problem 4).

``build_embeddings(force=False)`` over a non-empty vector index converges
it to the FTS chunk set instead of skipping all work: documents missing
from the vector index are embedded, documents whose indexed content
changed are re-embedded, and vectors for deleted documents are dropped.

This closes the gap that the boot reconciliation reindex (#665 PR2)
exposes: the boot ``ReindexAll`` job runs before the vector index is
loaded, so files changed while no server ran reach the FTS index but not
the vectors — the ``BuildEmbeddings`` job queued behind it (writer FIFO
order) now reconciles the difference rather than skipping because the
vector index is non-empty.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from markdown_vault_mcp.vault import Vault
from markdown_vault_mcp.vector_index import VectorIndex
from tests.conftest import (
    MockEmbeddingProvider,
    _parse_tool_data,
    wait_for_mcp_writer_drain,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_vault_dir(tmp_path: Path, n_docs: int = 3) -> Path:
    """Create a vault directory with *n_docs* single-chunk notes."""
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    for i in range(n_docs):
        (vault / f"note_{i}.md").write_text(
            f"# Note {i}\n\nOriginal body {i}.\n", encoding="utf-8"
        )
    return vault


def _boot(vault_dir: Path, tmp_path: Path, provider: MockEmbeddingProvider) -> Vault:
    """Replicate the server lifespan boot sequence at the Vault level.

    The lifespan submits BuildIndex, ReindexAll (#665 PR2), and
    BuildEmbeddings to the writer in FIFO order; the synchronous facet
    calls below run the same jobs on the same writer thread in the same
    order, with the same warm-restart short-circuits.
    """
    vault = Vault(
        source_dir=vault_dir,
        index_path=tmp_path / "fts.db",
        state_path=tmp_path / "s.json",
        embeddings_path=tmp_path / "vectors",
        embedding_provider=provider,
    )
    vault.index.build_index()
    vault.index.reindex()
    vault.index.build_embeddings()
    return vault


def _track_embeds(provider: MockEmbeddingProvider) -> list[list[str]]:
    """Record every batch of texts passed to ``provider.embed()``."""
    original_embed = provider.embed
    calls: list[list[str]] = []

    def tracking_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return original_embed(texts)

    provider.embed = tracking_embed  # type: ignore[method-assign]
    return calls


class _FailingProvider(MockEmbeddingProvider):
    """Provider whose embed() always fails (boot-time provider outage)."""

    def embed(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
        raise RuntimeError("provider down during boot BuildEmbeddings")


class TestBootEmbeddingConvergence:
    """Boots must leave the vector index identical to the FTS chunk set."""

    def test_boot_with_no_drift_does_no_embedding_work(self, tmp_path: Path) -> None:
        """vectors ≡ FTS → second boot embeds nothing and saves nothing."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        count1 = v1.index.embeddings_status()["chunk_count"]
        assert count1 > 0
        v1.close()

        npy = tmp_path / "vectors.npy"
        mtime_before = npy.stat().st_mtime_ns
        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)

        assert calls == []
        assert v2.index.embeddings_status()["chunk_count"] == count1
        assert count1 == v2._fts.count_chunks()
        # A converged index must not be re-saved.
        assert npy.stat().st_mtime_ns == mtime_before
        v2.close()

    def test_boot_embeds_documents_added_while_down(self, tmp_path: Path) -> None:
        """Files written externally while no server runs are embedded at boot."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        count1 = v1.index.embeddings_status()["chunk_count"]
        v1.close()

        # External process (e.g. a session hook) writes notes while "down".
        (vault_dir / "hook_summary.md").write_text(
            "# Hook Summary\n\nSession summary written by a hook.\n",
            encoding="utf-8",
        )
        (vault_dir / "hook_followup.md").write_text(
            "# Hook Followup\n\nFollow-up note written by a hook.\n",
            encoding="utf-8",
        )

        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)

        # Exactly the new chunks were embedded — nothing re-embedded.
        embedded_texts = [t for batch in calls for t in batch]
        assert len(embedded_texts) == 2
        assert any("Session summary" in t for t in embedded_texts)
        assert any("Follow-up note" in t for t in embedded_texts)

        assert v2.index.embeddings_status()["chunk_count"] == count1 + 2
        assert count1 + 2 == v2._fts.count_chunks()

        # The new document is reachable via semantic search.  The mock
        # provider's vectors are hash-based (no real semantics), so use a
        # limit that returns every chunk and assert reachability only.
        results = v2.reader.search(
            "Session summary written by a hook", mode="semantic", limit=50
        )
        assert any(r.path == "hook_summary.md" for r in results)
        v2.close()

    def test_boot_removes_vectors_for_documents_deleted_while_down(
        self, tmp_path: Path
    ) -> None:
        """Vectors for files deleted externally are dropped at boot."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        count1 = v1.index.embeddings_status()["chunk_count"]
        v1.close()

        doomed = VectorIndex.load(tmp_path / "vectors", provider)
        doomed_chunks = len(doomed.chunks_by_path()["note_2.md"])
        assert doomed_chunks > 0

        (vault_dir / "note_2.md").unlink()

        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)

        assert calls == []
        assert (
            v2.index.embeddings_status()["chunk_count"]
            == count1 - doomed_chunks
            == v2._fts.count_chunks()
        )
        v2.close()

        persisted = VectorIndex.load(tmp_path / "vectors", provider)
        assert "note_2.md" not in persisted.chunks_by_path()

    def test_boot_refreshes_vectors_for_documents_modified_while_down(
        self, tmp_path: Path
    ) -> None:
        """Modified documents get fresh vectors; stale chunk content is gone."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        v1.close()

        (vault_dir / "note_1.md").write_text(
            "# Note 1\n\nCompletely rewritten body about gardening.\n",
            encoding="utf-8",
        )

        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)

        # Only the modified document's chunks were embedded.
        embedded_texts = [t for batch in calls for t in batch]
        assert len(embedded_texts) > 0
        assert all("gardening" in t for t in embedded_texts)

        assert v2.index.embeddings_status()["chunk_count"] == v2._fts.count_chunks()
        v2.close()

        persisted = VectorIndex.load(tmp_path / "vectors", provider)
        by_path = persisted.chunks_by_path()
        assert all("gardening" in r["content"] for r in by_path["note_1.md"])
        # No stale vector metadata for the old content remains anywhere.
        all_rows = [r for rows in by_path.values() for r in rows]
        assert not any("Original body 1" in r["content"] for r in all_rows)

    def test_boot_refreshes_metadata_for_line_shift_only_edit(
        self, tmp_path: Path
    ) -> None:
        """An edit that only shifts line numbers (identical chunk content)
        still refreshes start_line in vector metadata (#668 review)."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        v1.close()

        # Prepend blank lines: chunk content unchanged, start_line shifts.
        original = (vault_dir / "note_1.md").read_text(encoding="utf-8")
        (vault_dir / "note_1.md").write_text("\n\n" + original, encoding="utf-8")

        v2 = _boot(vault_dir, tmp_path, provider)
        fts_lines = {
            (r["content"], r["start_line"])
            for r in v2._fts.list_chunks()
            if r["path"] == "note_1.md"
        }
        v2.close()

        persisted = VectorIndex.load(tmp_path / "vectors", provider)
        vec_lines = {
            (r["content"], r["start_line"])
            for r in persisted.chunks_by_path()["note_1.md"]
        }
        assert vec_lines == fts_lines

    def test_boot_converges_legacy_drifted_vector_index(self, tmp_path: Path) -> None:
        """A historic vectors-subset-of-FTS state heals in a single boot.

        Reproduces the drift the old skip-if-nonempty guard accumulated
        (observed in #665: 307 vectors vs 1379 FTS chunks): the persisted
        vector index is missing documents the FTS index knows about.  One
        boot must embed exactly the missing chunks.
        """
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path, n_docs=5)
        v1 = _boot(vault_dir, tmp_path, provider)
        fts_chunks = v1._fts.count_chunks()
        v1.close()

        # Corrupt the sidecar the way the old code path did: drop some
        # documents' vectors while FTS keeps them.
        vectors = VectorIndex.load(tmp_path / "vectors", provider)
        dropped = vectors.delete_by_path("note_0.md")
        dropped += vectors.delete_by_path("note_3.md")
        assert dropped > 0
        vectors.save(tmp_path / "vectors")

        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)

        # Exactly the missing chunks were re-embedded.
        embedded_texts = [t for batch in calls for t in batch]
        assert len(embedded_texts) == dropped
        assert v2.index.embeddings_status()["chunk_count"] == fts_chunks
        v2.close()

        persisted = VectorIndex.load(tmp_path / "vectors", provider)
        by_path = persisted.chunks_by_path()
        assert "note_0.md" in by_path
        assert "note_3.md" in by_path

    def test_failed_boot_embeddings_job_self_heals_on_next_boot(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Provider outage during one boot's convergence heals on the next.

        A failed boot BuildEmbeddings job used to leave permanent drift:
        the no-op guard advanced past it forever.  Now the failure is just
        a larger diff for the next successful convergence.
        """
        import logging

        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        count1 = v1.index.embeddings_status()["chunk_count"]
        v1.close()

        (vault_dir / "offline_added.md").write_text(
            "# Offline\n\nWritten while the server was down.\n", encoding="utf-8"
        )

        # Boot 2: provider down for the whole convergence.  The job must
        # not raise; existing vectors stay intact.
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.index"
        ):
            v2 = _boot(vault_dir, tmp_path, _FailingProvider())
        assert v2.index.embeddings_status()["chunk_count"] == count1
        assert any(
            "build_embeddings_converge_skip_doc" in r.getMessage()
            for r in caplog.records
        )
        v2.close()

        # Boot 3: provider back — exactly the missing chunks are embedded.
        calls = _track_embeds(provider)
        v3 = _boot(vault_dir, tmp_path, provider)
        embedded_texts = [t for batch in calls for t in batch]
        assert len(embedded_texts) == 1
        assert "Written while the server was down" in embedded_texts[0]
        assert (
            v3.index.embeddings_status()["chunk_count"]
            == count1 + 1
            == v3._fts.count_chunks()
        )
        v3.close()

    def test_provider_failure_keeps_existing_vectors_for_modified_doc(
        self, tmp_path: Path
    ) -> None:
        """A doc whose re-embed fails keeps its old vectors (no data loss)."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        count1 = v1.index.embeddings_status()["chunk_count"]
        v1.close()

        (vault_dir / "note_1.md").write_text(
            "# Note 1\n\nRewritten while down.\n", encoding="utf-8"
        )

        v2 = _boot(vault_dir, tmp_path, _FailingProvider())
        assert v2.index.embeddings_status()["chunk_count"] == count1
        v2.close()

        persisted = VectorIndex.load(tmp_path / "vectors", provider)
        rows = persisted.chunks_by_path()["note_1.md"]
        # The stale (pre-modification) vectors are still searchable rather
        # than deleted-without-replacement.
        assert any("Original body 1" in r["content"] for r in rows)

    def test_force_rebuild_unchanged_by_convergence(self, tmp_path: Path) -> None:
        """force=True still discards and re-embeds the entire corpus."""
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        fts_chunks = v1._fts.count_chunks()
        v1.close()

        calls = _track_embeds(provider)
        v2 = Vault(
            source_dir=vault_dir,
            index_path=tmp_path / "fts.db",
            state_path=tmp_path / "s.json",
            embeddings_path=tmp_path / "vectors",
            embedding_provider=provider,
        )
        v2.index.build_index()
        embedded = v2.index.build_embeddings(force=True)

        assert embedded == fts_chunks
        assert sum(len(batch) for batch in calls) == fts_chunks
        v2.close()


class TestLifespanEmbeddingConvergence:
    """Full MCP lifespan: build + boot reindex + embeddings job converge."""

    def test_warm_boot_lifespan_converges_offline_add(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp import domain as deps_mod
        from markdown_vault_mcp.server import make_server

        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        pre = _boot(vault_dir, tmp_path, provider)
        count_before = pre.index.embeddings_status()["chunk_count"]
        pre.close()

        # Offline change while no server runs.
        (vault_dir / "offline_added.md").write_text(
            "# Offline\n\nWritten while the server was down.\n", encoding="utf-8"
        )

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_dir))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))

        # Inject the mock provider into the resolved instances so the
        # lifespan submits the boot BuildEmbeddings job without a real
        # provider (domain.Service.start resolves via to_vault_instances /
        # to_vault_settings since #1158).
        import dataclasses

        original_to_instances = deps_mod.to_vault_instances
        original_to_settings = deps_mod.to_vault_settings

        def patched_to_instances(config: Any) -> Any:
            return dataclasses.replace(
                original_to_instances(config),
                embedding_provider=MockEmbeddingProvider(),
            )

        def patched_to_settings(config: Any, *, instances: Any = None) -> Any:
            settings = original_to_settings(config, instances=instances)
            return dataclasses.replace(settings, embeddings_path=tmp_path / "vectors")

        monkeypatch.setattr(deps_mod, "to_vault_instances", patched_to_instances)
        monkeypatch.setattr(deps_mod, "to_vault_settings", patched_to_settings)
        server = make_server()

        async def _run() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                emb_res = await client.call_tool("embeddings_status", {})
                stats_res = await client.call_tool("stats", {})
                search_res = await client.call_tool(
                    "search",
                    {"query": "Offline", "mode": "semantic", "limit": 50},
                )
                return (
                    emb_res.structured_content or {},
                    stats_res.structured_content or {},
                    [r["path"] for r in _parse_tool_data(search_res)],
                )

        emb, stats, semantic_paths = asyncio.run(_run())
        # The boot reindex put the new doc into FTS; the embeddings job
        # behind it converged the vector index to the same chunk set.
        assert emb["chunk_count"] == stats["chunk_count"] == count_before + 1
        assert "offline_added.md" in semantic_paths


class TestAccessors:
    """Unit coverage for the two convergence accessors."""

    def test_fts_list_chunks_shape_and_order(self, tmp_path: Path) -> None:
        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path, n_docs=2)
        # Large enough that the adaptive chunker splits at the H2s.
        body = "\n".join(f"## Section {i}\n\n" + ("word " * 300) for i in range(3))
        (vault_dir / "multi.md").write_text(f"# Multi\n\n{body}\n", encoding="utf-8")
        v = _boot(vault_dir, tmp_path, provider)
        rows = v._fts.list_chunks()
        fts_chunks = v._fts.count_chunks()
        v.close()

        assert len(rows) == fts_chunks
        assert [r["path"] for r in rows] == sorted(r["path"] for r in rows)
        multi_rows = [r for r in rows if r["path"] == "multi.md"]
        assert len(multi_rows) > 1
        # Within a document, rows come back in chunk insertion order.
        assert [r["start_line"] for r in multi_rows] == sorted(
            r["start_line"] for r in multi_rows
        )
        for r in rows:
            assert set(r) == {
                "path",
                "title",
                "folder",
                "heading",
                "content",
                "start_line",
                "frontmatter_json",
                "is_first_chunk",
            }
        # is_first_chunk marks exactly the first row of each document.
        assert multi_rows[0]["is_first_chunk"] is True
        assert all(r["is_first_chunk"] is False for r in multi_rows[1:])

    def test_vector_chunks_by_path_groups_copies(self) -> None:
        provider = MockEmbeddingProvider()
        index = VectorIndex(provider)
        meta = [
            {"path": "a.md", "title": "A", "content": "one"},
            {"path": "b.md", "title": "B", "content": "two"},
            {"path": "a.md", "title": "A", "content": "three"},
        ]
        index.add(["one", "two", "three"], meta)

        grouped = index.chunks_by_path()
        assert sorted(grouped) == ["a.md", "b.md"]
        assert [r["content"] for r in grouped["a.md"]] == ["one", "three"]
        # Returned rows are copies — mutating them must not corrupt the index.
        grouped["a.md"][0]["content"] = "mutated"
        assert index.chunks_by_path()["a.md"][0]["content"] == "one"


# ---------------------------------------------------------------------------
# Curated ranking: preamble-aware convergence
# ---------------------------------------------------------------------------


def _boot_enriched(
    vault_dir: Path, tmp_path: Path, provider: MockEmbeddingProvider
) -> Vault:
    """Boot like :func:`_boot` with searchable frontmatter enrichment on."""
    vault = Vault(
        source_dir=vault_dir,
        index_path=tmp_path / "fts.db",
        state_path=tmp_path / "s.json",
        embeddings_path=tmp_path / "vectors",
        embedding_provider=provider,
        searchable_frontmatter_fields=["summary"],
    )
    vault.index.build_index()
    vault.index.reindex()
    vault.index.build_embeddings()
    return vault


class TestPreambleConvergence:
    def test_offline_summary_only_edit_re_embeds(self, tmp_path: Path) -> None:
        """Body unchanged, frontmatter summary changed while down → re-embed.

        The chunk (title, heading, content, start_line) signature is
        untouched by a frontmatter-only edit; only the 5th preamble term
        catches it.
        """
        provider = MockEmbeddingProvider()
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        note = vault_dir / "doc.md"
        note.write_text(
            "---\nsummary: old summary\n---\n# Doc\n\nstable body\n",
            encoding="utf-8",
        )
        v1 = _boot_enriched(vault_dir, tmp_path, provider)
        v1.close()

        note.write_text(
            "---\nsummary: new summary\n---\n# Doc\n\nstable body\n",
            encoding="utf-8",
        )
        calls = _track_embeds(provider)
        v2 = _boot_enriched(vault_dir, tmp_path, provider)
        try:
            embedded = [t for batch in calls for t in batch]
            assert any("new summary" in t for t in embedded)
            rows = v2._vectors.chunks_by_path()["doc.md"]
            assert rows[0]["preamble"] == "new summary"
        finally:
            v2.close()

    def test_enriched_boot_converges_to_no_op(self, tmp_path: Path) -> None:
        """No edits between enriched boots → second boot embeds nothing."""
        provider = MockEmbeddingProvider()
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "doc.md").write_text(
            "---\nsummary: a summary\n---\n# Doc\n\nstable body\n",
            encoding="utf-8",
        )
        v1 = _boot_enriched(vault_dir, tmp_path, provider)
        v1.close()

        calls = _track_embeds(provider)
        v2 = _boot_enriched(vault_dir, tmp_path, provider)
        try:
            assert calls == []
        finally:
            v2.close()

    def test_v2_sidecar_search_path_loads_clean_without_rebuild(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SearchManager's lazy vector load carries the v2 format token.

        ``Vault`` wires ``embed_text_format`` into two independent
        consumers — ``IndexManager`` and ``SearchManager`` — each loading the
        shared sidecar with its own slot. The convergence tests exercise only
        the IndexManager path; a semantic search is the sole trigger for
        SearchManager's lazy load. If SearchManager's token were mis-wired to
        the default ``"v1"``, the first semantic search after every boot would
        see the ``"v2;fields=summary"`` sidecar as incompatible and route to a
        full re-embed — a silent per-boot rebuild the IndexManager-only tests
        miss. ``load_or_self_heal`` logs that mismatch (``Rebuilding
        embeddings.``) synchronously before queueing the rebuild, so its
        absence proves the token matched; the doc is also returned.
        """
        import logging

        provider = MockEmbeddingProvider()
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "doc.md").write_text(
            "---\nsummary: a crisp summary\n---\n# Doc\n\nstable body\n",
            encoding="utf-8",
        )
        v1 = _boot_enriched(vault_dir, tmp_path, provider)
        v1.close()

        # Construct an enriched Vault and build ONLY the FTS index, leaving
        # SearchManager's vector slot cold: build_embeddings would pre-populate
        # it and bypass the on-disk load entirely, so a semantic query is the
        # sole trigger for SearchManager's independent, lazy sidecar load.
        v2 = Vault(
            source_dir=vault_dir,
            index_path=tmp_path / "fts.db",
            state_path=tmp_path / "s.json",
            embeddings_path=tmp_path / "vectors",
            embedding_provider=provider,
            searchable_frontmatter_fields=["summary"],
        )
        v2.index.build_index()
        try:
            with caplog.at_level(
                logging.WARNING, logger="markdown_vault_mcp.managers.search"
            ):
                results = v2.reader.search("crisp summary", mode="semantic", limit=50)
            assert any(r.path == "doc.md" for r in results)
            # A mis-wired token would raise VectorIndexCompatibilityError and
            # log this before rebuilding; a matching token loads clean.
            assert not any(
                "Rebuilding embeddings" in r.getMessage() for r in caplog.records
            )
        finally:
            v2.close()

    def test_legacy_rows_without_preamble_do_not_re_embed_under_defaults(
        self, tmp_path: Path
    ) -> None:
        """A pre-upgrade sidecar (no preamble keys, no format token) loads
        clean under the default config and converges with zero work."""
        import json as _json

        provider = MockEmbeddingProvider()
        vault_dir = _make_vault_dir(tmp_path)
        v1 = _boot(vault_dir, tmp_path, provider)
        v1.close()

        # Rewrite the sidecar to the pre-upgrade shape: strip the per-row
        # preamble keys and the index-level embed_text_format token.
        json_path = tmp_path / "vectors.json"
        payload = _json.loads(json_path.read_text(encoding="utf-8"))
        for row in payload["rows"]:
            row.pop("preamble", None)
        payload["index_metadata"].pop("embed_text_format", None)
        json_path.write_text(_json.dumps(payload), encoding="utf-8")

        calls = _track_embeds(provider)
        v2 = _boot(vault_dir, tmp_path, provider)
        try:
            assert calls == []
        finally:
            v2.close()
