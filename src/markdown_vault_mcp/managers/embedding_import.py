"""Import vectors into the configured store from another store (#1505).

The explicit switch between stores (choice C: only the released sidecar
upgrades automatically). The source is opened read-only as a donor and never
modified; the destination is the configured store, filled by one full build
that copies every chunk the destination's own rows or the source hold and
embeds the rest, then publishes one generation. A dry run counts instead,
writing nothing. Each refusal is one sentence naming the remedy, logged once
as ``embeddings_import_refused``.

Refusals that change nothing come first: no provider, a destination kept in
memory, the source being the destination; then a built-in destination's
writer lock (a running server) and the source (identity, emptiness, damage).
The keyword index is then brought up to date with vectors deferred, so no
vector store is opened, embedded into or purged early; a vault that then
holds no notes is refused, so a missing folder never empties a store. Last
the destination opens for writing, where a PostgreSQL destination's lease
refuses.

See ``docs/design/design.md`` § "The explicit import (#1505 child 6)".
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import (
    ConfigurationError,
    EmbeddingsNotConfiguredError,
    MarkdownMCPError,
    StoreBusyError,
    StoreOpenError,
)
from markdown_vault_mcp.indexing.donor import Donor, DonorRefused

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.config_sections import VaultSettings
    from markdown_vault_mcp.indexing.donor import DonorRefusal
    from markdown_vault_mcp.interfaces import EmbeddingStore, StoreBackend
    from markdown_vault_mcp.managers.embeddings import EmbeddingsManager
    from markdown_vault_mcp.vault import Vault

logger = logging.getLogger(__name__)

SourceKind = Literal["numpy", "sqlite", "postgres"]

ImportRefusal = Literal[
    "not_configured",
    "in_memory",
    "same_store",
    "busy",
    "vault_empty",
    "source_empty",
    "identity_unknown",
    "identity_differs",
    "source_corrupt",
    "source_unavailable",
    "source_unsupported",
]


@dataclass(frozen=True)
class ImportSource:
    """The store to import from, as the command names it.

    Attributes:
        kind: The source's backend.
        path: A numpy or SQLite base path; ``None`` takes the configured one.
        schema: A PostgreSQL schema; ``None`` takes the configured one.
        dsn_env: The environment variable holding a PostgreSQL source's DSN;
            ``None`` takes the configured DSN.
    """

    kind: SourceKind
    path: Path | None = None
    schema: str | None = None
    dsn_env: str | None = None


@dataclass(frozen=True)
class ImportReport:
    """What an import did, or with *dry_run* would do.

    Attributes:
        source: ``<backend>:<location>``; never a DSN.
        destination: ``<backend>:<location>``; never a DSN.
        chunks: Every chunk the keyword index holds.
        reused: Chunks copied (or, in a dry run, copyable).
        embedded: Inputs the provider embedded (or would embed).
        refused_rows: Source rows skipped for a broken vector.
        dry_run: Whether nothing was written.
        published: Whether a generation was published.
        failed_batches: Provider batches that failed; into an empty store the
            build is published without them (#649).
    """

    source: str
    destination: str
    chunks: int
    reused: int
    embedded: int
    refused_rows: int
    dry_run: bool
    published: bool
    failed_batches: int = 0


class _SourceUnreadable(MarkdownMCPError):
    """The source failed a read during the build; the build published nothing."""

    def __init__(self, exc: StoreOpenError) -> None:
        super().__init__(str(exc))
        self.cause = exc


class _SourceDonor(Donor):
    """The source as a donor whose failed read is told apart from the destination's."""

    def vectors_for(self, path: str) -> dict[str, Any]:
        """Return the source's vectors for *path*; see :meth:`Donor.vectors_for`.

        Raises:
            _SourceUnreadable: If the source can no longer be read.
        """
        try:
            return super().vectors_for(path)
        except StoreOpenError as exc:
            raise _SourceUnreadable(exc) from exc


class ImportRefused(MarkdownMCPError):
    """The import will not run; ``str(exc)`` is the operator's line.

    Attributes:
        reason: The coarse tag the log carries.
    """

    def __init__(self, message: str, *, reason: ImportRefusal) -> None:
        super().__init__(message)
        self.reason: ImportRefusal = reason


@dataclass(frozen=True)
class _Side:
    """One side of an import: how to open it and how to recognise it."""

    backend: Any
    location: Path | str
    key: tuple[str, ...]
    label: str


_DONOR_REFUSALS: dict[DonorRefusal, ImportRefusal] = {
    "empty": "source_empty",
    "identity_unknown": "identity_unknown",
    "identity_differs": "identity_differs",
    "corrupt": "source_corrupt",
    "unavailable": "source_unavailable",
    "unsupported": "source_unsupported",
    "not_configured": "not_configured",
}


def import_embeddings(
    vault: Vault, settings: VaultSettings, source: ImportSource, *, dry_run: bool
) -> ImportReport:
    """Import vectors from *source* into the configured store, or count a dry run.

    Args:
        vault: The vault of the configured store; for a dry run, one with a
            scratch keyword index and every store opened read-only.
        settings: The settings *vault* was built from.
        source: The store to import from.
        dry_run: Count what would be copied and embedded; write nothing.

    Returns:
        What the import did, or would do.

    Raises:
        ImportRefused: When the import may not run, with the remedy.
        EmbeddingsNotPublishedError: If a provider batch failed while the
            destination held vectors: nothing was published.
        StoreOpenError: If the destination cannot be opened for another reason.
    """
    manager: EmbeddingsManager = vault._index_mgr._embeddings
    destination = _destination(manager, settings)
    side = _source(settings, source)
    if side.key == destination.key:
        raise _refuse(
            "same_store",
            f"{side.label} is the configured store. Name another source with "
            "--from, --from-path or --from-schema.",
        )
    logger.info(
        "embeddings_import_started source=%s destination=%s dry_run=%s",
        side.label,
        destination.label,
        dry_run,
    )
    if dry_run:
        return _preview(vault, manager, side, destination)
    try:
        with _destination_lock(destination), _donor(manager, side) as donor:
            with manager.vectors_deferred():
                vault.index.build_index()
                vault.index.reindex()
            _refuse_an_empty_vault(manager)
            try:
                outcome = manager.import_from(donor)
            except _SourceUnreadable as exc:
                raise _refuse(
                    "source_unavailable",
                    f"{side.label} cannot be read: {str(exc).rstrip('.')}. "
                    "Nothing was published.",
                ) from exc.cause
            refused_rows = donor.refused_rows
    except StoreBusyError:
        raise _refuse(
            "busy",
            f"{destination.label} is being written by another process. Stop the "
            "server first, then run the import again.",
        ) from None
    logger.info(
        "embeddings_import_completed source=%s destination=%s chunks=%d reused=%d "
        "embedded=%d refused_rows=%d failed_batches=%d published=%s",
        side.label,
        destination.label,
        outcome.attempted,
        outcome.reused,
        outcome.embedded,
        refused_rows,
        outcome.failed_batches,
        outcome.published,
    )
    return ImportReport(
        side.label,
        destination.label,
        outcome.attempted,
        outcome.reused,
        outcome.embedded,
        refused_rows,
        dry_run=False,
        published=outcome.published,
        failed_batches=outcome.failed_batches,
    )


def _refuse_an_empty_vault(manager: EmbeddingsManager) -> None:
    """Refuse a vault with no notes: its build would empty the destination."""
    if manager._fts.count_chunks() == 0:
        raise _refuse(
            "vault_empty",
            "the vault holds no notes to import vectors for, so the configured "
            "store was left as it is. Check MARKDOWN_VAULT_MCP_SOURCE_DIR.",
        )


def _preview(
    vault: Vault, manager: EmbeddingsManager, side: _Side, destination: _Side
) -> ImportReport:
    """Count a dry run against *vault*'s scratch keyword index."""
    with _donor(manager, side) as donor:
        vault.index.build_index()
        _refuse_an_empty_vault(manager)
        preview = manager.preview_import(donor)
        refused_rows = donor.refused_rows
    logger.info(
        "embeddings_import_previewed source=%s chunks=%d reusable=%d to_embed=%d "
        "refused_rows=%d",
        side.label,
        preview.chunks,
        preview.reusable,
        preview.to_embed,
        refused_rows,
    )
    return ImportReport(
        side.label,
        destination.label,
        preview.chunks,
        preview.reusable,
        preview.to_embed,
        refused_rows,
        dry_run=True,
        published=False,
    )


def _destination(manager: EmbeddingsManager, settings: VaultSettings) -> _Side:
    """Describe the configured store, refusing one an import cannot fill."""
    try:
        manager._require_vectors()
    except EmbeddingsNotConfiguredError:
        raise _refuse(
            "not_configured",
            "no embedding provider is configured, so there is nothing to import "
            "into. Set MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER.",
        ) from None
    slot = manager._slot
    if slot is None or slot.location is None:
        raise _refuse(
            "in_memory",
            "the configured store keeps vectors in memory only. Set "
            "MARKDOWN_VAULT_MCP_INDEX_PATH or MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH.",
        )
    name = slot.backend_name
    if name == "postgres":
        dsn = settings.embedding_postgres_dsn or ""
        key: tuple[str, ...] = ("postgres", *_server(dsn), str(slot.location))
    else:
        key = (name, _lock_key(slot.location))
    return _Side(None, slot.location, key, f"{name}:{slot.location}")


def _source(settings: VaultSettings, source: ImportSource) -> _Side:
    """Resolve *source* to a backend and location, opening nothing."""
    if source.kind == "postgres":
        return _postgres_source(settings, source)
    base = source.path or settings.effective_embeddings_path()
    if base is None:
        raise _refuse(
            "not_configured",
            f"no {source.kind} source to import: name its base path with --from-path.",
        )
    base = Path(base)
    if source.kind == "sqlite":
        from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend

        return _Side(
            SqliteVectorBackend(),
            base,
            ("sqlite", _lock_key(base)),
            f"sqlite:{base}",
        )
    from markdown_vault_mcp.stores.numpy_sidecar import (
        NumpySidecarBackend,
        committed_json,
    )

    # The numpy store at this base once it committed, else the sidecar a
    # release or an edge build left there (#1505).
    if committed_json(base) is not None:
        return _Side(
            NumpySidecarBackend(),
            base,
            ("numpy", _lock_key(base)),
            f"numpy:{base}",
        )
    return _Side(
        NumpySidecarBackend(legacy_only=True),
        base,
        ("numpy-released", _lock_key(base)),
        f"numpy:{base}",
    )


def _postgres_source(settings: VaultSettings, source: ImportSource) -> _Side:
    """Resolve a PostgreSQL source; its DSN comes only from the environment."""
    from markdown_vault_mcp.vault import _postgres_backend

    if source.dsn_env is not None:
        dsn = os.environ.get(source.dsn_env, "")
        if not dsn.strip():
            raise _refuse(
                "not_configured",
                f"--from-dsn-env names {source.dsn_env}, which is not set. Export "
                "the source's connection string in it.",
            )
    else:
        dsn = settings.embedding_postgres_dsn or ""
    schema = (source.schema or settings.embedding_postgres_schema or "").strip()
    if not schema:
        raise _refuse(
            "not_configured",
            "no PostgreSQL schema to import from: name it with --from-schema.",
        )
    try:
        from markdown_vault_mcp.config_sections import VaultSettings

        fields = {
            f.name: getattr(settings, f.name) for f in dataclasses.fields(settings)
        }
        fields.update(
            embedding_store="postgres",
            embedding_search="streamed",
            embedding_postgres_dsn=dsn,
            embedding_postgres_schema=schema,
        )
        source_settings = VaultSettings(**fields)
        backend = _postgres_backend(source_settings)
    except ConfigurationError as exc:  # its message never quotes the DSN
        raise _refuse("not_configured", f"{exc}") from None
    return _Side(
        backend, schema, ("postgres", *_server(dsn), schema), f"postgres:{schema}"
    )


def _lock_key(base: Path | str) -> str:
    """Name a built-in store by its writer lock's path.

    The stores name their files with ``Path.with_suffix`` (#819), so ``emb``,
    ``emb.npy`` and ``emb.sqlite3`` are one base, as they are one lock.
    """
    return os.path.realpath(Path(base).with_suffix(".lock"))


def _server(dsn: str) -> tuple[str, str, str]:
    """Return the host, port and database *dsn* names, for comparing two DSNs."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(dsn)
    except Exception:
        return (dsn, "", "")
    return (
        str(parts.get("host") or ""),
        str(parts.get("port") or "5432"),
        str(parts.get("dbname") or ""),
    )


@contextlib.contextmanager
def _destination_lock(destination: _Side) -> Iterator[None]:
    """Hold a built-in destination's writer lock for the whole import.

    PostgreSQL's lease is taken when the destination opens for writing.

    Raises:
        StoreBusyError: If another process writes the destination.
    """
    if destination.key[0] == "postgres":
        yield
        return
    from markdown_vault_mcp.stores._writer_lock import hold_writer_lock

    share = hold_writer_lock(Path(destination.location), backend=destination.key[0])
    try:
        yield
    finally:
        share.release()


@contextlib.contextmanager
def _donor(manager: EmbeddingsManager, side: _Side) -> Iterator[Donor]:
    """Open *side* read-only as a donor, refusing one that may not donate."""
    if manager._slot is None:  # pragma: no cover - _destination() checked
        raise RuntimeError("No vector store slot without an embedding provider.")
    backend: StoreBackend[EmbeddingStore] = side.backend
    opened = _SourceDonor.open(
        backend,
        side.location,
        manager._slot.identity,
        builder=manager._embed_builder,
    )
    if isinstance(opened, DonorRefused):
        raise _refuse(_DONOR_REFUSALS[opened.reason], _donor_message(side, opened))
    try:
        yield opened
    finally:
        opened.close()


def _donor_message(side: _Side, refused: DonorRefused) -> str:
    """The operator's line for a source that may not donate."""
    reason: DonorRefusal = refused.reason
    if reason == "empty":
        return f"{side.label} holds no vectors; nothing to import."
    if reason == "identity_unknown":
        return (
            f"{side.label} records no embedding identity, so its vectors cannot be "
            "reused. Run index to embed with the configured provider."
        )
    detail = str(refused.detail).rstrip(".")
    if reason == "identity_differs":
        return (
            f"{side.label} holds vectors of another embedding model ({detail}). "
            "Configure that provider to import, or run index to embed with this one."
        )
    return f"{side.label} cannot be read: {detail}. No vectors were changed."


def _refuse(reason: ImportRefusal, message: str) -> ImportRefused:
    """Log one refusal and return it for the caller to raise."""
    logger.warning("embeddings_import_refused reason=%s", reason)
    return ImportRefused(message, reason=reason)
