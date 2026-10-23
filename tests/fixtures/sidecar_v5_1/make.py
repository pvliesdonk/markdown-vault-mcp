"""Write the v5.1.0 sidecar fixture with v5.1.0's own ``VectorIndex.save`` (#1505).

Run from the repository root, with the project environment::

    uv run python tests/fixtures/sidecar_v5_1/make.py

It loads ``src/markdown_vault_mcp/vector_index.py`` as tagged ``v5.1.0``
(``git show``) into a scratch module, takes the embedding inputs and row
metadata the current keyword index builds for ``notes.NOTES``, embeds them with
the test suite's deterministic ``MockEmbeddingProvider``, and saves
``index-db-embeddings.json`` and ``index-db-embeddings.npy`` here: the bytes a
v5.1.0 server leaves beside ``index.db``, but for the trailing newline the
repository's end-of-file hook adds to the JSON (still the same JSON). Commit the
output; rerun only to change the notes.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


def _v5_1_vector_index() -> ModuleType:
    source = subprocess.run(
        ["git", "show", "v5.1.0:src/markdown_vault_mcp/vector_index.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "vector_index_v5_1.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location("vector_index_v5_1", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def main() -> None:
    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.indexing.embedding_reconciler import EmbeddingReconciler
    from markdown_vault_mcp.scanner import HeadingChunker, parse_note
    from tests.conftest import MockEmbeddingProvider
    from tests.fixtures.sidecar_v5_1.notes import NOTES

    fts = FTSIndex(db_path=":memory:")
    with tempfile.TemporaryDirectory() as scratch:
        notes = Path(scratch)
        for name, text in sorted(NOTES.items()):
            (notes / name).parent.mkdir(parents=True, exist_ok=True)
            (notes / name).write_text(text)
            fts.upsert_note(parse_note(notes / name, notes, HeadingChunker()))
    provider = MockEmbeddingProvider()
    reconciler = EmbeddingReconciler(
        fts=fts,
        provider=provider,
        embed_builder=EmbedTextBuilder(),
        batch_size=16,
        source_probe=None,  # type: ignore[arg-type]
        is_excluded=lambda _p: False,
    )
    texts: list[str] = []
    metadata: list[dict] = []
    for doc in reconciler.documents().values():
        texts.extend(doc.texts)
        metadata.extend(doc.metadata)
    index = _v5_1_vector_index().VectorIndex(provider, embed_text_format="v1")
    index.add(texts, metadata)
    index.save(HERE / "index-db-embeddings")
    fts.close()
    logger.info(
        "sidecar_fixture_written rows=%d provider=%s model=%s",
        len(texts),
        provider.provider_name,
        provider.model_name,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
