"""Private, repeatable scratch representation of a rebuilt generation."""

from __future__ import annotations

import base64
import json
import logging
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


logger = logging.getLogger(__name__)


def _pack(vector: array[float]) -> str:
    return base64.b64encode(vector.tobytes()).decode("ascii")


def _unpack(value: str) -> array[float]:
    result: array[float] = array("f")
    result.frombytes(base64.b64decode(value))
    return result


class EmbeddingStage:
    """Stage source records, misses and results in a private temporary directory."""

    def __init__(self, directory: Path | None = None) -> None:
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(
            prefix="embedding-stage-", dir=directory
        )
        self._root = Path(self._temp.name)
        self._readers: set[TextIO] = set()
        try:
            self._docs = (self._root / "documents").open("w", encoding="utf-8")
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        self.miss_count = 0
        self._opaque: list[tuple[Any, Any, Any]] = []

    def __enter__(self) -> EmbeddingStage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        streams = [getattr(self, "_docs", None), *self._readers]
        actions = [stream.close for stream in streams if stream is not None]
        actions.append(self._temp.cleanup)
        for action in actions:
            try:
                action()
            except Exception as error:
                logger.warning(
                    "embedding_stage_cleanup_failed path=%s err=%s", self._root, error
                )
        self._readers.clear()

    def add(
        self, doc: _Document, hits: dict[int, array[float]], wanted: list[str]
    ) -> None:
        """Write one classified document and its ordered unique misses."""
        scalar = (type(None), bool, int, float, str)
        safe = (
            type(doc.path) is str
            and type(doc.token) in (str, type(None))
            and all(
                type(key) is str and type(value) in scalar
                for meta in doc.metadata
                for key, value in meta.items()
            )
        )
        if safe:
            identity: Any = [doc.path, doc.token, doc.metadata]
            reference = None
        else:
            reference = len(self._opaque)
            self._opaque.append((doc.path, doc.token, doc.metadata))
            identity = None
        json.dump(
            [
                identity,
                reference,
                doc.texts,
                {str(k): _pack(v) for k, v in hits.items()},
                wanted,
            ],
            self._docs,
        )
        self._docs.write("\n")
        self.miss_count += len(wanted)

    def finish(self) -> None:
        """Close the classification file before reading it."""
        self._docs.close()

    def _records(self) -> Generator[tuple[Any, Any, Any, Any, Any, Any]]:
        stream = (self._root / "documents").open(encoding="utf-8")
        self._readers.add(stream)
        try:
            for line in stream:
                identity, reference, texts, hits, wanted = json.loads(line)
                path, token, metadata = (
                    self._opaque[reference] if reference is not None else identity
                )
                yield path, token, texts, metadata, hits, wanted
        finally:
            stream.close()
            self._readers.discard(stream)

    def misses(self) -> Generator[tuple[str, int]]:
        """Yield misses in provider order with their chunk fanout."""
        with closing(self._records()) as records:
            for _path, _token, texts, _metadata, _hits, wanted in records:
                counts = Counter(texts)
                for text in wanted:
                    yield text, counts[text]

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
        results = (self._root / "results").open(encoding="utf-8")
        self._readers.add(results)
        try:
            with closing(self._records()) as records:
                for path, token, texts, metadata, hits, wanted in records:
                    fresh: dict[str, Any] = {}
                    for text in wanted:
                        value = json.loads(next(results))
                        if value is not None:
                            fresh[text] = _unpack(value)
                    rows = []
                    for index, (text, meta) in enumerate(
                        zip(texts, metadata, strict=True)
                    ):
                        value = hits.get(str(index))
                        vector = (
                            _unpack(value) if value is not None else fresh.get(text)
                        )
                        if vector is not None:
                            rows.append(VectorRow(vector, dict(meta)))
                    if rows:
                        yield DocumentRows(path, token, tuple(rows))
        finally:
            results.close()
            self._readers.discard(results)
