"""A read-only store that donates compatible vectors to a full build (#1505).

A *donor* is an existing vector store, of any backend, opened read-only. It
may donate only under an identity it recorded and that equals the configured
one; a store that recorded none (a bare-list sidecar), holds another model,
or cannot be read is refused once, with its reason, and the build proceeds
without it. Vectors are matched per chunk and by path, keyed by the embedding
input :func:`~markdown_vault_mcp.indexing.embedding_reconciler.stored_inputs`
rebuilds, the same rule a store's own rows are reused by (#1795); a vector
that is zero, not finite or of the wrong length is skipped and counted. A
donor is never written, leased, renamed or deleted.

See ``docs/design/design.md`` § "Embedding transitions and recovery (#1505)".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from markdown_vault_mcp.exceptions import (
    StoreCorruptError,
    StoreIncompatibleError,
    StoreNotConfiguredError,
    StoreOpenError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs
from markdown_vault_mcp.stores._identity import served_identity
from markdown_vault_mcp.types import VectorRow

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.types import EmbeddingIdentity

logger = logging.getLogger(__name__)

DonorRefusal = Literal[
    "identity_unknown",
    "identity_differs",
    "corrupt",
    "unavailable",
    "unsupported",
    "not_configured",
]

#: The refusal reason for each typed open outcome, most specific first.
_OPEN_REASONS: tuple[tuple[type[StoreOpenError], DonorRefusal], ...] = (
    (StoreNotConfiguredError, "not_configured"),
    (StoreUnavailableError, "unavailable"),
    (StoreIncompatibleError, "identity_differs"),
    (StoreUnsupportedSchemaError, "unsupported"),
    (StoreCorruptError, "corrupt"),
)


class Donor:
    """A read-only vector store consulted per chunk by a full build.

    Construct with :meth:`open`, which returns ``None`` for a store that may
    not donate. Usable as a context manager; :meth:`close` is idempotent.

    Attributes:
        source: ``<backend>:<location>``, for logs; never a connection string.
        identity: The identity the store recorded, equal to the wanted one.
        refused_rows: Rows skipped so far because their vector was zero, not
            finite or of the wrong length.
    """

    def __init__(
        self,
        store: Any,
        *,
        source: str,
        identity: EmbeddingIdentity,
        builder: EmbedTextBuilder,
    ) -> None:
        self._store = store
        self._builder = builder
        self._closed = False
        self.source = source
        self.identity = identity
        self.refused_rows = 0

    @classmethod
    def open(
        cls,
        backend: Any,
        location: Path | str,
        wanted: EmbeddingIdentity,
        *,
        builder: EmbedTextBuilder,
    ) -> Donor | None:
        """Open *location* through *backend* read-only as a donor for *wanted*.

        Args:
            backend: A vector backend (:class:`~markdown_vault_mcp.interfaces.StoreBackend`).
            location: The store's location: a base path, or a PostgreSQL schema.
            wanted: The configured embedding identity.
            builder: The builder the current embedding inputs come from.

        Returns:
            The donor, or ``None`` when the store may not donate; the reason is
            logged once at WARNING as ``embeddings_donor_refused``. Never
            raises a :class:`~markdown_vault_mcp.exceptions.StoreOpenError`.
        """
        source = f"{getattr(backend, 'name', type(backend).__name__)}:{location}"
        try:
            store = backend.open(location, wanted, owns_files=False)
        except StoreOpenError as exc:
            _refuse(source, _reason_for(exc))
            return None
        recorded = getattr(store, "recorded_identity", store.identity)
        reason = _identity_refusal(recorded, wanted, source)
        if reason is not None:
            store.close()
            _refuse(source, reason)
            return None
        return cls(store, source=source, identity=recorded, builder=builder)

    def vectors_for(self, path: str) -> dict[str, np.ndarray]:
        """Return the donor's vectors for *path*, keyed by embedding input.

        Args:
            path: The document's vault-relative path.

        Returns:
            Each rebuilt embedding input to its float32 vector; empty for a
            path the donor does not hold. Rows whose vector fails the
            integrity check are left out and counted in :attr:`refused_rows`.
        """
        good: list[VectorRow] = []
        for row in self._store.rows_for(path):
            vector = np.asarray(row.vector, dtype=np.float32)
            if self._intact(vector):
                good.append(VectorRow(vector.tolist(), row.metadata))
            else:
                self.refused_rows += 1
        return {
            text: np.asarray(vector, dtype=np.float32)
            for text, vector in stored_inputs(self._builder, good).items()
        }

    def _intact(self, vector: np.ndarray) -> bool:
        """Whether *vector* has the identity's length, finite values and a norm."""
        return bool(
            vector.ndim == 1
            and vector.shape[0] == self.identity.dimension
            and np.all(np.isfinite(vector))
            and float(np.linalg.norm(vector)) > 0.0
        )

    def close(self) -> None:
        """Close the underlying store; a second call does nothing."""
        if not self._closed:
            self._closed = True
            self._store.close()

    def __enter__(self) -> Donor:
        """Return the donor itself."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the donor."""
        self.close()


def _reason_for(exc: StoreOpenError) -> DonorRefusal:
    """Return the refusal reason for a typed open outcome."""
    for kind, reason in _OPEN_REASONS:
        if isinstance(exc, kind):
            return reason
    return "unavailable"


def _identity_refusal(
    recorded: EmbeddingIdentity | None, wanted: EmbeddingIdentity, source: str
) -> DonorRefusal | None:
    """Return why *recorded* may not donate for *wanted*, or ``None`` if it may."""
    if recorded is None or recorded.dimension <= 0:
        return "identity_unknown"
    try:
        served_identity(recorded, wanted, backend="donor", where=source)
    except StoreIncompatibleError:
        return "identity_differs"
    return None


def _refuse(source: str, reason: DonorRefusal) -> None:
    """Log one refusal: the build proceeds without this donor."""
    logger.warning("embeddings_donor_refused source=%s reason=%s", source, reason)
