"""A read-only store that donates compatible vectors to a full build (#1505).

A *donor* is an existing vector store, of any backend, opened read-only. It
may donate only when it holds vectors under an identity it recorded and that
equals the configured one; a store that is missing or empty, recorded none (a
bare-list sidecar), holds another model, or cannot be read is refused once,
with its reason, and the build proceeds without it. Vectors are matched per chunk and by path, keyed by the embedding
input :func:`~markdown_vault_mcp.indexing.embedding_reconciler.stored_inputs`
rebuilds, the same rule a store's own rows are reused by (#1795); a vector
that is zero, not finite or of the wrong length is skipped and counted. A
donor is never written, leased, renamed or deleted.

See ``docs/design/design.md`` § "Embedding transitions and recovery (#1505)".
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

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

with contextlib.suppress(ImportError):  # numpy is the embeddings extra
    import numpy as np

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.interfaces import EmbeddingStore, StoreBackend
    from markdown_vault_mcp.types import EmbeddingIdentity, VectorRow

logger = logging.getLogger(__name__)

DonorRefusal = Literal[
    "empty",
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


class DonorRefused(NamedTuple):
    """Why a store may not donate.

    Attributes:
        source: ``<backend>:<location>``; never a connection string.
        reason: The coarse tag the log carries.
        detail: One sentence for a person: the store's own message, or the
            recorded and wanted identities.
    """

    source: str
    reason: DonorRefusal
    detail: str


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
        store: EmbeddingStore,
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
        backend: StoreBackend[EmbeddingStore],
        location: Path | str,
        wanted: EmbeddingIdentity,
        *,
        builder: EmbedTextBuilder,
    ) -> Donor | DonorRefused:
        """Open *location* through *backend* read-only as a donor for *wanted*.

        Args:
            backend: A vector backend (:class:`~markdown_vault_mcp.interfaces.StoreBackend`).
            location: The store's location: a base path, or a PostgreSQL schema.
            wanted: The configured embedding identity.
            builder: The builder the current embedding inputs come from.

        Returns:
            The donor, or a :class:`DonorRefused` when the store may not
            donate (missing or empty, no recorded identity, another identity,
            or unreadable); the reason is logged once at WARNING as
            ``embeddings_donor_refused``. Never raises a
            :class:`~markdown_vault_mcp.exceptions.StoreOpenError`.
        """
        source = f"{getattr(backend, 'name', type(backend).__name__)}:{location}"
        try:
            store = backend.open(location, wanted, owns_files=False)
        except StoreOpenError as exc:
            return _refuse(source, _reason_for(exc), str(exc))
        recorded = getattr(store, "recorded_identity", store.identity)
        refusal = _content_refusal(store, recorded, wanted)
        if refusal is not None:
            store.close()
            return _refuse(source, *refusal)
        return cls(store, source=source, identity=recorded, builder=builder)

    def vectors_for(self, path: str) -> dict[str, Any]:
        """Return the donor's vectors for *path*, keyed by embedding input.

        Args:
            path: The document's vault-relative path.

        Returns:
            Each rebuilt embedding input to its float32 vector; empty for a
            path the donor does not hold. Rows whose vector fails the
            integrity check are left out and counted in :attr:`refused_rows`.

        Raises:
            StoreUnavailableError: If the store cannot be read any more (a
                PostgreSQL connection lost mid-build): eligibility was settled
                at :meth:`open`, so a failed read fails the build, which then
                publishes nothing, rather than paying to re-embed the rest.
        """
        good: list[VectorRow] = []
        for row in self._store.rows_for(path):
            if self._intact(np.asarray(row.vector, dtype=np.float32)):
                good.append(row)
            else:
                self.refused_rows += 1
        return {
            text: np.asarray(vector, dtype=np.float32)
            for text, vector in stored_inputs(self._builder, good).items()
        }

    def _intact(self, vector: Any) -> bool:
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


def _content_refusal(
    store: EmbeddingStore,
    recorded: EmbeddingIdentity | None,
    wanted: EmbeddingIdentity,
) -> tuple[DonorRefusal, str] | None:
    """Return why an opened *store* may not donate for *wanted*, or ``None``.

    A store holding no vectors, a missing location included, is refused
    before its identity is looked at: an absent store opens empty under the
    identity it was asked for, which it never recorded.
    """
    if store.count == 0:
        return "empty", "it holds no vectors"
    if recorded is None or recorded.dimension <= 0:
        return "identity_unknown", "it records no embedding identity"
    try:
        served_identity(recorded, wanted, backend="donor", where="donor")
    except StoreIncompatibleError:
        return (
            "identity_differs",
            f"it was built with {_described(recorded)}; the configured provider "
            f"is {_described(wanted)}",
        )
    return None


def _described(identity: EmbeddingIdentity) -> str:
    """Name an identity for a person."""
    return (
        f"{identity.provider}/{identity.model} (variant {identity.variant!r}, "
        f"text format {identity.text_format}, dimension {identity.dimension})"
    )


def _refuse(source: str, reason: DonorRefusal, detail: str) -> DonorRefused:
    """Log one refusal and return it: the build proceeds without this donor."""
    logger.warning("embeddings_donor_refused source=%s reason=%s", source, reason)
    return DonorRefused(source, reason, detail)
