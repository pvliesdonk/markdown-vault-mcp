"""The one writer of embedding rows (#1768).

Rows come from the keyword index, never from a source file: each
document's chunks become embedding inputs, tagged with the document's
``content_hash`` as its token. Per document the reconciler compares those
inputs and that token with what the store holds and decides: nothing,
re-token (the input is unchanged and only the token moved, or a legacy row
carries no token: the stored vectors are republished, no provider call),
embed, or remove. An embed sends the provider only the chunks whose input
the store holds no vector for; every other chunk keeps its stored vector.
It embeds outside any store call and publishes with the
token it observed, so a write that crosses another writer's comes back as
a conflict, which the caller re-queues. A path the store holds and the
keyword index has no rows for loses its vectors only on an exclusion or a
source probe answering *absent* (#1785): a tombstoned note (it exists but
does not parse) keeps them until it is fixed or deleted, out of search
(#1505); *unavailable* keeps them, logged once per pass, and *present* is a
keyword-index gap that keeps them too.
"""

from __future__ import annotations

import logging
import math
import time
from array import array
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.embed_text import is_embeddable
from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.interfaces import ReadsMetadataInBatches
from markdown_vault_mcp.types import DocumentRows, VectorRow

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Sequence

    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.indexing.donor import Donor
    from markdown_vault_mcp.interfaces import (
        EmbeddingStore,
        KeywordIndex,
        SourceProbe,
    )
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import EmbeddingIdentity, RevisionToken

logger = logging.getLogger(__name__)


def stored_inputs(
    builder: EmbedTextBuilder, rows: Iterable[VectorRow]
) -> dict[str, Sequence[float]]:
    """Map the embedding input each of *rows* was embedded from to its vector.

    The input is rebuilt from the row's metadata the way the reconciler builds
    it from the keyword index (:meth:`EmbeddingReconciler.documents`); a
    preamble is only ever non-empty on a document's first chunk. A row whose
    metadata lacks a string title or content is left out, so its vector is not
    reused. The one matching rule for a store's own rows (#1795) and for a
    donor's (#1505).

    Args:
        builder: The builder the current embedding inputs come from.
        rows: Stored rows, typically one document's ``rows_for``.

    Returns:
        Each rebuilt input to the stored vector; a later duplicate input wins.
    """
    known: dict[str, Sequence[float]] = {}
    for row in rows:
        title = row.metadata.get("title")
        content = row.metadata.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            continue
        preamble = row.metadata.get("preamble") or ""
        text = builder.build(
            title=title,
            heading=row.metadata.get("heading"),
            content=content,
            fields_text=preamble,
            is_first_chunk=bool(preamble),
        )
        known[text] = row.vector
    return known


#: At or above this many re-tokened documents in one pass, one generation
#: publish (one matrix copy and one file write) replaces the per-document
#: replaces, each of which copies the whole matrix (#1768).
_BULK_RETOKEN = 64

# Documents whose stored metadata one read fetches, from a store that reads
# in batches (#1937): few round trips, and memory bounded by the batch.
_METADATA_BATCH = 500

_METADATA_KEYS = ("path", "title", "folder", "heading", "content", "start_line")


@dataclass
class ReconcileResult:
    """What one pass did, for the caller's aggregate log line.

    Attributes:
        embedded: Chunks embedded and published.
        reused: Chunks of a changed document published with their stored
            vector, because their embedding input is unchanged.
        removed: Rows removed.
        retokened: Documents republished under a new token without embedding.
        kept: Chunks left as they were: the provider failed, the store
            refused a vector, or a store write failed.
        up_to_date: Chunks already converged.
        conflicts: Paths another writer changed underneath; the caller
            re-queues them for another flush.
        retry: Paths the provider or a store write failed for; their rows
            stay, and the caller keeps them dirty for the next flush.
    """

    embedded: int = 0
    reused: int = 0
    removed: int = 0
    retokened: int = 0
    kept: int = 0
    up_to_date: int = 0
    conflicts: set[str] = field(default_factory=set)
    retry: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RebuildPreview:
    """What a full build would do, counted without building (:meth:`EmbeddingReconciler.preview`).

    Attributes:
        chunks: Every chunk the keyword index holds.
        reusable: Chunks a vector would be copied for, from the store's own
            rows or a donor (#1505).
        to_embed: The inputs the build would send the provider, each once
            per document.
    """

    chunks: int
    reusable: int
    to_embed: int


