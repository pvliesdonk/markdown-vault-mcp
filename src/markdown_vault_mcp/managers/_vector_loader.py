"""Shared load-or-self-heal routine for the vector sidecar (#736).

Both :class:`~markdown_vault_mcp.managers.index.IndexManager` and
:class:`~markdown_vault_mcp.managers.search.SearchManager` load the same shared
:class:`~markdown_vault_mcp.vector_index.VectorIndex` from disk and self-heal a
corrupt or incompatible sidecar by rebuilding. This module holds that one
routine so the logic lives in a single place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.vector_index import VectorIndex


def load_or_self_heal(
    *,
    embeddings_path: Path,
    embedding_provider: EmbeddingProvider,
    get_vectors: Callable[[], VectorIndex | None],
    set_vectors: Callable[[VectorIndex], None],
    rebuild: Callable[[], object],
    logger: logging.Logger,
) -> VectorIndex:
    """Load the vector sidecar into the caller's slot, self-healing corruption.

    Returns the cached index if ``get_vectors()`` is already populated.
    Otherwise deserialises it from ``{embeddings_path}.npy``/``.json``; on an
    incompatible, corrupt, or incomplete sidecar
    (``VectorIndexCompatibilityError``, ``VectorIndexCorruptError``,
    ``json.JSONDecodeError``/``ValueError``, ``EOFError``,
    ``FileNotFoundError``) it calls ``rebuild`` and re-reads the slot. A vault
    with no persisted index cold-builds an empty one. Environmental errors
    (e.g. ``PermissionError``) are not caught and propagate.

    Args:
        embeddings_path: Base path for the sidecar files (``.npy``/``.json``
            are appended).
        embedding_provider: Provider attached to a freshly-built index.
        get_vectors: Reads the caller's cached index slot (``None`` if empty).
        set_vectors: Writes a loaded/empty index into the caller's slot.
        rebuild: Zero-arg callback that rebuilds embeddings from scratch and
            repopulates the slot.
        logger: The caller's logger, so records are attributed to the calling
            manager's module.

    Returns:
        A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.

    Raises:
        ValueError: If a self-heal rebuild fails to produce a usable index.
    """
    cached = get_vectors()
    if cached is not None:
        return cached

    from markdown_vault_mcp.vector_index import (
        VectorIndex,
        VectorIndexCompatibilityError,
        VectorIndexCorruptError,
    )

    npy_path = Path(str(embeddings_path) + ".npy")
    if npy_path.exists():
        try:
            set_vectors(VectorIndex.load(embeddings_path, embedding_provider))
            logger.info("Loaded vector index from %s", embeddings_path)
        except VectorIndexCompatibilityError as exc:
            logger.warning("%s Rebuilding embeddings.", exc)
            rebuild()
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
            )
            rebuild()
            if get_vectors() is None:
                raise ValueError(
                    "Failed to rebuild vector index after a corrupt sidecar."
                ) from exc
    else:
        set_vectors(VectorIndex(embedding_provider))
        logger.info("No vector index on disk; created empty VectorIndex")

    result = get_vectors()
    if result is None:
        raise ValueError("Failed to rebuild vector index after a compatibility error.")
    return result
