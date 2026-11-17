"""Private, repeatable scratch representation of a rebuilt generation."""

from __future__ import annotations

import base64
import json
import tempfile
from array import array
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.types import DocumentRows, VectorRow

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from types import TracebackType
    from typing import TextIO

    from markdown_vault_mcp.indexing.embedding_reconciler import _Document


def _pack(vector: array[float]) -> str:
    return base64.b64encode(vector.tobytes()).decode("ascii")


def _unpack(value: str) -> array[float]:
    result: array[float] = array("f")
    result.frombytes(base64.b64decode(value))
    return result


class EmbeddingStage:
    """Stage source records, misses and results in a private temporary directory."""

    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="embedding-stage-")
        self._root = Path(self._temp.name)
        self._readers: set[TextIO] = set()
        try:
            self._docs = (self._root / "documents").open("w", encoding="utf-8")
            self._misses = (self._root / "misses").open("w", encoding="utf-8")
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        self.miss_count = 0

    def __enter__(self) -> EmbeddingStage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        failure = exc
        streams = [
            getattr(self, "_docs", None),
            getattr(self, "_misses", None),
            *self._readers,
        ]
        actions = [stream.close for stream in streams if stream is not None]
        actions.append(self._temp.cleanup)
        for action in actions:
            try:
                action()
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note(f"Scratch cleanup also failed: {error!r}")
        self._readers.clear()
        if exc is None and failure is not None:
            raise failure

    def add(
        self, doc: _Document, hits: dict[int, array[float]], wanted: list[str]
    ) -> None:
        """Write one classified document and its ordered unique misses."""
        counts = Counter(doc.texts)
        json.dump(
            [
                doc.path,
                doc.token,
                doc.texts,
                doc.metadata,
                {str(k): _pack(v) for k, v in hits.items()},
                wanted,
            ],
            self._docs,
        )
        self._docs.write("\n")
        for text in wanted:
            self._misses.write(json.dumps([text, counts[text]]) + "\n")
        self.miss_count += len(wanted)

    def finish(self) -> None:
        """Close classification files before reading them."""
        self._docs.close()
        self._misses.close()

    def misses(self) -> Generator[tuple[str, int]]:
        """Yield misses in provider order with their chunk fanout."""
        with (self._root / "misses").open(encoding="utf-8") as stream:
            for line in stream:
                text, count = json.loads(line)
                yield text, count

    def write_results(self, results: Generator[array[float] | None]) -> None:
        """Write one result or failure marker for each miss."""
        with (
            closing(results),
            (self._root / "results").open("w", encoding="utf-8") as stream,
        ):
            for vector in results:
                stream.write(
                    json.dumps(None if vector is None else _pack(vector)) + "\n"
                )

    def __iter__(self) -> Iterator[DocumentRows]:
        docs = (self._root / "documents").open(encoding="utf-8")
        self._readers.add(docs)
        try:
            results = (self._root / "results").open(encoding="utf-8")
            self._readers.add(results)
            try:
                yield from self._replay(docs, results)
            finally:
                results.close()
                self._readers.discard(results)
        finally:
            docs.close()
            self._readers.discard(docs)

    @staticmethod
    def _replay(docs: TextIO, results: TextIO) -> Iterator[DocumentRows]:
        for line in docs:
            path, token, texts, metadata, hits, wanted = json.loads(line)
            fresh: dict[str, Any] = {}
            for text in wanted:
                value = json.loads(next(results))
                if value is not None:
                    fresh[text] = _unpack(value)
            rows = []
            for index, (text, meta) in enumerate(zip(texts, metadata, strict=True)):
                value = hits.get(str(index))
                vector = _unpack(value) if value is not None else fresh.get(text)
                if vector is not None:
                    rows.append(VectorRow(vector, meta))
            if rows:
                yield DocumentRows(path, token, tuple(rows))