@dataclass(frozen=True)
class RebuildOutcome:
    """What one full build did (:meth:`EmbeddingReconciler.rebuild`).

    Counts describe the generation the build assembled, published or not.

    Attributes:
        embedded: Chunks the provider embedded for the generation.
        reused: Chunks copied into it from the store's own rows or a donor (#1505).
        attempted: Every chunk the keyword index holds.
        published: Whether a generation was published.
        failed_batches: Provider batches that failed and were skipped.
    """

    embedded: int
    reused: int
    attempted: int
    published: bool
    failed_batches: int


@dataclass(frozen=True)
class _Document:
    """One document's embedding inputs, from the keyword index."""

    path: str
    token: RevisionToken
    texts: tuple[str, ...]
    metadata: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _Pass:
    """The state one reconcile pass shares between its steps."""

    store: EmbeddingStore
    stored: dict[str, RevisionToken | None]
    result: ReconcileResult
    site: str


def _stored_metadata(
    store: EmbeddingStore, paths: list[str]
) -> dict[str, list[dict[str, Any]]] | None:
    """*paths*' stored metadata in one read, or ``None`` from a store that reads per document.

    A store without :class:`ReadsMetadataInBatches` serves each document's
    rows from this process, so reading them one at a time costs little.
    """
    if isinstance(store, ReadsMetadataInBatches):
        return store.metadata_for(paths)
    return None


