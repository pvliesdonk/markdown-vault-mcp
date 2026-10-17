"""The vault's one vector store: opened lazily, rebuilt, closed (#1777).

The slot opens the store through its :class:`VectorBackend` on first use,
because the identity it opens against reads ``provider.dimension``, which
some providers answer with a network probe; a provider that is down must
not stop a vault from being built. On a store the vault owns, a corrupt
store or one holding another identity (a model change) is rebuilt into a
fresh generation through the caller's ``rebuild``; without a rebuild (a
process that does not own the files) the store's own
:class:`~markdown_vault_mcp.exceptions.StoreCorruptError` or
:class:`~markdown_vault_mcp.exceptions.StoreIncompatibleError` propagates,
and such a process never creates or replaces the files (#1778). A store
that is unavailable or of an unsupported schema always propagates. An
injected store is the caller's: never rebuilt on a fault, never closed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
)
from markdown_vault_mcp.interfaces import ReportsResources
from markdown_vault_mcp.types import EmbeddingIdentity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp.interfaces import (
        EmbeddingStore,
        VectorBackend,
    )
    from markdown_vault_mcp.providers import EmbeddingProvider

logger = logging.getLogger(__name__)

#: The cost fields of ``embeddings_status`` when no store reports them (#1377).
NO_RESOURCES: dict[str, Any] = {
    "search_mode": None,
    "search_workspace_bytes": None,
    "stored_bytes": None,
    "resident_vector_bytes": None,
}


class VectorStoreSlot:
    """Holds the vault's vector store and decides what happens when it will not open.

    Args:
        backend: Opens the store.
        location: Where the store lives; ``None`` keeps it in memory.
        provider: Supplies the identity; ``None`` means no semantic search.
        text_format: The embedding-text format token.
        owns_files: ``False`` opens read-only.
        store: An already open store, owned by the caller.
    """

    def __init__(
        self,
        *,
        backend: VectorBackend,
        location: Path | None,
        provider: EmbeddingProvider | None,
        text_format: str,
        owns_files: bool = True,
        store: EmbeddingStore | None = None,
    ) -> None:
        self._backend = backend
        self._location = location
        self._provider = provider
        self._text_format = text_format
        self._owns_files = owns_files
        self._store = store
        self._injected = store is not None
        self._identity: EmbeddingIdentity | None = None

    @property
    def location(self) -> Path | None:
        """Where the store lives; ``None`` in memory."""
        return self._location

    @property
    def injected(self) -> bool:
        """Whether the caller passed the store in."""
        return self._injected

    @property
    def owns_files(self) -> bool:
        """Whether this process may rebuild the store's files."""
        return self._owns_files

    @property
    def backend_name(self) -> str:
        """The backend's registry name."""
        return str(self._backend.name)

    @property
    def identity(self) -> EmbeddingIdentity:
        """The identity the store opens against, computed on first use.

        Raises:
            RuntimeError: If the slot has no provider.
        """
        if self._identity is None:
            if self._provider is None:
                raise RuntimeError("A vector store needs an embedding provider.")
            self._identity = EmbeddingIdentity.of(self._provider, self._text_format)
        return self._identity

    def _open_identity(self) -> EmbeddingIdentity:
        """The identity to open with, without asking the provider for its dimension.

        Provider, model, variant and format come from configuration; the
        dimension may need a network probe, so a stored store supplies it
        (``0`` = take the stored one). In memory nothing is stored, and only
        the writer opens it, which needs the provider anyway.
        """
        if self._identity is not None or self._location is None:
            return self.identity
        if self._provider is None:
            raise RuntimeError("A vector store needs an embedding provider.")
        return EmbeddingIdentity(
            provider=self._provider.provider_name,
            model=self._provider.model_name,
            variant=self._provider.provider_variant,
            text_format=self._text_format,
            dimension=0,
        )

    def _run_rebuild(self, rebuild: Callable[[], object]) -> None:
        """Run *rebuild*, logging a failure before it propagates."""
        try:
            rebuild()
        except Exception:
            logger.exception("vector_store_rebuild_failed location=%s", self._location)
            raise

    def current(self) -> EmbeddingStore | None:
        """Return the installed store, opening nothing."""
        return self._store

    def get(self, *, rebuild: Callable[[], object] | None) -> EmbeddingStore:
        """Return the store, opening it on first use.

        A store that opens with nothing stored and no known dimension is
        returned but not installed, so the writer's :meth:`writable` still
        sets the provider's dimension.

        Args:
            rebuild: Rebuilds an owned store that is corrupt or holds another
                identity, installing the result (through :meth:`fresh`);
                ``None`` forbids it.

        Raises:
            StoreCorruptError: If the store is corrupt and no rebuild is
                allowed.
            StoreIncompatibleError: If the store holds another identity and
                no rebuild is allowed.
            ValueError: If a rebuild installs no store.
            StoreOpenError: If the store is unavailable or of an unsupported
                schema.
        """
        if self._store is not None:
            return self._store
        try:
            store = self._backend.open(
                self._location, self._open_identity(), owns_files=self._owns_files
            )
        except (StoreCorruptError, StoreIncompatibleError) as exc:
            if rebuild is None:
                raise
            logger.warning(
                "vector_store_rebuild_started location=%s reason=%s",
                self._location,
                type(exc).__name__,
            )
            self._run_rebuild(rebuild)
            if self._store is None:
                raise ValueError(
                    "Failed to rebuild the vector store after it would not open."
                ) from exc
            return self._store
        if store.identity.dimension == 0:
            # Nothing is stored and the dimension is not known: let the read
            # search the empty store, but install nothing, so the writer
            # opens through writable(), which learns the provider's dimension.
            return store
        self._store = store
        return store

    def replace(self, store: EmbeddingStore | None) -> None:
        """Install *store* (or clear the slot)."""
        self._store = store

    def fresh(self) -> EmbeddingStore:
        """Install and return an empty store at the identity, to rebuild into.

        An owned slot opens a fresh store over the location and drops the
        old object without closing it, so its unsaved rows are not written
        over the rebuild. An injected store is emptied in place.

        Raises:
            ReadOnlyError: If this process does not own the store's files.
        """
        self._refuse_unowned()
        if self._injected and self._store is not None:
            self._store.publish_generation(self.identity, [])
            return self._store
        self._store = self._backend.open_fresh(self._location, self.identity)
        return self._store

    def _answers(self, store: EmbeddingStore) -> bool:
        """Whether *store*'s rows answer this provider's queries: the same identity."""
        return store.identity == self.identity

    def _same_space(self, store: EmbeddingStore) -> bool:
        """Whether *store* was embedded by this provider's model and format.

        The dimension is left out: it may need a network probe, and a vector
        of another dimension is refused by the store anyway.
        """
        provider = self._provider
        if provider is None:
            return False
        held = store.identity
        return (held.provider, held.model, held.variant, held.text_format) == (
            provider.provider_name,
            provider.model_name,
            provider.provider_variant,
            self._text_format,
        )

    def writable_current(self) -> EmbeddingStore | None:
        """Return the installed store if this provider may write to it, else ``None``.

        Opens nothing and asks the provider for nothing: the reindex path
        writes only into a store already open, and never into one embedded
        by another model.
        """
        store = self._store
        if store is None or not self._same_space(store):
            return None
        return store

    def _refuse_unowned(self) -> None:
        """Refuse to create or replace files this process does not own.

        Raises:
            ReadOnlyError: If the store is on disk, not injected, and not owned.
        """
        if self._location is not None and not self._owns_files and not self._injected:
            raise ReadOnlyError(
                "This process does not own the vector store's files; rebuild "
                "the embeddings in the process that owns them."
            )

    def _mismatch(self, store: EmbeddingStore) -> str:
        """Say which embeddings *store* holds and which the provider makes."""
        held, wanted = store.identity, self.identity
        return (
            f"The vector store holds {held.model} vectors of dimension "
            f"{held.dimension}; the provider embeds {wanted.model} at "
            f"dimension {wanted.dimension}."
        )

    def writable(self, *, rebuild: Callable[[], object] | None) -> EmbeddingStore:
        """Open (installing) and return the store, for writing.

        Writing needs the provider, so here its identity, dimension included,
        is checked against the store's: an empty store takes the provider's
        identity (an owned one is replaced by a fresh one, an injected one is
        emptied in place, losing nothing); a populated one of another
        identity is rebuilt, or refused without ``rebuild`` or when injected.

        Raises:
            StoreIncompatibleError: If the store holds another identity and
                may not be rebuilt.
        """
        store = self.get(rebuild=rebuild)
        if not self._answers(store):
            if store.count == 0:
                self.fresh()
            elif rebuild is None or self._injected:
                raise StoreIncompatibleError(
                    self._mismatch(store),
                    backend=self.backend_name,
                    location=str(self._location) if self._location else ":memory:",
                )
            else:
                logger.warning(
                    "vector_store_rebuild_started location=%s reason=%s",
                    self._location,
                    "identity",
                )
                self._run_rebuild(rebuild)
        installed = self._store
        if installed is None:  # pragma: no cover - get() or fresh() installed one
            raise RuntimeError("The vector store did not install.")
        return installed

    def rebuild_target(self) -> EmbeddingStore:
        """Return the store a full rebuild publishes into.

        The store (opened first if need be), when its rows answer this
        provider's queries: the rebuild publishes one generation into it at
        the end, so readers keep its rows meanwhile and a rebuild that embeds
        nothing leaves them. Otherwise :meth:`fresh`: a fresh store over the
        location for an owned slot (the old files survive until the rebuild
        publishes), the injected store emptied in place.

        Raises:
            ReadOnlyError: If this process does not own the store's files.
            StoreOpenError: If the store is unavailable or of an unsupported
                schema.
        """
        self._refuse_unowned()
        if self._store is None:
            # A store that will not open is rebuilt into a fresh one below.
            with contextlib.suppress(StoreCorruptError, StoreIncompatibleError):
                self.get(rebuild=None)
        if self._store is not None and self._answers(self._store):
            return self._store
        return self.fresh()

    def describe(self) -> dict[str, Any]:
        """Return backend, identity, generation and costs for ``embeddings_status``.

        The search mode and sizes come from the open store when it reports
        them (:class:`~markdown_vault_mcp.interfaces.ReportsResources`, #1377);
        ``None`` before it opens or when it does not.

        Raises:
            StoreOpenError: If a streamed store cannot be read: its
                ``generation`` is read from the database.
            OSError: If a stat of the store's files is refused (#1625).
        """
        store = self._store
        described: dict[str, Any] = {
            "backend": self.backend_name,
            "identity": dataclasses.asdict(store.identity)
            if store is not None
            else None,
            "generation": store.generation if store is not None else None,
            **NO_RESOURCES,
        }
        if isinstance(store, ReportsResources):
            described.update(dataclasses.asdict(store.resources()))
        return described

    def close(self) -> None:
        """Close the store if the slot opened it; an injected store is left open."""
        if self._store is not None and not self._injected:
            self._store.close()
