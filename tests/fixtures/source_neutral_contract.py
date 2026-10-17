"""Conformance case 6 every vector store runs: no working tree (#1786).

Subclass :class:`SourceNeutralContract` beside
:class:`~tests.fixtures.vector_store_contract.VectorStoreContract`; it uses
the same ``make_store`` fixture. The source is a dict: notes reach the
keyword index as :class:`ParsedNote` objects, the reconciler reads only the
keyword index, and the dict answers the source probe. Nothing on disk is
the source. Writer B's interleavings run inside writer A's provider or
probe call, or just after A reads the keyword index: every window
between A's reads and its write (#1508).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest

from markdown_vault_mcp.embed_text import EmbedTextBuilder
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.indexing.embedding_reconciler import (
    EmbeddingReconciler,
    ReconcileResult,
)
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.types import EmbeddingIdentity, ParsedNote, SourceState
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator

    from markdown_vault_mcp.interfaces import EmbeddingStore, KeywordIndex

SITE = "conformance"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class DictSource:
    """A source with no working tree: notes in a dict, and the source probe.

    ``available = False`` answers *unavailable* for every path.
    ``after_probe`` runs once, after an answer is decided and before it is
    returned, so a test can change the source between a probe and the
    write that acts on it.
    """

    def __init__(self) -> None:
        self.texts: dict[str, str] = {}
        self.frontmatter: dict[str, dict[str, Any]] = {}
        self.available = True
        self.after_probe: Callable[[], None] | None = None

    def note(self, path: str) -> ParsedNote:
        """Parse *path*'s text as the keyword index receives it."""
        text = self.texts[path]
        meta = self.frontmatter.get(path, {})
        return ParsedNote(
            path=path,
            frontmatter=dict(meta),
            title=path.rsplit("/", 1)[-1].removesuffix(".md"),
            chunks=HeadingChunker().chunk(text, {}),
            content_hash=_hash(repr(sorted(meta.items())) + text),
            modified_at=0.0,
            content_chars=len(text),
        )

    def probe(self, path: str) -> SourceState:
        """Answer present (with the note's token), absent or unavailable."""
        if not self.available:
            answer = SourceState("unavailable")
        elif path in self.texts:
            answer = SourceState("present", self.note(path).content_hash)
        else:
            answer = SourceState("absent")
        hook, self.after_probe = self.after_probe, None
        if hook is not None:
            hook()
        return answer


class HookedProvider(MockEmbeddingProvider):
    """A mock provider that runs ``during`` once, inside its next call.

    The hook is disarmed before it runs, so a second writer's own embed
    inside it does not re-enter it. ``fail_after_hook`` raises once the hook
    has run, as a provider outage would.
    """

    def __init__(self) -> None:
        super().__init__(dim=8)
        self.calls = 0
        self.during: Callable[[], None] | None = None
        self.fail_after_hook = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        hook, self.during = self.during, None
        if hook is not None:
            hook()
            if self.fail_after_hook:
                self.fail_after_hook = False
                raise RuntimeError("provider down")
        return super().embed(texts)


class ReadHookedIndex:
    """Writer A's view of the keyword index: runs ``after_read`` once.

    The hook runs after a ``list_chunks`` read returns its rows, so a test
    can publish a newer revision between the reconciler's reads.
    """

    def __init__(self, inner: FTSIndex) -> None:
        self._inner = inner
        self.after_read: Callable[[], None] | None = None

    def list_chunks(self, paths: Collection[str] | None = None) -> list[dict[str, Any]]:
        rows = self._inner.list_chunks(paths=paths)
        hook, self.after_read = self.after_read, None
        if hook is not None:
            hook()
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass
class _World:
    """One source, one keyword index and one store, written by two writers."""

    source: DictSource
    fts: FTSIndex
    provider: HookedProvider
    store: EmbeddingStore
    identity: EmbeddingIdentity
    writer: EmbeddingReconciler
    other: EmbeddingReconciler
    reads: ReadHookedIndex

    def write(
        self, path: str, body: str, *, frontmatter: dict[str, Any] | None = None
    ) -> None:
        """Change the source and index the note, as the keyword side would."""
        self.source.texts[path] = body
        self.source.frontmatter[path] = frontmatter or {}
        self.fts.upsert_note(self.source.note(path))

    def delete(self, path: str) -> None:
        """Delete the note from the source and from the keyword index."""
        del self.source.texts[path]
        self.fts.delete_by_path(path)

    def reconcile(
        self,
        paths: Collection[str] | None = None,
        *,
        by: EmbeddingReconciler | None = None,
    ) -> ReconcileResult:
        """Run one pass of writer A (or *by*)."""
        return (by or self.writer).reconcile(self.store, paths, site=SITE)

    def holds(self, path: str, body: str) -> bool:
        """Whether the store's rows for *path* carry *body*."""
        rows = self.store.rows_for(path)
        return bool(rows) and all(body in r.metadata["content"] for r in rows)

    def token(self, path: str) -> str:
        """The token the keyword index holds for *path*."""
        return str(self.fts.list_chunks(paths=[path])[0]["content_hash"])


