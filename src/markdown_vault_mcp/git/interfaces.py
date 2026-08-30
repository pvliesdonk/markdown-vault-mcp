"""Narrow protocols for the versioned-filestore seam (#1229).

``GitWriteStrategy`` carries three concerns that its consumers want in
different combinations: **history** (log/diff), **syncing** (pull/push), and
**versioning** (the per-write commit).  ``GitQueryManager`` reads history and
nothing else; the webhook pulls and nothing else; the write path commits and
syncs.  These protocols make that split explicit so each consumer depends on
the surface it actually uses.

Deliberately **one implementation object, three interfaces** — not three
objects.  ``GitWriteStrategy`` composes ``RepoBootstrap`` and ``PushScheduler``
over a single shared :class:`threading.Lock`, and :meth:`Vault.close` tells the
commit callback from the store by object identity (``on_write is
git_strategy``).  Splitting the object would break both; splitting the *type*
costs nothing.

The module imports nothing at runtime beyond ``typing``, which keeps the
``git`` package free of a ``fastmcp`` dependency (asserted by a guard test) and
lets any consumer depend on the seam without pulling in the implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import contextlib
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.git.types import PullResult, PushResult
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry, WriteOperation

__all__ = [
    "HistorySource",
    "Syncer",
    "VersionedStore",
    "Versioner",
]


@runtime_checkable
class HistorySource(Protocol):
    """Read-only access to a document's revision history.

    The narrowest of the three facets: ``GitQueryManager`` depends on this and
    nothing else, so a backend that can answer "what changed, and when" is
    enough to serve the history and diff tools.
    """

    def get_file_history(
        self,
        repo_path: Path,
        path: Path | None,
        since: str | None,
        limit: int,
        until: str | None = None,
        *,
        is_dir: bool = False,
    ) -> list[HistoryEntry]:
        """Return commits touching *path*, newest first.

        Args:
            repo_path: The working tree to query.
            path: The file or directory to filter by, or ``None`` for the
                whole tree.
            since: Lower bound on commit time, in any form the backend
                accepts, or ``None`` for unbounded.
            limit: Maximum number of entries to return.
            until: Upper bound on commit time, or ``None`` for unbounded.
            is_dir: Whether *path* names a directory rather than a file.

        Returns:
            The matching history entries, newest first.
        """
        ...

    def get_file_diff(
        self,
        repo_path: Path,
        path: Path,
        ref: str | None,
        per_commit: bool,
        since_timestamp: str | None = None,
        limit: int | None = None,
        *,
        summarize_binary: bool = False,
    ) -> str | list[CommitDiff]:
        """Return the changes to *path* since *ref* or *since_timestamp*.

        Args:
            repo_path: The working tree to query.
            path: The file to diff.
            ref: The revision to diff against, or ``None`` when
                *since_timestamp* is supplied instead.
            per_commit: Whether to return one diff per commit rather than a
                single aggregate diff.
            since_timestamp: Lower bound on commit time, used in place of
                *ref*.
            limit: Maximum number of commits to include, or ``None``.
            summarize_binary: Whether to replace binary diffs with a summary
                line.

        Returns:
            A unified diff when *per_commit* is false, else one entry per
            commit.
        """
        ...


@runtime_checkable
class Syncer(Protocol):
    """Replication against a remote, plus the lifecycle that drives it.

    Carries the pull/push surface *and* the loop lifecycle, because the two
    consumers of this facet — :class:`Vault` and the ``git_sync`` tool — use
    them together; a backend that syncs is also the thing that has to be
    started, flushed, and closed.
    """

    @property
    def is_managed(self) -> bool:
        """Whether this store owns a managed clone of a remote repository.

        The ``git_sync`` tool is only meaningful for a managed deployment, and
        gates on this.
        """
        ...

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Pull from the remote synchronously.

        Args:
            dry_run: Report what a pull would do without changing the tree.

        Returns:
            The structured outcome, including a reason code when no pull ran.
        """
        ...

    def force_push(self, *, dry_run: bool = False) -> PushResult:
        """Push local commits to the remote synchronously.

        Args:
            dry_run: Report what a push would do without contacting the
                remote.

        Returns:
            The structured outcome, including a reason code when no push ran.
        """
        ...

    def sync_once(self, repo_path: Path) -> bool:
        """Fetch and fast-forward once.

        Args:
            repo_path: The working tree to update.

        Returns:
            ``True`` when the local head advanced.
        """
        ...

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        on_pull: Callable[[], object] | None = None,
    ) -> None:
        """Start the periodic background pull loop.

        Args:
            repo_path: The working tree to keep in sync.
            pull_interval_s: Seconds between pulls; non-positive disables the
                loop.
            on_pull: Called after a pull that advanced the head, so the owner
                can reindex.
        """
        ...

    def stop(self) -> None:
        """Stop the background pull loop, leaving the store usable."""
        ...

    def flush(self) -> None:
        """Block until any deferred push has completed."""
        ...

    def close(self) -> None:
        """Flush pending work and release resources permanently."""
        ...

    def set_write_quiescer(
        self,
        pause_writes: Callable[[], contextlib.AbstractContextManager[None]],
        drain_writes: Callable[[], bool],
    ) -> None:
        """Wire the callables that hold writes still during a pull.

        Args:
            pause_writes: Context manager suspending new writes for its
                duration.
            drain_writes: Blocks until in-flight writes finish, returning
                whether the queue drained.
        """
        ...

    def resolve_force_repo(self) -> Path:
        """Return the working tree the ``force_*`` methods operate on.

        Raises:
            RuntimeError: When the store was constructed without one.
        """
        ...

    def head_sha(self, git_root: Path) -> str:
        """Return the current head revision of *git_root*.

        Args:
            git_root: The working tree to read.

        Returns:
            The full revision identifier.
        """
        ...

    def branch_name(self, git_root: Path) -> str:
        """Return the checked-out branch name of *git_root*.

        Args:
            git_root: The working tree to read.

        Returns:
            The branch name; a detached head yields ``"HEAD"``.
        """
        ...


@runtime_checkable
class Versioner(Protocol):
    """Commits a single write, attributed to the acting principal.

    Structurally identical to
    :class:`~markdown_vault_mcp.types.PrincipalAwareWriteCallback`, which the
    write-callback dispatcher already types against — the two are
    interchangeable, since protocols match by shape.  Restated here rather
    than subclassed so this module needs no runtime import, keeping the seam
    depending on nothing but ``typing``.
    """

    accepts_principal: bool

    def __call__(
        self,
        path: Path,
        content: str,
        operation: WriteOperation,
        *,
        old_path: Path | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Commit one write, optionally told who performed it.

        Args:
            path: The file that changed.
            content: Its new content; ignored by backends that read the file.
            operation: Which kind of write occurred.
            old_path: The previous path, for a rename.
            principal: Who performed the write, for attribution.
        """
        ...


@runtime_checkable
class VersionedStore(HistorySource, Syncer, Versioner, Protocol):
    """All three facets on one object — what :class:`Vault` actually holds.

    The vault receives the same object twice: as ``git_strategy`` for history
    and syncing, and as ``on_write`` for the commit.  That is deliberate, and
    :meth:`Vault.close` relies on it — it compares the two by identity to
    avoid closing one object twice.  Consumers should depend on the single
    facet they use; this composition exists for the owner that holds all of
    them.
    """
