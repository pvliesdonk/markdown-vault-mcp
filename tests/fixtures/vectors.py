"""Vector helpers shared by tests: grouped metadata and legacy sidecars (#1778)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.interfaces import VectorReader
    from markdown_vault_mcp.providers import EmbeddingProvider


def metadata_by_path(store: VectorReader) -> dict[str, list[dict[str, Any]]]:
    """Return *store*'s row metadata grouped by path, documents with rows only."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in store.tokens():
        rows = store.rows_for(path)
        if rows:
            grouped[path] = [dict(row.metadata) for row in rows]
    return grouped


def write_legacy_sidecar(
    base: Path,
    vectors: list[list[float]],
    metadata: list[dict[str, Any]],
    *,
    provider: EmbeddingProvider,
    text_format: str | None = "v1",
) -> None:
    """Write the ``{base}.npy`` + ``{base}.json`` pair released versions wrote.

    Rows are normalised as the released index stored them. ``text_format=None``
    omits the format key, as sidecars written before it existed did.
    """
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.size:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms == 0, 1.0, norms)
    else:
        matrix = np.empty((0, 0), dtype=np.float32)
    index_metadata: dict[str, Any] = {
        "provider": provider.provider_name,
        "model": provider.model_name,
        "provider_variant": provider.provider_variant,
        "dimension": int(matrix.shape[1]) if matrix.ndim == 2 else 0,
    }
    if text_format is not None:
        index_metadata["embed_text_format"] = text_format
    base.parent.mkdir(parents=True, exist_ok=True)
    np.save(base.with_suffix(".npy"), matrix)
    base.with_suffix(".json").write_text(
        json.dumps({"rows": metadata, "index_metadata": index_metadata})
    )
