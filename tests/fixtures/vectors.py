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


def write_store(
    base: Path,
    provider: EmbeddingProvider,
    texts: list[str],
    metadata: list[dict[str, Any]],
    *,
    text_format: str = "v1",
) -> None:
    """Embed *texts* with *provider* and persist them at *base*, as the vault does."""
    from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
    from markdown_vault_mcp.types import (
        ANY_TOKEN,
        DocumentRows,
        EmbeddingIdentity,
        VectorRow,
    )

    store = NumpySidecarBackend().open(
        base, EmbeddingIdentity.of(provider, text_format)
    )
    by_path: dict[str, list[VectorRow]] = {}
    vectors = provider.embed(texts) if texts else []
    for vector, meta in zip(vectors, metadata, strict=True):
        by_path.setdefault(meta["path"], []).append(VectorRow(vector, dict(meta)))
    for path, rows in by_path.items():
        store.replace_document(DocumentRows(path, None, rows), expected_token=ANY_TOKEN)
    store.close()


class StoreWriter:
    """Collect texts and metadata, then embed and persist them with :func:`write_store`."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._texts: list[str] = []
        self._metadata: list[dict[str, Any]] = []

    def add(self, texts: list[str], metadata: list[dict[str, Any]]) -> None:
        """Queue *texts* against *metadata*."""
        self._texts.extend(texts)
        self._metadata.extend(metadata)

    def save(self, base: Path) -> None:
        """Embed everything queued and persist it at *base*."""
        write_store(base, self._provider, self._texts, self._metadata)