def _signature(rows: Collection[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
    """The chunk identity two row sets are compared by (#665, #1087).

    ``start_line`` participates so that line-shift-only edits (content
    identical, positions moved) still refresh vector metadata; the chunks'
    inputs are unchanged, so their stored vectors are republished without a
    provider call. The preamble participates so an offline summary-only
    edit (content identical, searchable frontmatter changed) still
    re-embeds; ``or ""`` normalisation keeps legacy vector rows without the
    key equal to fresh rows under default (v1) config.
    """
    return Counter(
        (
            r.get("title"),
            r.get("heading"),
            r.get("content"),
            r.get("start_line"),
            r.get("preamble") or "",
        )
        for r in rows
    )


class EmbeddingReconciler:
    """Publishes every embedding row, one document at a time.

    Args:
        fts: The keyword index, the source of every row.
        provider: Embeds the documents that need it.
        embed_builder: Builds each chunk's embedding text.
        batch_size: Most texts per provider call.
        source_probe: Answers whether a document the keyword index holds no
            rows for is still at its source.
        is_excluded: Whether a path matches the vault's exclusions.
    """

    def __init__(
        self,
        *,
        fts: KeywordIndex,
        provider: EmbeddingProvider,
        embed_builder: EmbedTextBuilder,
        batch_size: int,
        source_probe: SourceProbe,
        is_excluded: Callable[[str], bool],
    ) -> None:
        self._fts = fts
        self._provider = provider
        self._builder = embed_builder
        self._batch_size = batch_size
        self._source_probe = source_probe
        self._is_excluded = is_excluded

    def documents(self, paths: Collection[str] | None = None) -> dict[str, _Document]:
        """Read *paths*' (or every) document's embedding inputs from the keyword index.

        Args:
            paths: The documents to read; ``None`` for every document.

        Returns:
            Each document's inputs by path. A document the keyword index
            holds rows for but with nothing embeddable (#1087) is present,
            with no texts.
        """
        texts: dict[str, list[str]] = {}
        metadata: dict[str, list[dict[str, Any]]] = {}
        tokens: dict[str, RevisionToken] = {}
        for row in self._fts.list_chunks(paths=paths):
            path = row["path"]
            tokens[path] = row["content_hash"]
            texts.setdefault(path, [])
            metadata.setdefault(path, [])
            preamble = (
                self._builder.fields_text(row.get("frontmatter_json"))
                if row.get("is_first_chunk")
                else ""
            )
            text = self._builder.build(
                title=row["title"],
                heading=row["heading"],
                content=row["content"],
                fields_text=preamble,
                is_first_chunk=bool(row.get("is_first_chunk")),
            )
            if not is_embeddable(text):
                continue  # #1087: a blank input never enters the signature
            texts[path].append(text)
            metadata[path].append(
                {**{k: row.get(k) for k in _METADATA_KEYS}, "preamble": preamble}
            )
        return {
            path: _Document(
                path, tokens[path], tuple(texts[path]), tuple(metadata[path])
            )
            for path in tokens
        }

    def reconcile(
        self,
        store: EmbeddingStore,
        paths: Collection[str] | None = None,
        *,
        site: str,
    ) -> ReconcileResult:
        """Bring *paths* (or every document) in the store in line with the keyword index.

        The store's tokens are read before the keyword index: a revision
        the inputs miss was published after that read, so this pass's write
        of that document conflicts instead of replacing it (#1786). Removals run
        first, then each document's re-embed, then the re-tokens, so a bulk
        re-token publish sees the pass's other writes.

        Args:
            store: The store to write.
            paths: The documents to reconcile; ``None`` for a whole-store pass.
            site: Which caller, for the log lines.

        Returns:
            What the pass did; ``conflicts`` holds the paths to re-queue.
        """
        stored = store.tokens()
        docs = self.documents(paths)
        held = set(stored) if paths is None else set(paths) & set(stored)
        state = _Pass(store, stored, ReconcileResult(), site)
        self._remove_unbacked(state, sorted(held - set(docs)))
        retoken: list[_Document] = []
        ordered = sorted(docs)
        for start in range(0, len(ordered), _METADATA_BATCH):
            batch = ordered[start : start + _METADATA_BATCH]
            current = _stored_metadata(store, batch)
            for path in batch:
                held_rows = None if current is None else current.get(path, [])
                if self._reconcile_document(state, docs[path], held_rows):
                    retoken.append(docs[path])
        self._retoken(state, retoken)
        return state.result

    def _remove_unbacked(self, state: _Pass, paths: list[str]) -> None:
        """Remove the vectors of *paths* whose absence is authoritative (#1785).

        An exclusion removes without asking the source. A tombstone keeps:
        the note exists but does not parse, and once fixed its unchanged
        chunks are reused (#1505). Then the source probe decides: *absent*
        removes, *unavailable* keeps (one WARNING for the pass), *present* is
        a keyword-index gap and keeps.
        """
        unavailable = 0
        for path in paths:
            if self._is_excluded(path):
                self._remove(state, path)
                continue
            if self._fts.get_tombstone(path) is not None:
                logger.debug(
                    "embedding_reconcile_kept site=%s path=%s reason=tombstoned",
                    state.site,
                    path,
                )
                continue
            answer = self._source_probe.probe(path).status
            if answer == "absent":
                self._remove(state, path)
            elif answer == "unavailable":
                unavailable += 1
            else:
                logger.warning(
                    "embedding_reconcile_kept site=%s path=%s reason=keyword_gap",
                    state.site,
                    path,
                )
        if unavailable:
            logger.warning(
                "embedding_reconcile_source_unavailable site=%s kept=%d",
                state.site,
                unavailable,
            )

    def _remove(self, state: _Pass, path: str) -> None:
        """Remove *path*'s rows, conditional on the token observed."""
        removed = len(state.store.rows_for(path))
        try:
            outcome = state.store.remove_document(
                path, expected_token=state.stored.get(path)
            )
        except ReadOnlyError:
            raise
        except Exception as exc:
            # Broad by design: each backend raises its own I/O error.
            self._write_failed(state, path, removed, exc)
            return
        if outcome == "conflict":
            self._conflict(state, path)
            return
        state.result.removed += removed

    def _reconcile_document(
        self, state: _Pass, doc: _Document, current: list[dict[str, Any]] | None
    ) -> bool:
        """Remove, embed or keep *doc*; return whether a re-token is pending.

        *current* is the stored rows' metadata, read in a batch; ``None``
        reads the rows here. Vectors are read only for an embed, which
        reuses them. A re-token is returned rather than written, so the pass
        can write them together (:meth:`_retoken`).
        """
        stored = None
        if current is None:
            stored = state.store.rows_for(doc.path)
            current = [dict(r.metadata) for r in stored]
        if not doc.texts:
            if current:
                self._remove(state, doc.path)
            return False
        if current and _signature(current) == _signature(doc.metadata):
            if state.stored.get(doc.path) == doc.token:
                state.result.up_to_date += len(doc.texts)
                return False
            return True
        if stored is None:
            stored = state.store.rows_for(doc.path) if current else []
        self._embed_and_publish(state, doc, stored)
        return False

    def _embed_and_publish(
        self, state: _Pass, doc: _Document, stored: list[VectorRow]
    ) -> None:
        """Embed *doc*'s changed chunks and replace its rows, keeping them on any failure.

        A chunk whose embedding input one of *stored* was embedded from keeps
        that row's vector: the store's identity fixes the provider, model and
        text format, so the same input embeds to the same vector. Only the
        other inputs go to the provider, each once.
        """
        known = self._stored_inputs(stored)
        misses = [text for text in dict.fromkeys(doc.texts) if text not in known]
        try:
            fresh = self._embed(misses)
        except Exception as exc:
            # Broad by design: providers raise heterogeneous types for a
            # timeout, an oversized batch or an outage. The rows stay.
            state.result.kept += len(doc.texts)
            state.result.retry.add(doc.path)
            logger.warning(
                "embedding_reconcile_skip_doc site=%s path=%s chunks=%d err=%s",
                state.site,
                doc.path,
                len(doc.texts),
                exc,
                exc_info=True,
            )
            return
        try:
            known.update(zip(misses, fresh, strict=True))
            rows = [
                VectorRow(known[text], dict(m))
                for text, m in zip(doc.texts, doc.metadata, strict=True)
            ]
            outcome = state.store.replace_document(
                DocumentRows(doc.path, doc.token, rows),
                expected_token=state.stored.get(doc.path),
            )
        except ValueError as exc:
            # The store refuses another dimension, a NaN or a wrong count
            # before it changes anything, so the old rows stay (#1778).
            state.result.kept += len(doc.texts)
            logger.warning(
                "embedding_reconcile_refused site=%s path=%s err=%s",
                state.site,
                doc.path,
                exc,
            )
            return
        except ReadOnlyError:
            raise
        except Exception as exc:
            # Broad by design: each backend raises its own I/O error.
            self._write_failed(state, doc.path, len(doc.texts), exc)
            return
        if outcome == "conflict":
            self._conflict(state, doc.path)
            return
        missed = set(misses)
        embedded = sum(1 for text in doc.texts if text in missed)
        reused = len(doc.texts) - embedded
        state.result.embedded += embedded
        state.result.reused += reused
        if reused:
            logger.info(
                "embedding_reconcile_reused site=%s path=%s embedded=%d reused=%d",
                state.site,
                doc.path,
                embedded,
                reused,
            )

    def _stored_inputs(self, rows: list[VectorRow]) -> dict[str, Any]:
        """Map the embedding input each of *rows* was embedded from to its vector."""
        return stored_inputs(self._builder, rows)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts* in batches of at most ``batch_size``."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(
                self._provider.embed(texts[start : start + self._batch_size])
            )
        return vectors

    def _retoken(self, state: _Pass, docs: list[_Document]) -> None:
        """Republish *docs*' stored vectors under their new tokens.

        Below :data:`_BULK_RETOKEN` each is a conditional replace; at or
        above it, one generation holding every document's current rows.
        The bulk publish skips the per-document token check, which is safe
        for a store with one writer per location, as the numpy store is. A
        store write that fails keeps the rows and puts the documents in the
        retry set: one document for a replace, all of them for the bulk
        publish.
        """
        if len(docs) < _BULK_RETOKEN:
            for doc in docs:
                rows = state.store.rows_for(doc.path)
                try:
                    outcome = state.store.replace_document(
                        DocumentRows(doc.path, doc.token, rows),
                        expected_token=state.stored.get(doc.path),
                    )
                except ReadOnlyError:
                    raise
                except Exception as exc:
                    # Broad by design: each backend raises its own I/O error.
                    self._write_failed(state, doc.path, len(rows), exc)
                    continue
                if outcome == "conflict":
                    self._conflict(state, doc.path)
                else:
                    state.result.retokened += 1
            return
        new_tokens = {doc.path: doc.token for doc in docs}
        try:
            state.store.publish_generation(
                state.store.identity,
                [
                    DocumentRows(
                        path,
                        new_tokens.get(path, token),
                        _float32_rows(state.store, path),
                    )
                    for path, token in state.store.tokens().items()
                ],
            )
        except ReadOnlyError:
            raise
        except Exception as exc:
            # Broad by design: each backend raises its own I/O error. The
            # generation did not publish, so every document keeps its rows.
            state.result.kept += sum(len(doc.texts) for doc in docs)
            state.result.retry.update(new_tokens)
            logger.warning(
                "embedding_reconcile_bulk_write_failed site=%s paths=%d err=%s",
                state.site,
                len(docs),
                exc,
            )
            return
        state.result.retokened += len(docs)

    def rebuild(
        self,
        store: EmbeddingStore,
        identity: EmbeddingIdentity,
        *,
        donor: Donor | None = None,
        reuse_own: bool = False,
    ) -> RebuildOutcome:
        """Build every document the keyword index holds and publish one generation.

        Per chunk, a vector is copied when the store's own rows (only with
        *reuse_own*) or *donor* hold one for its embedding input, matched by
        :func:`stored_inputs` and of the identity's dimension (#1505); the
        rest are embedded in batches across documents, each input once per
        document. A batch the provider fails, or that the store would refuse,
        is logged and skipped. Into a store that holds no rows, the build is
        then published without it, and the next convergence pass embeds what
        it lacked (#649); a build that would replace a populated store
        publishes nothing unless every batch succeeded, so a failed rebuild
        never shrinks a working store. Nothing is published when no chunk
        has a vector: whether an empty build may replace the store is the
        caller's call.

        Args:
            store: The store to publish into.
            identity: The identity the vectors are embedded at.
            donor: A read-only store to copy matching vectors from.
            reuse_own: Copy matching vectors from *store*'s current rows; off
                by default, so a forced rebuild re-embeds every chunk.

        Returns:
            What the build did.

        Raises:
            StoreUnavailableError: If *donor* can no longer be read; nothing
                is published.
        """
        docs = [d for d in self.documents().values() if d.texts]
        populated = store.count > 0
        found, misses, donated = self._split(
            docs, store, identity.dimension, donor, reuse_own
        )
        embedded_inputs, failed = self._embed_batches(misses, identity.dimension)
        rows, embedded = _assemble(docs, found, embedded_inputs)
        reused = sum(len(v) for v in found.values())
        published = False
        if rows and failed and populated:
            logger.warning(
                "build_embeddings_not_published reason=batch_failed failed_batches=%d",
                failed,
            )
        elif rows:
            store.publish_generation(identity, rows)
            published = True
        if donor is not None:
            logger.info(
                "embeddings_donor_used source=%s reused=%d embedded=%d refused_rows=%d "
                "published=%s",
                donor.source,
                donated,
                embedded,
                donor.refused_rows,
                published,
            )
        attempted = sum(len(d.texts) for d in docs)
        return RebuildOutcome(embedded, reused, attempted, published, failed)

    def preview(
        self,
        store: EmbeddingStore | None,
        identity: EmbeddingIdentity,
        *,
        donor: Donor | None = None,
        reuse_own: bool = False,
    ) -> RebuildPreview:
        """Count what :meth:`rebuild` would copy and embed, calling no provider.

        Args:
            store: The store a build would publish into; ``None`` when it
                holds nothing a build could reuse.
            identity: The identity the vectors are embedded at.
            donor: A read-only store a build would copy matching vectors from.
            reuse_own: Count *store*'s own matching rows as reusable.

        Returns:
            The counts, matched by the same rule as :meth:`rebuild`.

        Raises:
            StoreUnavailableError: If *donor* can no longer be read.
        """
        docs = [d for d in self.documents().values() if d.texts]
        found, misses, _ = self._split(
            docs, store, identity.dimension, donor, reuse_own
        )
        return RebuildPreview(
            chunks=sum(len(d.texts) for d in docs),
            reusable=sum(len(v) for v in found.values()),
            to_embed=len(misses),
        )

    def _split(
        self,
        docs: list[_Document],
        store: EmbeddingStore | None,
        dimension: int,
        donor: Donor | None,
        reuse_own: bool,
    ) -> tuple[
        dict[str, dict[int, array[float]]],
        list[tuple[str, RevisionToken, str, dict[str, Any]]],
        int,
    ]:
        """Sort every chunk into a found vector or a miss to embed.

        Returns:
            Per path, chunk index to its found vector; the inputs to embed,
            each once per document; and how many found vectors came from
            *donor*.
        """
        donated = 0
        own = store if reuse_own and store is not None and store.count > 0 else None
        found: dict[str, dict[int, array[float]]] = {}
        misses: list[tuple[str, RevisionToken, str, dict[str, Any]]] = []
        for doc in docs:
            mine = stored_inputs(self._builder, own.rows_for(doc.path)) if own else {}
            given = donor.vectors_for(doc.path) if donor is not None else {}
            hits, wanted, from_donor = _split_document(doc, mine, given, dimension)
            if hits:
                found[doc.path] = hits
            misses.extend(wanted)
            donated += from_donor
        return found, misses, donated

    def _embed_batches(
        self,
        flat: list[tuple[str, RevisionToken, str, dict[str, Any]]],
        dimension: int,
    ) -> tuple[dict[tuple[str, str], array[float]], int]:
        """Embed *flat* in bounded batches, skipping a batch that fails.

        Per-batch detail logs at DEBUG; INFO carries only a decile heartbeat
        with an ETA (#311). A batch the provider rejects is logged and
        skipped rather than aborting the build (#649).

        Returns:
            Each embedded ``(path, input)`` to its float32 vector, and the
            number of batches that failed.
        """
        total = len(flat)
        started = time.monotonic()
        last_decile = 0
        failed = 0
        kept: dict[tuple[str, str], array[float]] = {}
        for start in range(0, total, self._batch_size):
            end = min(start + self._batch_size, total)
            batch = flat[start:end]
            try:
                vectors = _float32_batch(
                    self._provider.embed([item[2] for item in batch]),
                    dimension,
                    end - start,
                )
            except Exception as exc:
                # Broad by design: providers raise heterogeneous types for an
                # oversized batch (RuntimeError, httpx errors, ...).
                failed += 1
                logger.warning(
                    "build_embeddings_skip_batch chunks_start=%d chunks_end=%d "
                    "chunks_total=%d err=%s",
                    start + 1,
                    end,
                    total,
                    exc,
                    exc_info=True,
                )
            else:
                for (path, _token, text, _meta), vector in zip(
                    batch, vectors, strict=True
                ):
                    kept[(path, text)] = vector
            logger.debug(
                "build_embeddings_embedded_batch chunks_start=%d chunks_end=%d "
                "chunks_total=%d",
                start + 1,
                end,
                total,
            )
            last_decile = _log_progress(end, total, started, last_decile)
        return kept, failed

    @staticmethod
    def _write_failed(state: _Pass, path: str, chunks: int, exc: Exception) -> None:
        """Keep *path*'s rows after a store write failed, for the next flush.

        A store that writes durably raises its own I/O errors (a full disk,
        a lock timeout); like a provider failure, it costs this path, not
        the pass (#1497).
        """
        state.result.kept += chunks
        state.result.retry.add(path)
        logger.warning(
            "embedding_reconcile_write_failed site=%s path=%s err=%s",
            state.site,
            path,
            exc,
        )

    @staticmethod
    def _conflict(state: _Pass, path: str) -> None:
        """Record that another writer changed *path* underneath this pass."""
        state.result.conflicts.add(path)
        logger.info("embedding_reconcile_conflict site=%s path=%s", state.site, path)


def _split_document(
    doc: _Document,
    mine: dict[str, Any],
    given: dict[str, Any],
    dimension: int,
) -> tuple[
    dict[int, array[float]], list[tuple[str, RevisionToken, str, dict[str, Any]]], int
]:
    """Sort one document's chunks: own rows first, then the donor's, else a miss.

    Returns:
        Chunk index to its found vector; the inputs to embed, each once; and
        how many found vectors came from the donor.
    """
    found: dict[int, array[float]] = {}
    misses: list[tuple[str, RevisionToken, str, dict[str, Any]]] = []
    wanted: set[str] = set()
    donated = 0
    for index, (text, meta) in enumerate(zip(doc.texts, doc.metadata, strict=True)):
        vector = _sized(mine.get(text), dimension)
        if vector is None:
            vector = _sized(given.get(text), dimension)
            donated += vector is not None
        if vector is not None:
            found[index] = vector
        elif text not in wanted:
            wanted.add(text)
            misses.append((doc.path, doc.token, text, meta))
    return found, misses, donated


def _float32_rows(store: EmbeddingStore, path: str) -> list[VectorRow]:
    """*path*'s stored rows with their vectors packed as float32.

    A bulk publish holds every row of the store at once; as Python floats
    that is about ten times the matrix (#1768).
    """
    return [VectorRow(array("f", r.vector), r.metadata) for r in store.rows_for(path)]


def _assemble(
    docs: list[_Document],
    found: dict[str, dict[int, array[float]]],
    embedded: dict[tuple[str, str], array[float]],
) -> tuple[list[DocumentRows], int]:
    """Lay out each document's rows in chunk order; a chunk with no vector is left out.

    Returns:
        The documents with at least one row, and how many of their rows were
        embedded (as opposed to found).
    """
    out: list[DocumentRows] = []
    count = 0
    for doc in docs:
        rows: list[VectorRow] = []
        mine = found.get(doc.path, {})
        for index, (text, meta) in enumerate(zip(doc.texts, doc.metadata, strict=True)):
            vector = mine.get(index)
            if vector is None:
                vector = embedded.get((doc.path, text))
                count += vector is not None
            if vector is not None:
                rows.append(VectorRow(vector, dict(meta)))
        if rows:
            out.append(DocumentRows(doc.path, doc.token, tuple(rows)))
    return out, count


def _sized(vector: Sequence[float] | None, dimension: int) -> array[float] | None:
    """Return *vector* as float32 when it fits *dimension* (unknown: 0), else ``None``.

    A donor hands numpy float32 arrays; their bytes are copied at once rather
    than element by element, which costs seconds per hundred thousand chunks.
    """
    if vector is None:
        return None
    if hasattr(vector, "tobytes") and getattr(vector, "dtype", None) == "float32":
        packed = array("f")
        packed.frombytes(vector.tobytes())
    else:
        packed = array("f", vector)
    if dimension > 0 and len(packed) != dimension:
        return None
    return packed


def _log_progress(end: int, total: int, started: float, last_decile: int) -> int:
    """Log a decile heartbeat with an ETA once per decile; return the decile."""
    decile = end * 10 // total
    if decile <= last_decile:
        return last_decile
    elapsed = time.monotonic() - started
    rate = end / elapsed if elapsed > 0 else 0.0
    remaining = (total - end) / rate if rate > 0 else 0.0
    logger.info(
        "build_embeddings_progress pct=%d chunks_done=%d chunks_total=%d "
        "elapsed_s=%.0f eta_s=%.0f",
        decile * 10,
        end,
        total,
        elapsed,
        remaining,
    )
    return decile


def _float32_batch(
    vectors: list[list[float]], dimension: int, expected: int
) -> list[array[float]]:
    """Pack a batch as float32, refusing what the store would refuse.

    A cold build holds every row until it publishes, so rows are kept at the
    store's precision rather than as Python floats, which cost eight times
    the memory. The checks run on the packed values: a value too large for
    float32 becomes infinite there.

    Raises:
        ValueError: If the count, a dimension or a value is wrong.
    """
    if len(vectors) != expected:
        raise ValueError(
            f"The provider returned {len(vectors)} vectors for {expected} texts."
        )
    packed: list[array[float]] = []
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError(
                f"Embedding dimension mismatch: the store holds dimension "
                f"{dimension}, a vector has {len(vector)}."
            )
        row = array("f", vector)
        if not all(math.isfinite(x) for x in row):
            raise ValueError("Embedding values must be finite in float32.")
        packed.append(row)
    return packed