class SourceNeutralContract:
    """Convergence from a source with no working tree (spec §4, case 6)."""

    @pytest.fixture
    def world(self, make_store: Callable[..., Any]) -> Iterator[_World]:
        source = DictSource()
        fts = FTSIndex(db_path=":memory:")
        provider = HookedProvider()
        builder = EmbedTextBuilder()
        identity = EmbeddingIdentity.of(provider, builder.format_token())

        reads = ReadHookedIndex(fts)

        def writer(index: KeywordIndex) -> EmbeddingReconciler:
            return EmbeddingReconciler(
                fts=index,
                provider=provider,
                embed_builder=builder,
                batch_size=4,
                source_probe=source,
                is_excluded=lambda _path: False,
            )

        yield _World(
            source,
            fts,
            provider,
            make_store(identity),
            identity,
            writer(cast("KeywordIndex", reads)),
            writer(fts),
            reads,
        )
        fts.close()

    # -- convergence -------------------------------------------------------

    def test_a_cold_build_from_the_keyword_index_then_converges_for_free(
        self, world: _World
    ) -> None:
        world.write("a.md", "Apples are red.")
        world.write("b.md", "Boats float.")
        embedded, attempted = world.writer.rebuild(world.store, world.identity)
        assert (embedded, attempted) == (2, 2)
        assert world.store.tokens() == {p: world.token(p) for p in ("a.md", "b.md")}
        calls = world.provider.calls
        result = world.reconcile()
        assert (result.up_to_date, result.embedded, result.conflicts) == (2, 0, set())
        assert world.provider.calls == calls

    def test_an_edit_reembeds_only_that_note(self, world: _World) -> None:
        world.write("a.md", "Apples are red.")
        world.write("b.md", "Boats float.")
        world.reconcile()
        world.write("a.md", "Apples are green.")
        result = world.reconcile(["a.md"])
        assert (result.embedded, result.up_to_date) == (1, 0)
        assert world.holds("a.md", "Apples are green.")
        assert world.store.tokens()["a.md"] == world.token("a.md")
        assert world.holds("b.md", "Boats float.")

    def test_a_token_only_revision_costs_no_embedding(self, world: _World) -> None:
        world.write("a.md", "Apples are red.")
        world.reconcile()
        world.write("a.md", "Apples are red.", frontmatter={"reviewed": "yes"})
        calls = world.provider.calls
        result = world.reconcile(["a.md"])
        assert (result.retokened, world.provider.calls) == (1, calls)
        assert world.store.tokens()["a.md"] == world.token("a.md")

    # -- deleted against unavailable ---------------------------------------

    def test_a_deleted_note_loses_its_vectors(self, world: _World) -> None:
        world.write("a.md", "Apples are red.")
        world.write("b.md", "Boats float.")
        world.reconcile()
        world.delete("b.md")
        result = world.reconcile()
        assert result.removed == 1
        assert set(world.store.tokens()) == {"a.md"}

    def test_an_unavailable_source_keeps_every_vector_and_says_so_once(
        self, world: _World, caplog: pytest.LogCaptureFixture
    ) -> None:
        world.write("a.md", "Apples are red.")
        world.write("b.md", "Boats float.")
        world.reconcile()
        world.fts.delete_by_path("a.md")
        world.fts.delete_by_path("b.md")
        world.source.available = False
        with caplog.at_level(logging.WARNING):
            result = world.reconcile()
        assert result.removed == 0
        assert world.holds("a.md", "Apples are red.")
        assert world.holds("b.md", "Boats float.")
        warned = [
            r
            for r in caplog.records
            if r.msg.startswith("embedding_reconcile_source_unavailable")
        ]
        assert len(warned) == 1
        assert warned[0].args == (SITE, 2)

    def test_vectors_kept_through_an_outage_go_once_the_source_answers_absent(
        self, world: _World
    ) -> None:
        world.write("a.md", "Apples are red.")
        world.reconcile()
        world.source.available = False
        world.delete("a.md")
        assert world.reconcile().removed == 0
        world.source.available = True
        assert world.reconcile().removed == 1
        assert world.store.tokens() == {}

    def test_a_note_the_source_still_holds_is_kept_as_a_keyword_gap(
        self, world: _World
    ) -> None:
        world.write("a.md", "Apples are red.")
        world.reconcile()
        world.fts.delete_by_path("a.md")
        assert world.reconcile().removed == 0
        assert world.holds("a.md", "Apples are red.")

    # -- the out-of-order revision replay (#1508) --------------------------

    def _at_r1(self, world: _World) -> None:
        world.write("n.md", "Revision zero.")
        world.reconcile()
        world.write("n.md", "Revision one.")

    def test_a_slow_stale_writer_conflicts_and_its_retry_lands_nothing(
        self, world: _World
    ) -> None:
        self._at_r1(world)

        def newer_writer() -> None:
            world.write("n.md", "Revision two.")
            assert world.reconcile(by=world.other).embedded == 1

        world.provider.during = newer_writer
        result = world.reconcile()
        assert result.conflicts == {"n.md"}
        assert world.holds("n.md", "Revision two.")
        calls = world.provider.calls
        retry = world.reconcile(result.conflicts)
        assert (retry.up_to_date, retry.conflicts, world.provider.calls) == (
            1,
            set(),
            calls,
        )
        assert world.store.tokens()["n.md"] == world.token("n.md")

    def test_writers_in_order_never_conflict(self, world: _World) -> None:
        self._at_r1(world)
        first = world.reconcile()
        world.write("n.md", "Revision two.")
        second = world.reconcile(by=world.other)
        assert (first.conflicts, second.conflicts) == (set(), set())
        assert world.holds("n.md", "Revision two.")
        assert world.store.tokens()["n.md"] == world.token("n.md")

    def test_a_stale_publish_is_tied_to_its_revision_and_replaced_next_pass(
        self, world: _World
    ) -> None:
        self._at_r1(world)
        r1 = world.token("n.md")

        def source_moves_on() -> None:
            world.write("n.md", "Revision two.")

        world.provider.during = source_moves_on
        assert world.reconcile().conflicts == set()
        assert world.holds("n.md", "Revision one.")
        assert world.store.tokens()["n.md"] == r1
        newer = world.reconcile(by=world.other)
        assert (newer.embedded, newer.conflicts) == (1, set())
        assert world.holds("n.md", "Revision two.")
        assert world.store.tokens()["n.md"] == world.token("n.md")

    def test_a_newer_publish_between_the_reads_still_conflicts(
        self, world: _World
    ) -> None:
        self._at_r1(world)
        published: list[ReconcileResult] = []

        def newer_writer() -> None:
            world.write("n.md", "Revision two.")
            published.append(world.reconcile(by=world.other))

        world.reads.after_read = newer_writer
        result = world.reconcile()
        assert published[0].embedded == 1
        assert result.conflicts == {"n.md"}
        assert world.holds("n.md", "Revision two.")
        retry = world.reconcile(result.conflicts)
        assert (retry.up_to_date, retry.conflicts) == (1, set())
        assert world.store.tokens()["n.md"] == world.token("n.md")

    def test_a_removal_racing_a_recreate_keeps_the_new_rows(
        self, world: _World
    ) -> None:
        world.write("n.md", "Revision zero.")
        world.reconcile()
        world.delete("n.md")

        def recreated() -> None:
            world.write("n.md", "Revision three.")
            assert world.reconcile(by=world.other).embedded == 1

        world.source.after_probe = recreated
        result = world.reconcile()
        assert (result.removed, result.conflicts) == (0, {"n.md"})
        retry = world.reconcile(result.conflicts)
        assert (retry.removed, retry.up_to_date) == (0, 1)
        assert world.holds("n.md", "Revision three.")

    def test_a_provider_failure_after_the_newer_publish_keeps_it(
        self, world: _World
    ) -> None:
        self._at_r1(world)

        def newer_writer() -> None:
            world.write("n.md", "Revision two.")
            world.reconcile(by=world.other)

        world.provider.during = newer_writer
        world.provider.fail_after_hook = True
        result = world.reconcile()
        assert (result.retry, result.conflicts) == ({"n.md"}, set())
        assert world.holds("n.md", "Revision two.")
        assert world.reconcile(result.retry).up_to_date == 1

    def test_a_requeued_path_deleted_before_its_retry_is_removed(
        self, world: _World
    ) -> None:
        self._at_r1(world)

        def newer_writer() -> None:
            world.write("n.md", "Revision two.")
            world.reconcile(by=world.other)

        world.provider.during = newer_writer
        result = world.reconcile()
        assert result.conflicts == {"n.md"}
        world.delete("n.md")
        retry = world.reconcile(result.conflicts)
        assert (retry.removed, world.store.tokens()) == (1, {})
