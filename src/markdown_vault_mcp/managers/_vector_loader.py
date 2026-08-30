"""Shared load-or-self-heal routine for the vector sidecar (#736).

Both :class:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager`
(composed into :class:`~markdown_vault_mcp.managers.index.IndexManager`,
#1157) and :class:`~markdown_vault_mcp.managers.search.SearchManager` load the
same shared :class:`~markdown_vault_mcp.vector_index.VectorIndex` from disk and
self-heal a corrupt or incompatible sidecar by rebuilding. This module holds
that one routine so the logic lives in a single place.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp.interfaces import VectorStore
    from markdown_vault_mcp.providers import EmbeddingProvider


def load_or_self_heal(
    *,
    embeddings_path: Path,
    embedding_provider: EmbeddingProvider,
    get_vectors: Callable[[], VectorStore | None],
    set_vectors: Callable[[VectorStore], None],
    rebuild: Callable[[], object],
    logger: logging.Logger,
    embed_text_format: str = "v1",
) -> VectorStore:
    """Load the vector sidecar into the caller's slot, self-healing corruption.

    Returns the cached index if ``get_vectors()`` is already populated.
    Otherwise deserialises it from the ``.npy``/``.json`` sidecars derived via
    ``Path.with_suffix`` (a base that already carries an extension has it
    replaced, not doubled); on an
    incompatible, corrupt, or incomplete sidecar
    (``VectorIndexCompatibilityError``, ``VectorIndexCorruptError``,
    ``json.JSONDecodeError``/``ValueError``, ``EOFError``,
    ``FileNotFoundError``) it calls ``rebuild`` and re-reads the slot. A vault
    with no persisted index cold-builds an empty one. Environmental errors
    (e.g. ``PermissionError``) are not caught and propagate.

    Args:
        embeddings_path: Base path for the sidecar files; the ``.npy``/``.json``
            paths are derived with ``Path.with_suffix`` as described above.
        embedding_provider: Provider attached to a freshly-built index.
        get_vectors: Reads the caller's cached index slot (``None`` if empty).
        set_vectors: Writes a loaded/empty index into the caller's slot.
        rebuild: Zero-arg callback that rebuilds embeddings from scratch and
            repopulates the slot.
        logger: The caller's logger, so records are attributed to the calling
            manager's module.
        embed_text_format: The current embedding-text format token (see
            :meth:`~markdown_vault_mcp.embed_text.EmbedTextBuilder.format_token`).
            A persisted sidecar with a different token (absent key reads as
            ``"v1"``) raises ``VectorIndexCompatibilityError`` inside
            :meth:`VectorIndex.load` and routes to the same rebuild path as
            a provider/model mismatch; a fresh empty index is stamped with
            this token so its save records the format.

    Returns:
        The loaded store.  This function is the backend factory, so it names
        :class:`~markdown_vault_mcp.vector_index.VectorIndex` concretely when
        constructing one, but hands it back through the
        :class:`~markdown_vault_mcp.interfaces.VectorStore` seam its callers
        hold (#1230).

    Raises:
        ValueError: If a self-heal rebuild completes but leaves the slot empty.
        Exception: Any exception raised by the ``rebuild`` callback is logged
            at ERROR and re-raised unchanged.
    """
    cached = get_vectors()
    if cached is not None:
        return cached

    from markdown_vault_mcp.vector_index import (
        VectorIndex,
        VectorIndexCompatibilityError,
        VectorIndexCorruptError,
    )

    def _run_rebuild() -> None:
        """Run the rebuild callback; if it raises, log at ERROR and re-raise the original exception without wrapping.

        Ties a rebuild failure (e.g. provider/FTS error inside the rebuild)
        to the corruption event in the log instead of letting it propagate
        unlogged (#735).
        """
        try:
            rebuild()
        # Broad by design: the rebuild callback (build_embeddings / a provider
        # call) raises heterogeneous types; log then re-raise unchanged. Mirrors
        # IndexManager.build_embeddings' own broad per-batch except (#735).
        except Exception:
            logger.error(
                "vector_index_rebuild_failed path=%s",
                embeddings_path,
                exc_info=True,
            )
            raise

    # Derive the sidecar path exactly as VectorIndex.load/save do
    # (Path.with_suffix), so a base path that already carries an extension
    # (e.g. EMBEDDINGS_PATH=embeddings.npy) resolves to the same file that was
    # saved. A string-append would probe embeddings.npy.npy, miss the real
    # store, cold-build an empty index, and later overwrite the store (#819).
    npy_path = embeddings_path.with_suffix(".npy")
    if npy_path.exists():
        try:
            set_vectors(
                VectorIndex.load(
                    embeddings_path,
                    embedding_provider,
                    expected_embed_text_format=embed_text_format,
                )
            )
            logger.info("Loaded vector index from %s", embeddings_path)
        except VectorIndexCompatibilityError as exc:
            logger.warning("%s Rebuilding embeddings.", exc, exc_info=True)
            _run_rebuild()
            # An empty-but-populated index is accepted, not an error: the
            # degraded "every batch failed" case is surfaced by
            # IndexManager.build_embeddings' build_embeddings_all_batches_failed warning
            # (#649/#735). This guard only catches a rebuild that left the slot
            # entirely unpopulated (None).
            if get_vectors() is None:
                raise ValueError(
                    "Failed to rebuild vector index after a compatibility error."
                ) from exc
        except (
            VectorIndexCorruptError,
            json.JSONDecodeError,
            ValueError,
            EOFError,
            FileNotFoundError,
        ) as exc:
            # A corrupt/incomplete sidecar wedges every boot until a rebuild
            # (#720/#734): an interrupted save can leave a truncated, zero-byte,
            # or count-mismatched file. Self-heal by routing to the same rebuild
            # path as a compatibility mismatch.
            #   VectorIndexCorruptError — embeddings/metadata row-count mismatch
            #     (#734). A RuntimeError, so this entry is load-bearing — it is
            #     NOT covered by the ValueError arm below.
            #   ValueError — truncated/garbage .json (JSONDecodeError is a
            #     ValueError subclass) and corrupt/bad-version .npy.
            #   EOFError — a zero-byte .npy (numpy raises EOFError, not
            #     ValueError/OSError).
            #   FileNotFoundError — a missing .json while the .npy exists (an
            #     incomplete pair); the .npy-exists guard means it can only be
            #     the gone .json sidecar.
            # Deliberately NOT broad OSError: PermissionError / disk-IO errors
            # are environmental — a destructive rebuild would be futile and mask
            # the real problem, so they must propagate.
            logger.warning(
                "vector_index_corrupt_rebuilding path=%s error=%s",
                embeddings_path,
                exc,
                exc_info=True,
            )
            _run_rebuild()
            if get_vectors() is None:
                raise ValueError(
                    "Failed to rebuild vector index after a corrupt sidecar."
                ) from exc
    else:
        set_vectors(
            VectorIndex(embedding_provider, embed_text_format=embed_text_format)
        )
        logger.info("No vector index on disk; created empty VectorIndex")

    result = get_vectors()
    if result is None:
        raise ValueError(
            "Failed to obtain a usable vector index (slot empty after load/rebuild)."
        )
    return result
