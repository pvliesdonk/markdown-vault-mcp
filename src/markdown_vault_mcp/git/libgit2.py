"""Local versioning with libgit2; see docs/design/reference/git-libgit2.md."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.health import SyncHealthTracker
from markdown_vault_mcp.git.strategy import (
    GitWriteStrategy,
    _batch_subject,
    _sanitize_git_identity,
)
from markdown_vault_mcp.git.types import (
    PULL_REASON_PULL_DISABLED,
    PUSH_REASON_PUSH_FAILED,
    PullResult,
    PushResult,
)
from markdown_vault_mcp.utils.fs import is_regular_file, path_exists

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from contextlib import AbstractContextManager

    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.git.health import SyncHealth
    from markdown_vault_mcp.git.types import RevisionQuery
    from markdown_vault_mcp.types import (
        CommitDiff,
        HistoryEntry,
        RevisionContent,
        WriteBatchItem,
        WriteOperation,
    )


def _unsupported(shape: str) -> ConfigurationError:
    return ConfigurationError(
        f"libgit2 cannot serve {shape}; use GitWriteStrategy (the CLI layer)."
    )


def _pygit2() -> Any:
    try:
        import pygit2
    except ImportError as exc:
        raise ConfigurationError(
            'Install "markdown-vault-mcp[libgit2]" or use GitWriteStrategy (the CLI layer).'
        ) from exc
    return pygit2


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    try:
        return config[key]
    except KeyError:
        return default


def _config_bool(config: Any, key: str, default: bool = False) -> bool:
    try:
        return bool(config.get_bool(key))
    except KeyError:
        return default


def _rel(repo: Any, path: Path) -> str:
    return (
        (path.parent.resolve() / path.name)
        .relative_to(Path(repo.workdir).resolve())
        .as_posix()
    )


def _probe_config(gitdir: Path, pg: Any) -> None:
    try:
        common = gitdir / "commondir"
        if is_regular_file(common):
            gitdir = (gitdir / common.read_text().strip()).resolve()
        config = pg.Config(str(gitdir / "config"))
        keys = {entry.name.lower(): entry.value for entry in config}
    except (OSError, pg.GitError) as exc:
        raise _unsupported("an unreadable repository configuration") from exc
    for key, value, shape in (
        ("extensions.objectformat", "sha256", "SHA-256 repositories"),
        ("extensions.refstorage", "reftable", "reftable repositories"),
    ):
        if keys.get(key, "").lower() == value:
            raise _unsupported(shape)
    if "extensions.partialclone" in keys or any(
        k.startswith("remote.") and k.endswith(".promisor") and config.get_bool(k)
        for k in keys
    ):
        raise _unsupported("partial clones")


def _probe_policy(repo: Any) -> None:
    if _config_bool(repo.config, "commit.gpgsign"):
        raise _unsupported("signed commits")
    configured = _config_value(repo.config, "core.hookspath")
    gitdir = Path(repo.path)
    common = gitdir / "commondir"
    if is_regular_file(common):
        gitdir = (gitdir / common.read_text().strip()).resolve()
    hooks = Path(configured).expanduser() if configured else gitdir / "hooks"
    if not hooks.is_absolute():
        hooks = Path(repo.workdir) / hooks
    for name in (
        "pre-commit",
        "prepare-commit-msg",
        "commit-msg",
        "post-commit",
        "reference-transaction",
        "post-index-change",
    ):
        if os.access(hooks / name, os.X_OK):
            raise _unsupported(f"executable {name} hooks")


def _open(path: Path, pg: Any) -> Any | None:
    existing = path
    while not path_exists(existing) and existing != existing.parent:
        existing = existing.parent
    if is_regular_file(existing):
        existing = existing.parent
    gitdir = pg.discover_repository(str(existing))
    if gitdir is None:
        return None
    _probe_config(Path(gitdir), pg)
    try:
        repo = pg.Repository(gitdir)
        if repo.is_bare or repo.workdir is None:
            raise _unsupported("bare repositories")
        _probe_policy(repo)
        for entry in repo.index:
            _filter_guard(repo, entry.path)
        return repo
    except pg.GitError as exc:
        raise _unsupported("an unopenable repository") from exc


def _filter_guard(repo: Any, path: str) -> None:
    name = repo.get_attr(path, "filter")
    if isinstance(name, str) and any(
        _config_value(repo.config, f"filter.{name}.{kind}")
        for kind in ("clean", "process")
    ):
        raise _unsupported(f"the effective {name!r} filter on {path!r}")


def _tree_entry(index: Any, path: str) -> Any | None:
    try:
        return index[path]
    except KeyError:
        return None


def _index_path(repo: Any, index: Any, relative: str, pg: Any) -> bool:
    absolute = Path(repo.workdir) / relative
    existing = _tree_entry(index, relative)
    tracked = existing is not None
    try:
        mode = absolute.lstat().st_mode
    except FileNotFoundError:
        if tracked:
            index.remove(relative)
        return tracked
    if not tracked and repo.path_is_ignored(relative):
        return False
    _filter_guard(repo, relative)
    if stat.S_ISLNK(mode):
        oid = repo.create_blob(os.fsencode(absolute.readlink()))
        filemode = pg.GIT_FILEMODE_LINK
    elif stat.S_ISREG(mode):
        oid = repo.create_blob_fromworkdir(relative)
        filemode = (
            pg.GIT_FILEMODE_BLOB_EXECUTABLE
            if mode & stat.S_IXUSR
            else pg.GIT_FILEMODE_BLOB
        )
    else:
        return False
    if (
        existing is not None
        and filemode in (pg.GIT_FILEMODE_BLOB, pg.GIT_FILEMODE_BLOB_EXECUTABLE)
        and str(_config_value(repo.config, "core.filemode", "true")).lower()
        in {"false", "no", "0", "off"}
    ):
        filemode = existing.mode
    index.add(pg.IndexEntry(relative, oid, filemode))
    return True


def _stage_items(
    repo: Any, index: Any, items: Sequence[WriteBatchItem], pg: Any
) -> tuple[set[str], list[tuple[Path, WriteOperation, Path | None]]]:
    paths: set[str] = set()
    staged = []
    for path, operation, old_path, _ in items:
        changed = False
        for item in (path, old_path):
            if item is not None:
                rel = _rel(repo, item)
                if _index_path(repo, index, rel, pg):
                    paths.add(rel)
                    changed = True
        if changed:
            staged.append((path, operation, old_path))
    return paths, staged


@contextmanager
def _locked_index(repo: Any, pg: Any) -> Iterator[tuple[Any, Path, Path]]:
    gitdir = Path(repo.path)
    lock = gitdir / "index.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    try:
        os.close(fd)
        with tempfile.TemporaryDirectory(dir=gitdir) as directory:
            source, temporary = gitdir / "index", Path(directory) / "index"
            if path_exists(source):
                shutil.copyfile(source, temporary)
            yield pg.Index(temporary), temporary, source
    finally:
        lock.unlink()


def _update_index(real: Any, index: Any, paths: set[str]) -> None:
    for rel in paths:
        entry = _tree_entry(index, rel)
        if entry is None:
            if _tree_entry(real, rel) is not None:
                real.remove(rel)
        else:
            real.add(entry)


class Libgit2Store:
    """Provide local versioning without invoking Git's command line.

    Args:
        repo_path: Vault directory, optionally inside a Git working tree.
        commit_name: Committer name; defaults to the CLI store's identity.
        commit_email: Committer email; defaults to the CLI store's identity.
    """

    accepts_principal = True
    accepts_batch = True
    accepts_old_path = True
    accepts_primary_paths = True

    def __init__(
        self,
        repo_path: Path | None = None,
        commit_name: str | None = None,
        commit_email: str | None = None,
    ) -> None:
        self._pg = _pygit2()
        from markdown_vault_mcp.git import libgit2_history

        self._history = libgit2_history
        self._repo_path = repo_path
        self._repo = _open(repo_path, self._pg) if repo_path is not None else None
        self._name = _sanitize_git_identity(
            commit_name or GitWriteStrategy.DEFAULT_COMMIT_NAME
        )
        self._email = _sanitize_git_identity(
            commit_email or GitWriteStrategy.DEFAULT_COMMIT_EMAIL
        )
        self._lock = threading.Lock()
        self._health = SyncHealthTracker()
        self._observer: Callable[[str, str], None] | None = None
        self._closed = False
        self._observe()

    def _observe(self) -> None:
        if self._repo is not None:
            submodules = set(self._repo.listall_submodules())
            self._health.worktree_observed(
                dirty=any(p not in submodules for p in self._repo.status())
            )

    def _repository(self, path: Path) -> Any | None:
        if self._repo is None and self._repo_path is None:
            self._repo = _open(path, self._pg)
        return self._repo

    def _committed(self, before: str, after: str) -> None:
        self._observe()
        if self._observer is not None and before and before != after:
            self._observer(before, after)

    def __call__(
        self,
        path: Path,
        content: str,
        operation: WriteOperation,
        *,
        old_path: Path | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Commit the paths touched by one write, preserving unrelated staging."""
        del content
        self.on_write_batch(
            [(path, operation, old_path, principal)],
            operation,
            primary_paths=frozenset({path}),
        )

    def on_write_batch(  # noqa: C901, RUF100 -- locked publication and no-op reconciliation share one transaction.
        self,
        items: Sequence[WriteBatchItem],
        tool_name: str,
        *,
        primary_paths: frozenset[Path] | None = None,
    ) -> None:
        """Commit a tool call's writes together with their principal's identity."""
        if self._closed or not items:
            return
        try:
            with self._lock:
                repo = self._repository(items[0][0])
                if repo is None:
                    return
                _probe_policy(repo)
                with (
                    _locked_index(repo, self._pg) as (real, temporary, destination),
                    repo.transaction() as transaction,
                ):
                    transaction.lock_ref("HEAD")
                    head = repo.lookup_reference("HEAD")
                    refname = head.target if isinstance(head.target, str) else "HEAD"
                    if refname != "HEAD":
                        transaction.lock_ref(refname)
                    if repo.state():
                        raise _unsupported("in-progress Git operations")
                    root = Path(repo.workdir)
                    index = self._pg.Index()
                    parent = (
                        None if repo.head_is_unborn else repo.head.peel(self._pg.Commit)
                    )
                    if parent is not None:
                        index.read_tree(parent.tree)
                    paths, staged = _stage_items(repo, index, items, self._pg)
                    if not paths:
                        return
                    tree = index.write_tree(repo)
                    noop = parent is not None and tree == parent.tree_id
                    primary: dict[Path, Any] = {}
                    for item in staged:
                        if primary_paths and item[0] in primary_paths:
                            primary.setdefault(item[0], item)
                    subject = _batch_subject(
                        root, len(staged), tool_name, primary_paths, [*primary.values()]
                    )
                    principal = items[0][3]
                    author = self._pg.Signature(
                        _sanitize_git_identity(
                            (principal and principal.display_name) or self._name
                        ),
                        _sanitize_git_identity(
                            (principal and principal.email) or self._email
                        ),
                    )
                    committer = self._pg.Signature(self._name, self._email)
                    before = str(parent.id) if parent is not None else ""
                    after = (
                        parent.id
                        if parent is not None and noop
                        else repo.create_commit(
                            None,
                            author,
                            committer,
                            subject,
                            tree,
                            [parent.id] if parent is not None else [],
                        )
                    )
                    _update_index(real, index, paths)
                    real.write()
                    if not noop:
                        transaction.set_target(refname, after, message=subject)
                    temporary.replace(destination)
                self._committed(before, str(after))
        except Exception:
            with suppress(Exception):
                self._observe()
            raise

    @property
    def is_managed(self) -> bool:
        """Return whether this local store owns a managed clone."""
        return False

    def resolve_force_repo(self) -> Path:
        """Return the configured vault, or raise when none was configured."""
        if self._repo_path is None:
            raise RuntimeError(
                "Libgit2Store.force_* requires repo_path at construction."
            )
        return self._repo_path

    def head_sha(self, git_root: Path) -> str:
        """Return the current commit ID."""
        repo = self._repository(git_root)
        if repo is None or repo.head_is_unborn:
            raise OSError("No Git HEAD")
        return str(repo.head.target)

    def branch_name(self, git_root: Path) -> str:
        """Return the branch name, or HEAD for a detached checkout."""
        repo = self._repository(git_root)
        if repo is None or repo.head_is_unborn:
            raise OSError("No Git HEAD")
        return "HEAD" if repo.head_is_detached else repo.head.shorthand

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Report that this local store does not pull."""
        del dry_run
        self.resolve_force_repo()
        return PullResult.head_unchanged_failure("", PULL_REASON_PULL_DISABLED)

    def force_push(self, *, dry_run: bool = False) -> PushResult:
        """Report that this local store does not push."""
        del dry_run
        self.resolve_force_repo()
        return PushResult(
            False,
            0,
            "",
            "",
            PUSH_REASON_PUSH_FAILED,
            "This local store does not push; use GitWriteStrategy or the Git CLI.",
        )

    def sync_once(self, repo_path: Path) -> bool:
        """Perform no synchronization and report no change."""
        del repo_path
        return False

    def sync_health(self) -> SyncHealth | None:
        """Return the latest observed uncommitted-work condition."""
        return self._health.snapshot()

    def set_commit_observer(self, observer: Callable[[str, str], None]) -> None:
        """Set the callback for successful HEAD moves."""
        self._observer = observer

    def set_write_quiescer(
        self,
        pause_writes: Callable[[], AbstractContextManager[None]],
        drain_writes: Callable[[], bool],
    ) -> None:
        """Accept the synchronization interface; the local store does not sync."""
        del pause_writes, drain_writes

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        on_pull: Callable[[], object] | None = None,
        on_tick: Callable[[], object] | None = None,
    ) -> None:
        """Start the local store, which needs no background loop."""
        del pull_interval_s, on_pull, on_tick
        self._repository(repo_path)

    def stop(self) -> None:
        """Stop the local store's nonexistent background loop."""

    def flush(self) -> None:
        """Flush local work, which is already committed synchronously."""

    def close(self) -> None:
        """Close the local store; repeated calls are safe."""
        self._closed = True

    def get_file_history(  # noqa: PLR0913, RUF100 -- mirrors the HistorySource protocol.
        self,
        repo_path: Path,
        path: Path | None,
        since: str | None,
        limit: int,
        until: str | None = None,
        *,
        is_dir: bool = False,
    ) -> list[HistoryEntry]:
        """Return local commit history, optionally scoped to a path."""
        return self._history.get_file_history(
            self._repository(repo_path),
            repo_path,
            path,
            since,
            limit,
            until,
            is_dir=is_dir,
        )

    def get_file_diff(  # noqa: PLR0913, RUF100 -- mirrors the HistorySource protocol.
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
        """Return aggregate or individual diffs for a note."""
        return self._history.get_file_diff(
            self._repository(repo_path),
            repo_path,
            path,
            ref,
            per_commit,
            since_timestamp,
            limit,
            summarize_binary=summarize_binary,
        )

    def get_file_at_ref(self, query: RevisionQuery) -> RevisionContent:
        """Return the note's content at an ancestral revision."""
        return self._history.get_file_at_ref(self._repository(query.repo_path), query)

    def committed_revision(self, repo_path: Path, path: Path) -> str | None:
        """Return the latest matching committed content, if any."""
        return self._history.committed_revision(self._repository(repo_path), path)
