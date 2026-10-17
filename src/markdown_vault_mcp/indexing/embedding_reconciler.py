"""The one writer of embedding rows (#1768).

Rows come from the keyword index, never from a source file: each
document's chunks become embedding inputs, tagged with the document's
``content_hash`` as its token. Per document the reconciler compares those
inputs and that token with what the store holds and decides: nothing,
re-token (the input is unchanged and only the token moved, or a legacy row
carries no token: the stored vectors are republished, no provider call),
embed, or remove. It embeds outside any store call and publishes with the
token it observed, so a write that crosses another writer's comes back as
a conflict, which the caller re-queues. A path the store holds and the
keyword index has no rows for loses its vectors only on a tombstone, an
exclusion, or a source probe answering *absent* (#1785): *unavailable*
keeps them, logged once per pass, and *present* is a keyword-index gap
that keeps them too.
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
from markdown_vault_mcp.types import DocumentRows, VectorRow

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.interfaces import (
        EmbeddingStore,
        KeywordIndex,
        SourceProbe,
    )
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import EmbeddingIdentity, RevisionToken

logger = logging.getLogger(__name__)

#: At or above this many re-tokened documents in one pass, one generation
#: publish (one matrix copy and one file write) replaces the per-document
#: replaces, each of which copies the whole matrix (#1768).
_BULK_RETOKEN = 64

_METADATA_KEYS = ("path", "title", "folder", "heading", "content", "start_line")


@dataclass
class ReconcileResult:
    """What one pass did, for the caller's aggregate log line.

    Attributes:
        embedded: Chunks embedded and published.
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
    removed: int = 0
    retokened: int = 0
    kept: int = 0
    up_to_date: int = 0
    conflicts: set[str] = field(default_factory=set)
    retry: set[str] = field(default_factory=set)


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


def _signature(rows: Collection[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
    """The chunk identity two row sets are compared by (#665, #1087).

    ``start_line`` participates so that line-shift-only edits (content
    identical, positions moved) still refresh vector metadata; the cost is
    re-embedding that one doc's chunks, and embeddings are deterministic for
    identical content. The preamble participates so an offline summary-only
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
        for path in sorted(docs):
            if self._reconcile_document(state, docs[path]):
                retoken.append(docs[path])
        self._retoken(state, retoken)
        return state.result

    def _remove_unbacked(self, state: _Pass, paths: list[str]) -> None:
        """Remove the vectors of *paths* whose absence is authoritative (#1785).

        A tombstone or an exclusion removes without asking the source; then
        the source probe decides: *absent* removes, *unavailable* keeps (one
        WARNING for the pass), *present* is a keyword-index gap and keeps.
        """
        unavailable = 0
        for path in paths:
            if self._fts.get_tombstone(path) is not None or self._is_excluded(path):
                self._remove(state, path)
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

    def _reconcile_document(self, state: _Pass, doc: _Document) -> bool:
        """Remove, embed or keep *doc*; return whether a re-token is pending.

        A re-token is returned rather than written, so the pass can write
        them together (:meth:`_retoken`).
        """
        current = [dict(r.metadata) for r in state.store.rows_for(doc.path)]
        if not doc.texts:
            if current:
                self._remove(state, doc.path)
            return False
        if current and _signature(current) == _signature(doc.metadata):
            if state.stored.get(doc.path) == doc.token:
                state.result.up_to_date += len(doc.texts)
                return False
            return True
        self._embed_and_publish(state, doc)
        return False

    def _embed_and_publish(self, state: _Pass, doc: _Document) -> None:
        """Embed *doc* and replace its rows, keeping them on any failure."""
        try:
            vectors = self._embed(doc)
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
            rows = [
                VectorRow(v, dict(m))
                for v, m in zip(vectors, doc.metadata, strict=True)
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
        state.result.embedded += len(doc.texts)

    def _embed(self, doc: _Document) -> list[list[float]]:
        """Embed *doc*'s texts in batches of at most ``batch_size``."""
        vectors: list[list[float]] = []
        for start in range(0, len(doc.texts), self._batch_size):
            batch = list(doc.texts[start : start + self._batch_size])
            vectors.extend(self._provider.embed(batch))
        return vectors

    def _retoken(self, state: _Pass, docs: list[_Document]) -> None:
        """Republish *docs*' stored vectors under their new tokens.

        Below :data:`_BULK_RETOKEN` each is a conditional replace; at or
        above it, one generation holding every document's current rows.
        The bulk publish skips the per-document token check, which is safe
        for a store with one writer per location, as the numpy store is.
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
        state.store.publish_generation(
            state.store.identity,
            [
                DocumentRows(
                    path, new_tokens.get(path, token), _float32_rows(state.store, path)
                )
                for path, token in state.store.tokens().items()
            ],
        )
        state.result.retokened += len(docs)

    def rebuild(
        self, store: EmbeddingStore, identity: EmbeddingIdentity
    ) -> tuple[int, int]:
        """Embed every document the keyword index holds and publish one generation.

        Texts are embedded in batches across documents; a batch the
        provider fails, or that the store would refuse, is logged and
        skipped, so a document it touched is published with the rest of
        its rows and re-embedded by the next convergence pass. Nothing is
        published when nothing was embedded: whether an empty build may
        replace the store is the caller's call.

        Args:
            store: The store to publish into.
            identity: The identity the vectors are embedded at.

        Returns:
            ``(embedded, attempted)`` chunk counts.
        """
        docs = [d for d in self.documents().values() if d.texts]
        flat = [
            (doc.path, doc.token, text, meta)
            for doc in docs
            for text, meta in zip(doc.texts, doc.metadata, strict=True)
        ]
        kept = self._embed_batches(flat, identity.dimension)
        if kept:
            store.publish_generation(identity, _group(kept))
        return len(kept), len(flat)

    def _embed_batches(
        self,
        flat: list[tuple[str, RevisionToken, str, dict[str, Any]]],
        dimension: int,
    ) -> list[tuple[str, RevisionToken, VectorRow]]:
        """Embed *flat* in bounded batches, skipping a batch that fails.

        Per-batch detail logs at DEBUG; INFO carries only a decile heartbeat
        with an ETA (#311). A batch the provider rejects is logged and
        skipped rather than aborting the build (#649).
        """
        total = len(flat)
        started = time.monotonic()
        last_decile = 0
        kept: list[tuple[str, RevisionToken, VectorRow]] = []
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
                kept.extend(
                    (path, token, VectorRow(vector, dict(meta)))
                    for (path, token, _text, meta), vector in zip(
                        batch, vectors, strict=True
                    )
                )
            logger.debug(
                "build_embeddings_embedded_batch chunks_start=%d chunks_end=%d "
                "chunks_total=%d",
                start + 1,
                end,
                total,
            )
            last_decile = _log_progress(end, total, started, last_decile)
        return kept

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


def _float32_rows(store: EmbeddingStore, path: str) -> list[VectorRow]:
    """*path*'s stored rows with their vectors packed as float32.

    A bulk publish holds every row of the store at once; as Python floats
    that is about ten times the matrix (#1768).
    """
    return [VectorRow(array("f", r.vector), r.metadata) for r in store.rows_for(path)]


def _group(
    kept: list[tuple[str, RevisionToken, VectorRow]],
) -> list[DocumentRows]:
    """Group kept rows into one document per path, under its token."""
    rows: dict[str, list[VectorRow]] = {}
    tokens: dict[str, RevisionToken] = {}
    for path, token, row in kept:
        rows.setdefault(path, []).append(row)
        tokens[path] = token
    return [DocumentRows(path, tokens[path], group) for path, group in rows.items()]


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
