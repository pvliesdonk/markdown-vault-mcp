"""The vault's one vector store: opened lazily, rebuilt, closed (#1777).

The slot opens the store through its :class:`VectorBackend` on first use,
because the identity it opens against reads ``provider.dimension``, which
some providers answer with a network probe; a provider that is down must
not stop a vault from being built. On a store the vault owns, a corrupt
store or one holding another identity (a model change) is rebuilt into a
fresh generation through the caller's ``rebuild``. A corrupt built-in store
is first moved aside, every file of it renamed to ``.corrupt-<UTC time>``
through its backend's ``quarantine``, so nothing is rebuilt over it; a
rename that is refused leaves it unavailable (#1505). Without a rebuild (a
process that does not own the files) the store's own
:class:`~markdown_vault_mcp.exceptions.StoreCorruptError` or
:class:`~markdown_vault_mcp.exceptions.StoreIncompatibleError` propagates,
and such a process never creates or replaces the files (#1778). A store
that is unavailable or of an unsupported schema always propagates. An
injected store is the caller's: never rebuilt on a fault, never closed.

The slot closes every store it opens and stops using: a database-backed
store holds a pool, and dropping one unclosed would leak it (#1504). The
store a read opens before anything is stored, with no known dimension,
is kept as provisional and reused until a sized store replaces it.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreOpenError,
)
from markdown_vault_mcp.interfaces import DescribesStore, ReportsResources
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

#: Why a rebuild kept the store at the stored dimension (#1904).
_RESTART_HINT = (
    " The provider still reports the stored dimension, so the rebuild kept "
    "the store; restart the server to rebuild it."
)

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
        location: Where the store lives: a directory base, or a schema
            name; ``None`` keeps it in memory.
        provider: Supplies the identity; ``None`` means no semantic search.
        text_format: The embedding-text format token.
        owns_files: ``False`` opens read-only.
        store: An already open store, owned by the caller.
    """

    def __init__(
        self,
        *,
        backend: VectorBackend,
        location: Path | str | None,
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
        # The store a rebuild kept at another dimension than a query's (#1904).
        self._kept: tuple[EmbeddingStore, int] | None = None
        # The store a read opened before the dimension was known (#1504).
        self._provisional: EmbeddingStore | None = None
        # Guards opening, swapping and closing; never held across a rebuild.
        self._lock = threading.Lock()
        self._closed = False
        # Serializes moving a corrupt store aside across threads; the
        # writer lock in the backend only excludes other processes (#1505).
        self._recovery = threading.Lock()

    @property
    def location(self) -> Path | str | None:
        """Where the store lives: a directory base or a schema; ``None`` in memory."""
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

    def _run_rebuild(self, rebuild: Callable[[], object], reason: str) -> None:
        """Log why the store is rebuilt, then run *rebuild*, logging a failure."""
        logger.warning(
            "vector_store_rebuild_started location=%s reason=%s",
            self._location,
            reason,
        )
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
        sets the provider's dimension; it is kept as provisional and
        returned again until a sized store replaces it.

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
        opened = self._open()
        if isinstance(opened, StoreOpenError):
            return self._rebuilt(opened, rebuild)
        return opened

    def _open(self) -> EmbeddingStore | StoreOpenError:
        """Return the installed, provisional or newly opened store, or why it would not open.

        A corrupt store or one of another identity is returned as its
        error, not raised, for the caller to rebuild or raise.

        Raises:
            RuntimeError: If the slot is closed.
            StoreOpenError: If the store is unavailable or of an unsupported
                schema.
        """
        with self._lock:
            self._refuse_closed()
            if self._store is not None:
                return self._store
            if self._provisional is not None:
                return self._provisional
            try:
                store = self._backend.open(
                    self._location, self._open_identity(), owns_files=self._owns_files
                )
            except (StoreCorruptError, StoreIncompatibleError) as exc:
                return exc
            if store.identity.dimension == 0:
                # Nothing is stored and the dimension is not known: let
                # reads search the empty store, but install nothing, so
                # the writer opens through writable(), which learns the
                # provider's dimension.
                self._provisional = store
            else:
                self._store = store
            return store

    def _rebuilt(
        self, exc: StoreOpenError, rebuild: Callable[[], object] | None
    ) -> EmbeddingStore:
        """Rebuild a store that would not open, outside the lock; re-raise without one.

        A corrupt store is moved aside first; when another thread already
        did, the store it left is returned without a rebuild.
        """
        if rebuild is None:
            raise exc
        if isinstance(exc, StoreCorruptError):
            recovered = self._recover_corrupt()
            if recovered is not None:
                return recovered
        self._run_rebuild(rebuild, type(exc).__name__)
        if self._store is None:
            raise ValueError(
                "Failed to rebuild the vector store after it would not open."
            ) from exc
        return self._store

    def _recover_corrupt(self) -> EmbeddingStore | None:
        """Move a corrupt store aside, unless another thread already did.

        Under the recovery lock the store is opened again: a failure seen
        before another thread moved the files aside (and maybe rebuilt them)
        must not rename what that thread left. Never held across a rebuild,
        which may wait on the writer thread that meets the same store.

        Returns:
            The store that opens now, with nothing renamed; ``None`` once the
            corrupt files are moved aside, or when the store now holds
            another identity, for the caller to rebuild.

        Raises:
            StoreUnavailableError: If the files cannot be renamed.
        """
        with self._recovery:
            opened = self._open()
            if not isinstance(opened, StoreOpenError):
                return opened
            if isinstance(opened, StoreCorruptError):
                self._move_aside()
            return None

    def _move_aside(self) -> None:
        """Rename an owned corrupt store's files aside, if its backend can (#1505).

        Nothing for an injected store, one this process does not own, one in
        memory, or a backend without ``quarantine`` (PostgreSQL).

        Raises:
            StoreUnavailableError: If the files cannot be renamed.
        """
        quarantine = getattr(self._backend, "quarantine", None)
        if (
            quarantine is None
            or self._injected
            or not self._owns_files
            or self._location is None
        ):
            return
        quarantine(self._location)

    def _refuse_closed(self) -> None:
        """Refuse to open behind a closed slot; called under the lock.

        Raises:
            RuntimeError: If the slot is closed.
        """
        if self._closed:
            raise RuntimeError("The vector store slot is closed.")

    def _discard(self, store: EmbeddingStore | None) -> None:
        """Close a store the slot stops using; a failure is logged, not raised."""
        if store is None:
            return
        try:
            store.close()
        except Exception:
            # Broad by design: each backend fails its close its own way, and
            # the store is abandoned either way.
            logger.warning(
                "vector_store_close_failed location=%s", self._location, exc_info=True
            )

    def replace(self, store: EmbeddingStore | None) -> None:
        """Install *store* (or clear the slot), closing a provisional store.

        The store it replaces stays the caller's to close.
        """
        with self._lock:
            provisional, self._provisional = self._provisional, None
            self._store = store
            self._kept = None
            self._discard(provisional)

    def fresh(self) -> EmbeddingStore:
        """Install and return an empty store at the identity, to rebuild into.

        An owned slot closes the store it replaces (and a provisional one)
        before it opens a fresh store over the location: the close lands any
        pending rows first, the fresh store's first persist replaces them,
        and a rebuild that persists nothing leaves them. An injected store
        is emptied in place.

        Raises:
            ReadOnlyError: If this process does not own the store's files.
        """
        self._refuse_unowned()
        if self._injected and self._store is not None:
            self._store.publish_generation(self.identity, [])
            return self._store
        with self._lock:
            self._refuse_closed()
            # A built-in store's writer lock is held across the swap, so no
            # other process can take it between the close and the open (#1505).
            hold = getattr(self._backend, "hold_writer", None)
            share = (
                hold(self._location)
                if hold is not None and self._location is not None
                else None
            )
            try:
                old, self._store = self._store, None
                provisional, self._provisional = self._provisional, None
                self._kept = None
                # Closed before the fresh store opens, never after: a numpy
                # store flushes pending rows on close, which must land before
                # the rebuild's own first persist, never over it.
                self._discard(old)
                self._discard(provisional)
                self._store = self._backend.open_fresh(self._location, self.identity)
                return self._store
            finally:
                if share is not None:
                    share.release()

    def answers(self, store: EmbeddingStore) -> bool:
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

    def for_query(
        self,
        store: EmbeddingStore,
        dimension: int,
        *,
        rebuild: Callable[[], object] | None,
    ) -> EmbeddingStore:
        """Return a store that answers a query vector of *dimension* (#1904).

        A read opens the store at its stored dimension without asking the
        provider (#1504), so a model that changed only its dimension shows
        first in the length of the query vector. An owned store is rebuilt
        then, as for a model change, unless another search's rebuild already
        installed one at *dimension*; without a rebuild, or on an injected
        store, the mismatch is refused. A rebuild that keeps the old
        dimension is refused, and so is every later query at *dimension*
        while that store stays installed, without another rebuild.

        Args:
            store: The populated store the search read.
            dimension: The length of the query vector.
            rebuild: Rebuilds the store, installing the result; ``None``
                forbids it.

        Returns:
            *store*, or the store installed at *dimension*.

        Raises:
            StoreIncompatibleError: If the dimensions differ and the store
                may not be rebuilt, or a rebuild left it at another dimension.
            Exception: Whatever *rebuild* raises, unchanged.
        """
        installed = self._store
        if installed is not store and installed is not None:
            # Another search's rebuild may have closed *store* already. An
            # installed store at another dimension falls through to the
            # rebuild, which converges it.
            if installed.identity.dimension == dimension:
                return installed
        elif store.identity.dimension == dimension:
            return store
        if rebuild is None or self._injected:
            raise self._query_mismatch(store, dimension, "")
        if self._kept == (installed, dimension):
            raise self._query_mismatch(store, dimension, _RESTART_HINT)
        self._run_rebuild(rebuild, "dimension")
        return self._rebuilt_at(dimension)

    def _rebuilt_at(self, dimension: int) -> EmbeddingStore:
        """Return the store a rebuild installed, refusing one at another dimension.

        The rebuild compares with ``provider.dimension``, which providers
        cache from their first call: a model that changed its dimension
        while the process ran still reports the old one, so the rebuild
        keeps the store. That store is remembered, so later queries are
        refused without rebuilding again.

        Raises:
            StoreIncompatibleError: If the store is not at *dimension*.
        """
        installed = self._store
        if installed is None:  # pragma: no cover - unless the slot closed meanwhile
            raise RuntimeError("The vector store did not install.")
        if installed.identity.dimension != dimension:
            self._kept = (installed, dimension)
            raise self._query_mismatch(installed, dimension, _RESTART_HINT)
        return installed

    def _query_mismatch(
        self, store: EmbeddingStore, dimension: int, hint: str
    ) -> StoreIncompatibleError:
        """Say which vectors *store* holds and which dimension the query has."""
        held = store.identity
        return StoreIncompatibleError(
            f"The vector store holds {held.model} vectors of dimension "
            f"{held.dimension}; the provider embeds {held.model} queries at "
            f"dimension {dimension}.{hint}",
            backend=self.backend_name,
            location=str(self._location) if self._location else ":memory:",
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
        if not self.answers(store):
            if store.count == 0:
                self.fresh()
            elif rebuild is None or self._injected:
                raise StoreIncompatibleError(
                    self._mismatch(store),
                    backend=self.backend_name,
                    location=str(self._location) if self._location else ":memory:",
                )
            else:
                self._run_rebuild(rebuild, "identity")
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
        publishes), the injected store emptied in place. A corrupt store is
        moved aside first (#1505).

        Raises:
            ReadOnlyError: If this process does not own the store's files.
            StoreOpenError: If the store is unavailable or of an unsupported
                schema, or corrupt and cannot be moved aside.
        """
        self._refuse_unowned()
        if self._store is None:
            # A store that will not open is rebuilt into a fresh one below.
            try:
                self.get(rebuild=None)
            except StoreCorruptError:
                self._recover_corrupt()
            except StoreIncompatibleError:
                pass
        if self._store is not None and self.answers(self._store):
            return self._store
        return self.fresh()

    def describe(self) -> dict[str, Any]:
        """Return backend, identity, generation and costs for ``embeddings_status``.

        The search mode and sizes come from the open store when it reports
        them (:class:`~markdown_vault_mcp.interfaces.ReportsResources`, #1377);
        ``None`` before it opens or when it does not. Before the first build
        they come from the provisional store a read opened, which has no
        identity or generation to report yet. ``store_details`` comes
        from a store that says where it lives
        (:class:`~markdown_vault_mcp.interfaces.DescribesStore`, #1504);
        ``None`` otherwise.

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
        # Before the first build only the provisional store is open: it has
        # no identity to report, but it says what it costs and where it is.
        reporting = store if store is not None else self._provisional
        if isinstance(reporting, ReportsResources):
            described.update(dataclasses.asdict(reporting.resources()))
        # The store's own reads (generation, resources) come first: a store
        # that cannot be read fails the status there, before its details.
        described["store_details"] = (
            reporting.describe() if isinstance(reporting, DescribesStore) else None
        )
        return described

    def close(self) -> None:
        """Close the store if the slot opened it, and a provisional one; an injected store is left open."""
        with self._lock:
            self._closed = True
            store, self._store = self._store, None
            provisional, self._provisional = self._provisional, None
        self._discard(provisional)
        if store is not None and not self._injected:
            # Raised, not discarded: a failed final persist loses rows.
            store.close()
