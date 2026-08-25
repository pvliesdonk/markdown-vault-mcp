"""Numpy-backed vector index for semantic (cosine similarity) search."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.embed_text import is_embeddable

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.providers import EmbeddingProvider

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


class VectorIndexCompatibilityError(RuntimeError):
    """Raised when a persisted vector index is incompatible with current provider."""


class VectorIndexCorruptError(RuntimeError):
    """A persisted index is internally inconsistent and must be rebuilt.

    Raised by :meth:`VectorIndex.load` when the embeddings sidecar
    (``.npy``) and the metadata sidecar (``.json``) disagree on row count
    — the residue of a crash between the two atomic sidecar replaces
    (#734). A :class:`RuntimeError` (like its sibling
    :class:`VectorIndexCompatibilityError`), signalling a storage-integrity
    fault rather than a caller value error.
    """


class VectorIndex:
    """Cosine-similarity vector index backed by numpy.

    Stores embedding vectors as a 2-D numpy array (shape ``[n, dim]``)
    with normalised rows so that similarity queries reduce to a dot-product.
    A parallel ``list[dict[str, Any]]`` holds the per-row metadata.

    The index is serialised as two sidecar files:

    - ``{path}.npy`` — the embedding matrix.
    - ``{path}.json`` — row metadata plus index metadata.

    Args:
        provider: Initialised :class:`~markdown_vault_mcp.providers.EmbeddingProvider`
            used to embed query strings at search time.
        embed_text_format: Canonical embedding-text format token (see
            :meth:`~markdown_vault_mcp.embed_text.EmbedTextBuilder.format_token`)
            persisted in the sidecar so a later load can detect a format
            flip. Defaults to ``"v1"`` (raw chunk content).

    Raises:
        ImportError: If ``numpy`` is not installed.
    """

    def __init__(
        self, provider: EmbeddingProvider, *, embed_text_format: str = "v1"
    ) -> None:
        """Initialise an empty VectorIndex.

        Args:
            provider: Embedding provider used for query embedding.
            embed_text_format: Embedding-text format token persisted by
                :meth:`save`.

        Raises:
            ImportError: If ``numpy`` is not installed.
        """
        if not _NUMPY_AVAILABLE:
            raise ImportError(
                "VectorIndex requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        self._provider = provider
        self._embed_text_format = embed_text_format
        # Shape: (0, dim) — will grow with each add() call.
        self._embeddings: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self._metadata: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Class-method constructor
    # ------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        path: Path,
        provider: EmbeddingProvider,
        *,
        expected_embed_text_format: str | None = None,
    ) -> VectorIndex:
        """Load a VectorIndex from sidecar files.

        Args:
            path: Base path; files ``{path}.npy`` and ``{path}.json``
                must exist.
            provider: Embedding provider to attach to the loaded index.
            expected_embed_text_format: When provided, the persisted
                ``embed_text_format`` token (a sidecar without one reads as
                ``"v1"``) must match, or
                :class:`VectorIndexCompatibilityError` is raised so the
                caller's self-heal path re-embeds with the new format.
                ``None`` skips the check.

        Returns:
            A :class:`VectorIndex` populated with the stored embeddings
            and metadata.

        Raises:
            ImportError: If ``numpy`` is not installed.
            FileNotFoundError: If either sidecar file is missing.
            VectorIndexCompatibilityError: If the persisted provider/model or
                embedding-text format does not match the current one.
            VectorIndexCorruptError: If the embeddings and metadata sidecars
                disagree on row count (an incomplete atomic save).
        """
        if not _NUMPY_AVAILABLE:
            raise ImportError(
                "VectorIndex requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )

        npy_path = path.with_suffix(".npy")
        json_path = path.with_suffix(".json")

        embeddings: np.ndarray = np.load(str(npy_path))
        with json_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        metadata: list[dict[str, Any]]
        expected_provider = provider.provider_name
        expected_model = provider.model_name
        persisted_format = "v1"
        if isinstance(payload, list):
            metadata = payload
            logger.warning(
                "VectorIndex.load: legacy metadata format at %s without provider/model identity",
                path,
            )
        else:
            metadata = payload.get("rows", [])
            index_meta = payload.get("index_metadata", {})
            persisted_provider = index_meta.get("provider")
            persisted_model = index_meta.get("model")
            # A sidecar written before embedding-text enrichment carries no
            # format key; it holds raw-content vectors, i.e. format v1.
            persisted_format = index_meta.get("embed_text_format") or "v1"
            if (
                persisted_provider != expected_provider
                or persisted_model != expected_model
            ):
                raise VectorIndexCompatibilityError(
                    "Embedding provider/model mismatch for persisted index at "
                    f"{path}: stored provider={persisted_provider!r}, "
                    f"stored model={persisted_model!r}, "
                    f"current provider={expected_provider!r}, "
                    f"current model={expected_model!r}."
                )

        if (
            expected_embed_text_format is not None
            and persisted_format != expected_embed_text_format
        ):
            raise VectorIndexCompatibilityError(
                "Embedding-text format mismatch for persisted index at "
                f"{path}: stored format={persisted_format!r}, "
                f"current format={expected_embed_text_format!r}."
            )

        # Gate the parity invariant before constructing the index, so a
        # mismatched pair never yields even a transient inconsistent object.
        if embeddings.shape[0] != len(metadata):
            raise VectorIndexCorruptError(
                "VectorIndex.load: sidecar row count mismatch at "
                f"{path}: {embeddings.shape[0]} embedding rows vs "
                f"{len(metadata)} metadata rows (incomplete atomic save)."
            )

        index = cls(provider, embed_text_format=persisted_format)
        index._embeddings = embeddings
        index._metadata = metadata

        logger.info(
            "VectorIndex.load: loaded %d vectors from %s",
            len(metadata),
            path,
        )
        return index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of embedding rows currently stored.

        Returns:
            Integer row count.
        """
        return len(self._metadata)

    def chunks_by_path(self) -> dict[str, list[dict[str, Any]]]:
        """Group stored metadata rows by document path.

        Used to diff the vector index against the FTS chunk set at boot
        (#665).

        Returns:
            Mapping of document path to copies of the stored metadata
            dicts for that document, in storage order.
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self._metadata:
            grouped.setdefault(row.get("path", ""), []).append(dict(row))
        return grouped

    def add(self, texts: list[str], metadata: list[dict[str, Any]]) -> int:
        """Embed ``texts`` and append rows to the index.

        Vectors are L2-normalised before storage so that similarity
        queries can use a plain dot product.

        If the provider raises during embedding, no state is modified —
        the index remains exactly as it was before the call.

        Args:
            texts: Texts to embed.  Length must equal ``len(metadata)``.
            metadata: Per-row dicts (keys: ``path``, ``title``, ``folder``,
                ``heading``, ``content``, ``start_line``, and ``preamble``
                — the first-chunk searchable-field text, ``""`` otherwise).
                Each dict is stored verbatim.  ``start_line`` is used by
                callers to resolve ties when sorting sections of the same
                document; ``preamble`` feeds the boot-convergence signature.

        Returns:
            Number of rows added.

        Raises:
            ValueError: If ``len(texts) != len(metadata)``, ``texts`` is
                empty, or the new vectors' dimension does not match the
                dimension of vectors already stored in the index.
            RuntimeError: Propagated from the embedding provider.
        """
        if len(texts) != len(metadata):
            raise ValueError(
                f"texts and metadata must have the same length "
                f"(got {len(texts)} vs {len(metadata)})"
            )
        if not texts:
            return 0

        # Embed first — do NOT mutate state until this succeeds.
        raw: list[list[float]] = self._provider.embed(texts)
        return self.add_vectors(raw, metadata)

    def add_vectors(
        self, raw_vectors: list[list[float]], metadata: list[dict[str, Any]]
    ) -> int:
        """Append pre-computed embedding vectors to the index.

        Accepts raw (un-normalised) float vectors as returned by
        :meth:`~markdown_vault_mcp.providers.EmbeddingProvider.embed`.
        Vectors are L2-normalised before storage.

        Use this when embeddings have already been computed outside a
        critical lock section — the caller embeds outside the lock, then
        calls ``add_vectors`` inside the lock to perform only the fast
        numpy mutation.

        Args:
            raw_vectors: Pre-computed embeddings as a list of float lists
                (shape ``[n, dim]``).  Length must equal ``len(metadata)``.
            metadata: Per-row dicts (keys: ``path``, ``title``, ``folder``,
                ``heading``, ``content``, ``start_line``, and ``preamble``
                — the first-chunk searchable-field text, ``""`` otherwise).
                Each dict is stored verbatim.  ``start_line`` is used by
                callers to resolve ties when sorting sections of the same
                document; ``preamble`` feeds the boot-convergence signature.

        Returns:
            Number of rows added.

        Raises:
            ValueError: If ``len(raw_vectors) != len(metadata)`` or the
                vector dimension does not match the dimension of vectors
                already stored in the index.
        """
        if len(raw_vectors) != len(metadata):
            raise ValueError(
                f"raw_vectors and metadata must have the same length "
                f"(got {len(raw_vectors)} vs {len(metadata)})"
            )
        if not raw_vectors:
            return 0

        vectors = np.array(raw_vectors, dtype=np.float32)

        # L2-normalise each row.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        # Avoid division by zero for zero-magnitude vectors.
        norms = np.where(norms == 0, 1.0, norms)
        # np.linalg.norm + np.where(..., 1.0, ...) widen to float64; cast back
        # to float32 so _embeddings keeps its declared dtype invariant.
        vectors = (vectors / norms).astype(np.float32, copy=False)

        if self._embeddings.size == 0:
            self._embeddings = vectors
        else:
            existing_dim = self._embeddings.shape[1]
            new_dim = vectors.shape[1]
            if new_dim != existing_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: existing index has dim={existing_dim}, "
                    f"but new vectors have dim={new_dim}. "
                    "All embeddings must use the same model and dimension."
                )
            self._embeddings = np.vstack([self._embeddings, vectors])

        self._metadata.extend(metadata)

        logger.debug(
            "VectorIndex.add_vectors: added %d rows (total=%d)",
            len(raw_vectors),
            self.count,
        )
        return len(raw_vectors)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top-k most similar chunks for ``query``.

        Every stored chunk is scored regardless of *limit* (the similarity
        pass is a full dot product), so *predicate* costs nothing extra and
        selects the top-k **within** the eligible rows instead of leaving a
        caller to discard rows the cap already admitted — a scope whose
        chunks all rank below the cap would otherwise come back empty
        however many of them match (#1108).

        Args:
            query: Natural-language search string.
            limit: Maximum number of results to return.  Zero or less
                returns ``[]``.
            predicate: Optional row filter applied to each candidate's
                metadata dict before the cap. Rows it rejects are skipped;
                ``None`` admits every row. Called at most once per stored
                chunk, in descending-score order, so keep it cheap.

        Returns:
            List of metadata dicts ordered by descending cosine similarity.
            Each dict contains all stored metadata fields plus a ``score``
            key (float in ``[-1, 1]``).

        Raises:
            RuntimeError: Propagated from the embedding provider.
        """
        if self.count == 0:
            logger.debug("VectorIndex.search: index empty, returning []")
            return []

        if limit <= 0:
            # The collect-until-full loop below checks its stop condition
            # after appending, so a non-positive cap has to be refused here
            # or it would yield one row.
            logger.debug("VectorIndex.search: limit=%d, returning []", limit)
            return []

        if not is_embeddable(query):
            # A blank or whitespace-only query would reach the provider as
            # [""], which every OpenAI-compatible endpoint rejects with a
            # hard HTTP 400 (#1111).  SearchManager guards its own channels
            # so hybrid takes the same path; this backstops a library
            # consumer calling VectorIndex directly.
            logger.debug("VectorIndex.search: blank query, returning []")
            return []

        logger.debug(
            "VectorIndex.search: query=%r limit=%d index_size=%d",
            query,
            limit,
            self.count,
        )

        raw = self._provider.embed([query])
        q_vec = np.array(raw[0], dtype=np.float32)

        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm

        # Dot product against normalised rows = cosine similarity.
        scores: np.ndarray = self._embeddings @ q_vec

        # argsort descending, then walk it until *limit* eligible rows are
        # collected.  Walking (rather than slicing to k up front) is what
        # lets *predicate* narrow the pool without narrowing the result.
        results: list[dict[str, Any]] = []
        for idx in np.argsort(scores)[::-1]:
            i = int(idx)
            row = self._metadata[i]
            if predicate is not None and not predicate(row):
                continue
            entry = dict(row)
            entry["score"] = float(scores[i])
            results.append(entry)
            if len(results) >= limit:
                break

        logger.debug("VectorIndex.search: returning %d results", len(results))
        return results

    def search_by_path(
        self,
        path: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top-k most similar chunks from *other* documents.

        Looks up the stored embedding vectors for ``path``, averages them
        if multiple chunks exist, and computes cosine similarity against
        all chunks from other documents (excludes self-matches).

        Args:
            path: Relative document path whose stored vectors to use.
            limit: Maximum number of results to return.  Zero or less
                returns ``[]``.
            predicate: Optional row filter applied before the cap, with the
                same contract as :meth:`search`.

        Returns:
            List of metadata dicts ordered by descending cosine similarity.
            Each dict contains all stored metadata fields plus a ``score``
            key.  Returns ``[]`` if ``path`` has no stored embeddings or
            the index is empty.
        """
        if self.count == 0 or limit <= 0:
            # `candidates[:min(limit, len(candidates))]` below would read a
            # negative cap as a negative slice and drop rows from the *end*,
            # so the cap is refused here, matching :meth:`search`.
            return []

        # Gather indices for all chunks belonging to this document.
        doc_indices = [i for i, m in enumerate(self._metadata) if m.get("path") == path]
        if not doc_indices:
            return []

        # Average the document's chunk vectors to get a single query vector.
        doc_vectors = self._embeddings[doc_indices]
        q_vec = np.mean(doc_vectors, axis=0)
        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm

        # Dot product against all stored vectors.
        scores: np.ndarray = self._embeddings @ q_vec

        # Build (score, index) pairs excluding chunks from the same document
        # and any row the caller's predicate rejects.
        candidates: list[tuple[float, int]] = []
        for i, score in enumerate(scores):
            row = self._metadata[i]
            if row.get("path") == path:
                continue
            if predicate is not None and not predicate(row):
                continue
            candidates.append((float(score), i))

        # Sort descending by score and take top-k.
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[: min(limit, len(candidates))]

        results: list[dict[str, Any]] = []
        for score, idx in top:
            entry = dict(self._metadata[idx])
            entry["score"] = score
            results.append(entry)

        logger.debug("VectorIndex.search_by_path: %s → %d results", path, len(results))
        return results

    def delete_by_path(self, path: str) -> int:
        """Remove all rows for a given document path.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            Number of rows removed.
        """
        if self.count == 0:
            return 0

        keep_mask = np.array(
            [m.get("path") != path for m in self._metadata], dtype=bool
        )
        removed = int(np.sum(~keep_mask))

        if removed == 0:
            return 0

        if np.all(~keep_mask):
            # All rows belong to this path — reset to empty.
            self._embeddings = np.empty((0, 0), dtype=np.float32)
            self._metadata = []
        else:
            self._embeddings = self._embeddings[keep_mask]
            self._metadata = [
                m for m, keep in zip(self._metadata, keep_mask, strict=True) if keep
            ]

        logger.debug(
            "VectorIndex.delete_by_path: removed %d rows for %s (remaining=%d)",
            removed,
            path,
            self.count,
        )
        return removed

    def save(self, path: Path) -> None:
        """Persist the index to sidecar files.

        Writes ``{path}.npy`` (the embedding matrix) and ``{path}.json``
        (the metadata list).  An empty index is saved as a zero-row array.

        Each sidecar is written to a temp file in the same directory and
        atomically ``replace()``-d onto its final path (mirrors
        :meth:`tracker.ChangeTracker._save_state`), so an interrupted write
        can never corrupt a previously persisted index.

        Args:
            path: Base path for the sidecar files.  Parent directory must
                exist.
        """
        npy_path = path.with_suffix(".npy")
        json_path = path.with_suffix(".json")

        # An empty index is saved as a zero-shape array so load() can always
        # read it back.
        array_to_save = (
            np.empty((0, 0), dtype=np.float32)
            if self._embeddings.size == 0
            else self._embeddings
        )

        payload = {
            "rows": self._metadata,
            "index_metadata": {
                "provider": self._provider.provider_name,
                "model": self._provider.model_name,
                "dimension": (
                    int(self._embeddings.shape[1]) if self._embeddings.ndim == 2 else 0
                ),
                "embed_text_format": self._embed_text_format,
            },
        }

        # Write each sidecar to a temp file in the same directory, then
        # atomically replace the final file. A mid-write interruption can then
        # never corrupt a previously persisted index (mirrors
        # tracker._save_state). np.save appends ".npy" unless the path already
        # ends in it, so the temp file uses a ".npy" suffix and we write
        # through the open fd to avoid any suffix ambiguity.
        npy_fd, npy_tmp = tempfile.mkstemp(dir=npy_path.parent, suffix=".npy")
        try:
            with os.fdopen(npy_fd, "wb") as fh:
                np.save(fh, array_to_save)
            Path(npy_tmp).replace(npy_path)
        except BaseException:
            Path(npy_tmp).unlink(missing_ok=True)
            raise

        json_fd, json_tmp = tempfile.mkstemp(dir=json_path.parent, suffix=".tmp")
        try:
            with os.fdopen(json_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            Path(json_tmp).replace(json_path)
        except BaseException:
            Path(json_tmp).unlink(missing_ok=True)
            raise

        logger.info(
            "VectorIndex.save: saved %d vectors to %s",
            self.count,
            path,
        )
