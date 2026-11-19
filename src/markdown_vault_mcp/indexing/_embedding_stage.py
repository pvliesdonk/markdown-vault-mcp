"""Private, repeatable scratch representation of a rebuilt generation."""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import threading
from array import array
from collections import Counter
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from markdown_vault_mcp.types import DocumentRows, VectorRow

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
    from types import TracebackType

    from markdown_vault_mcp.indexing.embedding_reconciler import _Document


logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def _pack(vector: array[float]) -> str:
    return base64.b64encode(vector.tobytes()).decode("ascii")


def _unpack(value: str) -> array[float]:
    result: array[float] = array("f")
    result.frombytes(base64.b64decode(value))
    return result


def _misses(texts: Iterable[str], hits: Mapping[int, Any]) -> list[str]:
    """Return ordered unique inputs without a reusable vector."""
    return list(
        dict.fromkeys(text for index, text in enumerate(texts) if index not in hits)
    )


class _Spool:
    """One anonymous scratch file of lines that any number of readers replay.

    The OS owns the file after creation; a POSIX fallback may briefly create
    a name before unlinking it.
    Readers share the handle; each keeps its own offset, and the seek and
    read of a line happen under one lock.
    """

    def __init__(self, directory: Path) -> None:
        # Owned for this object's life and closed by close().
        self._file = tempfile.TemporaryFile(  # noqa: SIM115
            prefix="embedding-stage-", dir=directory
        )
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        """Write *line* and a newline at the end."""
        with self._lock:
            self._file.seek(0, os.SEEK_END)
            self._file.write(line.encode("utf-8") + b"\n")

    def flush(self) -> None:
        """Push buffered writes to the file."""
        with self._lock:
            self._file.flush()

    def read(self, offset: int) -> bytes:
        """Return the line at *offset*, empty at the end."""
        with self._lock:
            self._file.seek(offset)
            return self._file.readline()

    def close(self) -> None:
        """Close the file, which deletes it."""
        self._file.close()


class EmbeddingStage:
    """Stage documents with add, finish, write_results, then replay via iter.

    After finish, misses may be traversed to prepare ordered provider results.
    """

    def __init__(self, directory: Path | None = None) -> None:
        try:
            self._location = directory or Path(tempfile.gettempdir())
        except OSError as error:
            logger.error(
                "embedding_stage_io_failed path=%s err=%s",
                "system temporary directories",
                error,
            )
            raise
        if directory is not None:
            self._io(lambda: directory.mkdir(parents=True, exist_ok=True))
        self._docs = self._io(lambda: _Spool(self._location))
        try:
            self._results = self._io(lambda: _Spool(self._location))
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        self.miss_count = 0
        self._opaque: list[tuple[Any, Any, Any]] = []

    def _io(self, action: Callable[[], _T]) -> _T:
        try:
            return action()
        except OSError as error:
            logger.error(
                "embedding_stage_io_failed path=%s err=%s", self._location, error
            )
            raise

    def __enter__(self) -> EmbeddingStage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for name in ("_docs", "_results"):
            spool = getattr(self, name, None)  # unset if the constructor failed
            try:
                if spool is not None:
                    spool.close()
            except Exception as error:
                logger.warning(
                    "embedding_stage_cleanup_failed path=%s err=%s",
                    self._location,
                    error,
                )

    def add(self, doc: _Document, hits: dict[int, array[float]]) -> None:
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
        record = [
            identity,
            reference,
            doc.texts,
            {str(k): _pack(v) for k, v in hits.items()},
        ]
        self._io(lambda: self._docs.append(json.dumps(record)))
        self.miss_count += len(_misses(doc.texts, hits))

    def finish(self) -> None:
        """Flush the classification file before reading it."""
        self._io(self._docs.flush)

    def _lines(self, spool: _Spool) -> Generator[str]:
        """Yield *spool*'s lines from the start, holding an offset of its own."""
        offset = 0
        while data := self._io(partial(spool.read, offset)):
            yield data.decode("utf-8")
            offset += len(data)

    def _records(self) -> Generator[tuple[Any, Any, Any, Any, Any]]:
        for line in self._lines(self._docs):
            identity, reference, texts, hits = json.loads(line)
            path, token, metadata = (
                self._opaque[reference] if reference is not None else identity
            )
            yield path, token, texts, metadata, {int(k): v for k, v in hits.items()}

    def misses(self) -> Generator[tuple[str, int]]:
        """Yield misses in provider order with their chunk fanout."""
        with closing(self._records()) as records:
            for _path, _token, texts, _metadata, hits in records:
                counts = Counter(texts)
                for text in _misses(texts, hits):
                    yield text, counts[text]

    def write_results(self, results: Generator[array[float] | None]) -> None:
        """Write one result or failure marker for each miss."""
        with closing(results):
            for vector in results:
                line = json.dumps(None if vector is None else _pack(vector))
                self._io(partial(self._results.append, line))
            self._io(self._results.flush)

    def __iter__(self) -> Iterator[DocumentRows]:
        results = self._lines(self._results)
        with closing(self._records()) as records:
            for path, token, texts, metadata, hits in records:
                fresh: dict[str, Any] = {}
                for text in _misses(texts, hits):
                    value = json.loads(next(results))
                    if value is not None:
                        fresh[text] = _unpack(value)
                rows = []
                for index, (text, meta) in enumerate(zip(texts, metadata, strict=True)):
                    value = hits.get(index)
                    vector = _unpack(value) if value is not None else fresh.get(text)
                    if vector is not None:
                        rows.append(VectorRow(vector, dict(meta)))
                if rows:
                    yield DocumentRows(path, token, tuple(rows))
