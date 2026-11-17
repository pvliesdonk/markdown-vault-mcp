"""What the PostgreSQL vector store can store, checked before any write (#1504).

A row PostgreSQL would refuse inside the write transaction becomes a write
failure the reconciler retries forever; the same row refused here with
``ValueError`` changes nothing and is reported once per pass, the old rows
kept (#1778). So every value is checked as it will actually be sent: the
metadata is serialised with the same ``json.dumps`` the ``Jsonb`` adapter
uses, NaN and infinity refused (``jsonb`` rejects them), and every string
(path, folder, metadata keys and values) is walked for a NUL character
(PostgreSQL ``text`` and ``jsonb`` cannot hold one) and lone surrogates
(not valid Unicode). A path or folder longer than a btree index entry can
hold is refused too. Where this departs from the SQLite store is recorded
in ``docs/design/reference/pgvector-search.md``.
"""

from __future__ import annotations

import contextlib
import functools
import json
import tempfile
from typing import TYPE_CHECKING, Any, BinaryIO

import numpy as np
from psycopg.types.json import Jsonb

from markdown_vault_mcp.stores._resident import row_metadata, row_vectors

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from markdown_vault_mcp.types import DocumentRows, RevisionToken

# A btree index entry holds at most about 2,700 bytes (version 4); paths and
# folders are indexed, so a longer one would fail inside the transaction.
MAX_KEY_BYTES = 2000

_DUMPS = functools.partial(json.dumps, allow_nan=False)

ChunkValues = tuple[str, int, str, Any, Jsonb]


def chunk_values(document: DocumentRows, dimension: int) -> list[ChunkValues]:
    """Validate *document* and return its ``vector_chunks`` rows; nothing is written.

    Args:
        document: The document to publish.
        dimension: The store's embedding dimension; 0 means not known yet.

    Returns:
        One ``(path, ordinal, folder, vector, metadata)`` per row.

    Raises:
        ValueError: If the dimension is not known, a row is of another
            dimension or not finite, the metadata is not JSON (NaN and
            infinity included), or a path, folder or metadata string holds a
            NUL character or a lone surrogate, or a path or folder is too long.
    """
    require_dimension(dimension)
    check_key(document.path, what="path", path=document.path)
    vectors = row_vectors(document.rows, dimension)
    rows: list[ChunkValues] = []
    for ordinal, (meta, vector) in enumerate(
        zip(row_metadata(document), vectors, strict=True)
    ):
        folder = str(meta.get("folder") or "")
        check_key(folder, what="folder", path=document.path)
        _check_metadata(meta, path=document.path)
        rows.append((document.path, ordinal, folder, vector, Jsonb(meta, dumps=_DUMPS)))
    return rows


class GenerationReplay:
    """Validated document keys and a private replay of normalized chunk rows.

    Attributes:
        stream: The scratch file holding each row as a JSON line followed
            by its little-endian float32 vector.
        dimension: The generation's embedding dimension.
        documents: ``(path, token)`` per document, in input order.
        count: How many chunk rows the scratch file holds.
    """

    def __init__(self, stream: BinaryIO, dimension: int) -> None:
        """Start an empty replay over *stream* at *dimension*.

        Args:
            stream: An open, empty binary scratch file.
            dimension: The generation's embedding dimension.
        """
        self.stream = stream
        self.dimension = dimension
        self.documents: list[tuple[str, RevisionToken | None]] = []
        self.count = 0

    def chunks(self) -> Iterator[ChunkValues]:
        """Replay the validated rows in their original order.

        Each call starts again from the beginning of the scratch file, so a
        replay can be repeated; only the most recent iterator may be used.

        Yields:
            One ``(path, ordinal, folder, vector, metadata)`` per row, as
            :func:`chunk_values` returns them.
        """
        self.stream.seek(0)
        while line := self.stream.readline():
            path, ordinal, folder, metadata = json.loads(line)
            vector = np.frombuffer(self.stream.read(self.dimension * 4), dtype="<f4")
            yield (
                path,
                ordinal,
                folder,
                vector.astype(np.float32, copy=False),
                Jsonb(metadata, dumps=_DUMPS),
            )


@contextlib.contextmanager
def generation_replay(
    documents: Iterable[DocumentRows], dimension: int
) -> Iterator[GenerationReplay]:
    """Validate a one-shot generation before yielding its disposable replay.

    Every document is read, validated and written to a private temporary
    file before this yields, so nothing reaches the database until the whole
    generation has passed; the file is closed when the block exits.

    Args:
        documents: The generation's documents, iterated once.
        dimension: The generation identity's dimension.

    Yields:
        The replay of the validated generation.

    Raises:
        ValueError: If the dimension is unknown, a path appears twice, or
            any row cannot be stored (see :func:`chunk_values`).
        OSError: If the temporary file cannot be created or written.
    """
    require_dimension(dimension)
    with tempfile.TemporaryFile(mode="w+b") as stream:
        replay = GenerationReplay(stream, dimension)
        seen: set[str] = set()
        for document in documents:
            if document.path in seen:
                raise ValueError(
                    f"publish_generation got {document.path[:80]!r} twice."
                )
            seen.add(document.path)
            replay.documents.append((document.path, document.token))
            for path, ordinal, folder, vector, metadata in chunk_values(
                document, dimension
            ):
                stream.write(
                    (_DUMPS([path, ordinal, folder, metadata.obj]) + "\n").encode(
                        "utf-8"
                    )
                )
                stream.write(vector.astype("<f4", copy=False).tobytes())
                replay.count += 1
        stream.flush()
        yield replay


def require_dimension(dimension: int) -> None:
    """Refuse to write while the store does not know its embedding dimension.

    Raises:
        ValueError: If *dimension* is 0.
    """
    if dimension == 0:
        raise ValueError(
            "The vector store does not know its embedding dimension yet; "
            "nothing can be written to it."
        )


def check_key(value: str, *, what: str, path: str) -> None:
    """Refuse a path or folder PostgreSQL cannot store or index.

    Raises:
        ValueError: If it holds a NUL character or a lone surrogate, or is
            longer than ``MAX_KEY_BYTES`` in UTF-8.
    """
    problem = _text_problem(value)
    if problem is not None:
        raise ValueError(f"The {what} of {path[:80]!r}: {problem}.")
    size = len(value.encode("utf-8"))
    if size > MAX_KEY_BYTES:
        raise ValueError(
            f"The {what} of {path[:80]!r} is too long for a PostgreSQL index: "
            f"{size} bytes, at most {MAX_KEY_BYTES}."
        )


def _check_metadata(meta: dict[str, Any], *, path: str) -> None:
    """Refuse metadata that ``jsonb`` cannot store."""
    try:
        _DUMPS(meta)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Row metadata of {path[:80]!r} cannot be stored: {exc}"
        ) from exc
    for text in _strings(meta):
        problem = _text_problem(text)
        if problem is not None:
            raise ValueError(f"Row metadata of {path[:80]!r}: {problem}.")


def _text_problem(text: str) -> str | None:
    """Say why PostgreSQL cannot store *text*, or ``None``."""
    if "\x00" in text:
        return "PostgreSQL cannot store a NUL character"
    if any("\ud800" <= char <= "\udfff" for char in text):
        return "a lone surrogate is not valid Unicode"
    return None


def _strings(value: object) -> Iterator[str]:
    """Every string in *value*: dict keys and values, list and tuple items."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
