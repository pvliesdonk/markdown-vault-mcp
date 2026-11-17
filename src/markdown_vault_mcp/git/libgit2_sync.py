"""Remote synchronization for the optional libgit2 store."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git._run import _normalize_remote, redact
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.conflict import (
    conflict_sibling_name,
    parse_conflict_note,
    with_conflict_metadata,
)
from markdown_vault_mcp.git.health import push_failure_reason
from markdown_vault_mcp.git.libgit2 import (
    Libgit2Store,
    _checked,
    _common,
    _config_bool,
    _open,
    _probe_config_hooks,
    _probe_policy,
    _pygit2,
    _tree_entry,
)
from markdown_vault_mcp.git.push_scheduler import PushBackendError, PushScheduler
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS,
    PULL_REASON_DIVERGED,
    PULL_REASON_FETCH_FAILED,
    PULL_REASON_NO_REMOTE,
    PULL_REASON_PULL_DISABLED,
    PULL_REASON_REBASED,
    PUSH_REASON_DRY_RUN_UNSUPPORTED,
    PUSH_REASON_NO_REMOTE,
    PUSH_REASON_PUSH_FAILED,
    PullResult,
    PushResult,
)
from markdown_vault_mcp.utils.fs import is_directory, is_regular_file, path_exists

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from contextlib import AbstractContextManager

    from markdown_vault_mcp.types import WriteBatchItem

logger = logging.getLogger(__name__)
_FALLBACK = "Set MARKDOWN_VAULT_MCP_GIT_BACKEND=cli to use the CLI backend."


def _ssh(url: str) -> bool:
    return url.startswith("ssh://") or ("://" not in url and ":" in url.split("/")[0])


def _safe(text: str, token: str | None) -> str:
    text = re.sub(r"(://)[^/@\s]+@", r"\1***@", text)
    return redact(text, token)


def _refusal(exc: ConfigurationError, token: str | None = None) -> ConfigurationError:
    text = _safe(str(exc), token)
    return ConfigurationError(text if _FALLBACK in text else f"{text} {_FALLBACK}")


def _push_declined(before: str, reason: str, detail: str) -> PushResult:
    return PushResult(False, 0, before, before, reason, detail)


def _callbacks(pg: Any, token: str | None, username: str, url: str) -> Any:
    class Callbacks(pg.RemoteCallbacks):  # type: ignore[misc]
        rejection: str | None = None
        credential_attempts = 0

        def credentials(self, request_url: str, _user: str | None, allowed: int) -> Any:
            expected, requested = urlsplit(url), urlsplit(request_url)
            if (
                token
                and expected.scheme == "https"
                and requested.scheme == "https"
                and expected.hostname == requested.hostname
                and expected.port == requested.port
                and allowed & pg.enums.CredentialType.USERPASS_PLAINTEXT
            ):
                self.credential_attempts += 1
                if self.credential_attempts > 3:
                    raise ValueError("Credential attempts exhausted")
                return pg.UserPass(username, token)
            raise ValueError("No supported credential available")

        def push_update_reference(self, _refname: str, message: str | None) -> None:
            self.rejection = message

    return Callbacks()


def _extra_policy(repo: Any) -> None:
    events = ("pre-push", "post-merge", "post-checkout", "pre-rebase", "post-rewrite")
    _probe_config_hooks(repo, events)
    configured = _tree_entry(repo.config, "core.hookspath")
    hooks = Path(configured or _common(Path(repo.path)) / "hooks").expanduser()
    for name in events:
        if os.access(Path(repo.workdir, hooks, name), os.X_OK):
            raise ConfigurationError(f"libgit2 cannot run {name} hooks. {_FALLBACK}")
    if _tree_entry(repo.config, "core.sshcommand"):
        raise ConfigurationError(f"libgit2 cannot honor core.sshCommand. {_FALLBACK}")


def _local_path(url: str, root: Path) -> Path | None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("", "file") or _ssh(url):
        return None
    return root / (unquote(parsed.path) if parsed.scheme else url)


def _local_remote_policy(pg: Any, url: str, root: Path) -> None:
    path = _local_path(url, root)
    if path is None or not path_exists(path):
        return
    repo = pg.Repository(str(path))
    configured = _tree_entry(repo.config, "core.hookspath")
    hooks = Path(repo.path, Path(configured or "hooks").expanduser())
    if any(
        os.access(hooks / name, os.X_OK)
        for name in ("pre-receive", "update", "post-receive", "post-update")
    ):
        raise ConfigurationError(
            f"libgit2 local transport bypasses remote hooks. {_FALLBACK}"
        )


def _check_tree(repo: Any, commit: Any, baseline: Any = None) -> None:
    """Check effective incoming attributes before checkout."""
    pg = _pygit2()
    deltas = list(repo.diff(baseline.tree, commit.tree).deltas) if baseline else []
    changed = any(
        Path(d.new_file.path).name == ".gitattributes"
        or Path(d.old_file.path).name == ".gitattributes"
        for d in deltas
    )
    paths = (
        (entry.path for entry in repo.index if entry.mode != 0o160000)
        if baseline is None or changed
        else (
            d.new_file.path
            for d in deltas
            if d.new_file.id != pg.Oid(hex="0" * 40) and d.new_file.mode != 0o160000
        )
    )
    for path in paths:
        encoding = repo.get_attr(
            path, "working-tree-encoding", pg.enums.AttrCheck.INDEX_ONLY
        )
        name = repo.get_attr(path, "filter", pg.enums.AttrCheck.INDEX_ONLY)
        if isinstance(encoding, str) and encoding:
            raise ConfigurationError(
                f"incoming working-tree-encoding is unsupported. {_FALLBACK}"
            )
        if isinstance(name, str) and (
            name == "lfs"
            or any(
                _tree_entry(repo.config, f"filter.{name}.{kind}")
                for kind in ("clean", "process")
            )
            or _config_bool(repo.config, f"filter.{name}.required")
        ):
            raise ConfigurationError(
                f"incoming attribute changes: external filter {name!r} (including Git LFS) is unsupported. {_FALLBACK}"
            )


def _parse_logged(content: bytes, conflict_with: str) -> Any:
    post, error = parse_conflict_note(content.decode("utf-8"))
    if error is not None:
        logger.warning(
            "git_conflict_frontmatter_parse_failed conflict_with=%s "
            "fallback=plain_content",
            conflict_with,
            exc_info=error,
        )
    return post


def _frontmatter(content: bytes, other: str, date: str) -> bytes:
    post = _parse_logged(content, other)
    return with_conflict_metadata(post, other, date).encode("utf-8")


def _conflict_contents(
    original: Path, ours: bytes, theirs: bytes | None, references: tuple[str, str, str]
) -> tuple[bytes, bytes | None]:
    if original.suffix.lower() != ".md":
        return ours, theirs
    try:
        ours.decode("utf-8")
        if theirs is not None:
            theirs.decode("utf-8")
    except UnicodeDecodeError:
        return ours, theirs
    original_path, sibling_path, date = references
    return (
        _frontmatter(ours, original_path, date),
        _frontmatter(theirs, sibling_path, date) if theirs is not None else None,
    )


class Libgit2SyncStore(Libgit2Store):
    """A versioned store with native clone, fetch, merge and push."""

    def __init__(  # noqa: PLR0913, RUF100 -- mirrors the configured strategy interface.
        self,
        repo_path: Path,
        *,
        token: str | None = None,
        username: str = "x-access-token",
        repo_url: str | None = None,
        managed: bool = False,
        enable_sync: bool = False,
        push_delay_s: float = 30,
        commit_name: str | None = None,
        commit_email: str | None = None,
    ) -> None:
        try:
            pg = _pygit2()
        except ConfigurationError as exc:
            raise ConfigurationError(f"{exc} {_FALLBACK}") from None
        self._token, self._username = token, username
        self._sync_path = repo_path
        self._managed, self._enable_sync = managed, enable_sync
        self._write_initialized = False
        self._checkout_observer: Callable[[], None] | None = None
        self._pause: Callable[[], AbstractContextManager[None]] | None = None
        self._drain: Callable[[], bool] | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        if managed:
            self._clone_if_needed(pg, repo_url)
        try:
            super().__init__(repo_path, commit_name, commit_email)
            self._validate_repository(repo_url)
        except ConfigurationError as exc:
            raise _refusal(exc, token) from None
        self._bootstrap = RepoBootstrap(
            lock=self._lock, token=token, username=username, repo_url=repo_url
        )
        self._bootstrap.git_root = Path(self._repo.workdir) if self._repo else None
        self._scheduler = PushScheduler(
            lock=self._lock,
            bootstrap=self._bootstrap,
            health=self._health,
            enable_push=enable_sync,
            push_delay_s=push_delay_s,
            push=self._push,
            has_unpushed=self._has_unpushed,
        )

    def _clone_if_needed(self, pg: Any, url: str | None) -> None:
        if not url:
            raise ConfigurationError(f"Managed mode requires a remote URL. {_FALLBACK}")
        self._check_transport(url, self._token)
        path = self._sync_path
        if path_exists(path) and (not is_directory(path) or any(path.iterdir())):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cloned = pg.clone_repository(
                url,
                str(path),
                callbacks=_callbacks(pg, self._token, self._username, url),
            )
            local = _local_path(url, path.parent)
            if cloned.head_is_unborn and local is not None:
                head = pg.Repository(str(local)).lookup_reference("HEAD").target
                if isinstance(head, str) and head.startswith("refs/heads/"):
                    cloned.set_head(head)
        except Exception as exc:
            raise ConfigurationError(
                f"Native clone failed: {_safe(str(exc), self._token)}. {_FALLBACK}"
            ) from None

    def _validate_repository(self, url: str | None) -> None:
        repo = self._repo
        if repo is None:
            if self._managed:
                raise ConfigurationError(
                    f"Managed source is not a repository. {_FALLBACK}"
                )
            return
        origin = self._origin()
        if self._enable_sync:
            self._gate(repo)
            if not repo.head_is_unborn:
                _check_tree(repo, repo.head.peel(self._pg.Commit))
        if self._managed and (
            not origin or _normalize_remote(origin.url) != _normalize_remote(url or "")
        ):
            raise ConfigurationError(
                f"Managed origin does not match configured remote. {_FALLBACK}"
            )

    def _gate(self, repo: Any) -> None:
        try:
            _checked(repo)
            _probe_policy(repo)
            _extra_policy(repo)
        except ConfigurationError as exc:
            raise _refusal(exc) from None
        if repo.is_shallow or _config_bool(repo.config, "core.sparsecheckout"):
            raise ConfigurationError(
                f"Shallow or sparse repositories are unsupported. {_FALLBACK}"
            )
        remote = self._origin()
        if remote:
            for url in (remote.url, remote.push_url or remote.url):
                self._check_transport(url, self._token)
            _local_remote_policy(
                self._pg, remote.push_url or remote.url, Path(repo.workdir)
            )

    @staticmethod
    def _check_transport(url: str, token: str | None) -> None:
        if _ssh(url):
            raise ConfigurationError(
                f"Native SSH host key verification is unavailable. {_FALLBACK}"
            )
        if token and urlsplit(url).scheme.lower() == "http":
            raise ConfigurationError(
                f"Token authentication requires HTTPS. {_FALLBACK}"
            )
        if urlsplit(url).scheme.lower() not in ("", "file", "http", "https"):
            raise ConfigurationError(f"Unsupported remote transport. {_FALLBACK}")

    @property
    def is_managed(self) -> bool:
        """Return whether this instance owns the clone."""
        return self._managed

    def _origin(self) -> Any | None:
        try:
            return self._repo.remotes["origin"] if self._repo else None
        except KeyError:
            return None

    def _discover(self) -> Any | None:
        """Adopt a checkout created after startup, applying the startup checks."""
        if self._repo is None:
            try:
                self._repo = _open(self._sync_path, self._pg)
                self._validate_repository(None)
            except ConfigurationError as exc:
                self._repo = None
                raise _refusal(exc, self._token) from None
            if self._repo is not None:
                self._bootstrap.git_root = Path(self._repo.workdir)
        return self._repo

    def _tracking(self) -> Any | None:
        if self._repo is None:
            return None
        head = self._repo.lookup_reference("HEAD").target
        branch = head.removeprefix("refs/heads/") if isinstance(head, str) else "HEAD"
        refs = (f"refs/remotes/origin/{branch}", "refs/remotes/origin/HEAD")
        ref = next(filter(None, map(self._repo.references.get, refs)), None)
        return ref.resolve() if ref else None

    def _callback(self, remote: Any, *, push: bool = False) -> Any:
        url = (remote.push_url or remote.url) if push else remote.url
        return _callbacks(self._pg, self._token, self._username, url)

    def on_write_batch(
        self,
        items: Sequence[WriteBatchItem],
        tool_name: str,
        *,
        primary_paths: frozenset[Path] | None = None,
    ) -> None:
        """Commit a write and schedule publication after releasing the store lock."""
        if self._closed or not items:
            return
        if self._enable_sync and not self._write_initialized:
            with self._lock:
                if not self._write_initialized:
                    self._scheduler.push_if_unpushed()
                    self._write_initialized = True
        before = (
            self.head_sha(self._sync_path)
            if self._repo and not self._repo.head_is_unborn
            else ""
        )
        super().on_write_batch(items, tool_name, primary_paths=primary_paths)
        after = (
            self.head_sha(self._sync_path)
            if self._repo and not self._repo.head_is_unborn
            else ""
        )
        if self._enable_sync and before != after:
            self._scheduler.schedule_push()

    @contextlib.contextmanager
    def _quiesce(self, *, skip: bool = False) -> Iterator[None]:
        if skip or self._pause is None or self._drain is None:
            yield
            return
        with self._pause():
            if not self._drain():
                logger.warning("git_write_queue_drain_incomplete action=proceed_anyway")
            yield

    def set_checkout_observer(self, observer: Callable[[], None]) -> None:
        """Install the checkout invalidation callback."""
        self._checkout_observer = observer

    def set_write_quiescer(
        self,
        pause_writes: Callable[[], AbstractContextManager[None]],
        drain_writes: Callable[[], bool],
    ) -> None:
        """Install the pause and drain functions for synchronized pulls."""
        self._pause, self._drain = pause_writes, drain_writes

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Fetch and reconcile the remote branch without invoking Git."""
        return self._force_pull(dry_run=dry_run, startup=False)

    def _force_pull(self, *, dry_run: bool, startup: bool) -> PullResult:
        if not self._enable_sync:
            return PullResult.head_unchanged_failure("", PULL_REASON_PULL_DISABLED)
        with self._quiesce(skip=dry_run), self._lock:
            result = self._pull(dry_run, startup=startup)
            if dry_run:
                return result
            if result.applied:
                self._health.pull_succeeded()
            elif result.reason:
                self._health.pull_failed(result.reason, result.detail)
            self._observe_safe()
            return result

    def _observe_safe(self) -> None:
        try:
            self._observe()
        except Exception as exc:
            logger.warning("git_worktree_check_failed error=%s", type(exc).__name__)

    def _pull(self, dry_run: bool, *, startup: bool = False) -> PullResult:  # noqa: PLR0911, RUF100 -- distinct outcomes preserve result contracts.
        before = ""
        try:
            repo = self._discover()
            before = str(repo.head.target) if repo and not repo.head_is_unborn else ""
            remote = self._origin()
            if remote is None or repo is None:
                return PullResult.head_unchanged_failure(before, PULL_REASON_NO_REMOTE)
            self._gate(repo)
            try:
                remote.fetch(callbacks=self._callback(remote))
            except Exception as exc:
                return PullResult.head_unchanged_failure(
                    before, PULL_REASON_FETCH_FAILED, _safe(str(exc), self._token)
                )
            tracking = self._tracking()
            if tracking is None:
                return PullResult.head_unchanged_failure(before, PULL_REASON_NO_REMOTE)
            # Incoming rules load into this handle's index, never the shared one.
            checkout, repo = repo, self._pg.Repository(repo.path)
            theirs = repo[tracking.target]
            ahead, behind = (
                repo.ahead_behind(repo.head.target, theirs.id)
                if before
                else (0, sum(1 for _ in repo.walk(theirs.id)))
            )
            if behind == 0:
                return PullResult(True, True, 0, before, before)
            baseline = repo.head.peel(self._pg.Commit) if before else None
            deltas = (
                list(repo.diff(baseline.tree, theirs.tree).deltas) if baseline else []
            )
            overlapping_attrs = any(
                Path(d.new_file.path).name == ".gitattributes"
                and is_regular_file(Path(repo.workdir) / d.new_file.path)
                for d in deltas
            )
            repo.index.read_tree(theirs.tree)
            _check_tree(repo, theirs, baseline)
            if dry_run:
                return PullResult(
                    False,
                    ahead == 0,
                    behind,
                    before,
                    str(theirs.id),
                    PULL_REASON_DIVERGED if ahead else None,
                )
            target, result_reason, siblings = self._merge(
                repo, theirs, ahead, overlapping_attrs
            )
            self._apply(checkout, before, target)
            return PullResult(
                True,
                ahead == 0,
                behind if not ahead else 0,
                before,
                str(target),
                result_reason,
                tuple(siblings),
            )
        except Exception as exc:
            detail = _safe(str(exc), self._token)
            if startup and isinstance(exc, ConfigurationError):
                raise ConfigurationError(detail) from None
            if "ambiguous path" in detail or (
                "conflict" in detail and "checkout" in detail
            ):
                detail = f"{detail}; inspect local changes and move untracked obstructions before retrying."
            return PullResult.head_unchanged_failure(
                before, PULL_REASON_CONFLICT_RESOLUTION_FAILED, detail
            )

    def _apply(self, repo: Any, before: str, target: Any) -> None:
        old_tree = repo[before].tree if before else repo[repo.TreeBuilder().write()]
        paths = list(
            {
                path
                for delta in repo.diff(old_tree, repo[target].tree).deltas
                for path in (delta.old_file.path, delta.new_file.path)
            }
        )
        dirty = []
        for path in paths:
            try:
                if repo.status_file(path):
                    dirty.append(path)
            except KeyError:
                pass
        if dirty:
            raise RuntimeError(f"Dirty overlapping paths: {', '.join(dirty)}")
        changed = False
        try:
            with repo.transaction() as transaction:
                transaction.lock_ref("HEAD")
                head = repo.lookup_reference("HEAD")
                name = head.target if isinstance(head.target, str) else "HEAD"
                if name != "HEAD":
                    transaction.lock_ref(name)
                current = "" if repo.head_is_unborn else str(repo.head.target)
                if repo.state() or current != before:
                    raise RuntimeError(
                        "HEAD changed or another Git operation is in progress"
                    )
                if self._checkout_observer:
                    self._checkout_observer()
                changed = True
                repo.checkout_tree(
                    repo[target],
                    strategy=self._pg.GIT_CHECKOUT_SAFE
                    | self._pg.GIT_CHECKOUT_DONT_OVERWRITE_IGNORED,
                )
                transaction.set_target(name, target, message="synchronize remote")
        except Exception:
            if changed and paths:
                repo.checkout_tree(
                    old_tree,
                    strategy=self._pg.GIT_CHECKOUT_FORCE
                    | self._pg.GIT_CHECKOUT_DISABLE_PATHSPEC_MATCH,
                    paths=paths,
                )
            raise

    def _merge(  # noqa: C901, RUF100 -- replay must handle each conflict shape.
        self, repo: Any, theirs: Any, ahead: int, overlapping_attrs: bool = False
    ) -> tuple[Any, str | None, list[str]]:
        if not ahead:
            return theirs.id, None, []
        head = repo.head.peel(self._pg.Commit)
        walk = repo.walk(
            head.id, self._pg.GIT_SORT_TOPOLOGICAL | self._pg.GIT_SORT_REVERSE
        )
        walk.hide(theirs.id)
        if any(len(commit.parent_ids) > 1 for commit in walk):
            raise ConfigurationError(
                f"Native replay cannot preserve local merge commits. {_FALLBACK}"
            )
        rebase = repo.rebase_init(
            branch=head,
            upstream=theirs,
            onto=theirs,
            inmemory=True,
            flags=self._pg.enums.MergeFlag.FIND_RENAMES,
        )
        signature = self._pg.Signature(self._name, self._email)
        target = theirs
        conflicts: dict[str, Any] = {}
        for _operation in rebase:
            index = rebase.inmemory_index
            if index.conflicts and overlapping_attrs:
                raise ConfigurationError(
                    f"Incoming attributes conflict with checkout attributes. {_FALLBACK}"
                )
            self._pair_renames(repo, _operation, target, index, conflicts)
            for ancestor, remote, local in list(index.conflicts or []):
                path = (local or remote or ancestor).path
                del index.conflicts[path]
                if local is None and remote is not None:
                    raise RuntimeError(
                        f"Local deletion conflicts with remote modification: {path}. Reconcile manually."
                    )
                if remote is not None:
                    index.add(remote)
                if local is not None:
                    conflicts[path] = local
            oid = rebase.commit(signature)
            if oid is not None:
                target = repo[oid]
        rebase.finish()
        if not conflicts:
            return target.id, PULL_REASON_REBASED, []
        return self._save_siblings(repo, head, target, conflicts, signature)

    def _pair_renames(
        self,
        repo: Any,
        operation: Any,
        target: Any,
        index: Any,
        conflicts: dict[str, Any],
    ) -> None:
        if (entries := index.conflicts) and repo[operation.id].parents:
            diff = repo.diff(repo[operation.id].parents[0].tree, target.tree)
            diff.find_similar(flags=self._pg.enums.DiffFind.FIND_RENAMES)
            for delta in diff.deltas:
                old, new = delta.old_file.path, delta.new_file.path
                if delta.status != self._pg.enums.DeltaStatus.RENAMED:
                    continue
                if old not in entries or new not in entries:
                    continue
                ancestor, remote, local = entries[old]
                new_ancestor, new_remote, new_local = entries[new]
                if not (
                    ancestor
                    and local
                    and not remote
                    and not new_ancestor
                    and new_remote
                    and not new_local
                ):
                    continue
                del entries[old]
                del entries[new]
                index.add(new_remote)
                conflicts[new] = local

    def _save_siblings(
        self,
        repo: Any,
        head: Any,
        target: Any,
        conflicts: dict[str, Any],
        signature: Any,
    ) -> tuple[Any, str, list[str]]:
        index = self._pg.Index()
        index.read_tree(target.tree)
        siblings = []
        date = datetime.now(UTC)
        head_index = self._pg.Index()
        head_index.read_tree(head.tree)
        for path, entry in conflicts.items():
            local = _tree_entry(head_index, entry.path) or entry
            original = _tree_entry(index, path)
            name = conflict_sibling_name(Path(path), date.strftime("%Y%m%d-%H%M%S"))
            counter = 1
            while _tree_entry(index, name.as_posix()) is not None or path_exists(
                Path(repo.workdir) / name
            ):
                name = conflict_sibling_name(
                    Path(path), date.strftime("%Y%m%d-%H%M%S"), counter
                )
                counter += 1
            try:
                root = self._sync_path.resolve()
                relative, sibling_relative = (
                    (Path(repo.workdir) / item).resolve().relative_to(root).as_posix()
                    for item in (path, name)
                )
                siblings.append(sibling_relative)
            except ValueError:
                relative, sibling_relative = path, name.as_posix()
            local_data = repo[local.id].data
            original_data = repo[original.id].data if original else None
            if local.mode != self._pg.GIT_FILEMODE_LINK and (
                original is None or original.mode != self._pg.GIT_FILEMODE_LINK
            ):
                local_data, original_data = _conflict_contents(
                    Path(path),
                    local_data,
                    original_data,
                    (relative, sibling_relative, date.isoformat(timespec="seconds")),
                )
            index.add(
                self._pg.IndexEntry(
                    name.as_posix(), repo.create_blob(local_data), local.mode
                )
            )
            if original is not None and original_data is not None:
                index.add(
                    self._pg.IndexEntry(
                        path, repo.create_blob(original_data), original.mode
                    )
                )
        tree = index.write_tree(repo)
        oid = repo.create_commit(
            None,
            signature,
            signature,
            f"conflict: saved {len(conflicts)} MCP version(s) for manual reconciliation",
            tree,
            [target.id],
        )
        return oid, PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS, siblings

    def _has_unpushed(self, _path: Path) -> bool:
        tracking = self._tracking()
        repo = self._repo
        return bool(
            repo
            and not repo.head_is_unborn
            and self._origin()
            and (
                not tracking or repo.ahead_behind(repo.head.target, tracking.target)[0]
            )
        )

    def _push(self, _path: Path) -> None:
        remote, tracking = self._origin(), self._tracking()
        repo = self._repo
        if not repo or not remote or repo.head_is_unborn or repo.head_is_detached:
            raise PushBackendError("No remote branch")
        ahead, behind = (
            repo.ahead_behind(repo.head.target, tracking.target) if tracking else (1, 0)
        )
        if behind:
            raise PushBackendError(
                "non-fast-forward: run git_sync(direction='pull') first.",
            )
        if not ahead:
            return
        try:
            self._gate(repo)
        except Exception as exc:
            raise PushBackendError(_safe(str(exc), self._token)) from None
        callback = self._callback(remote, push=True)
        branch = repo.head.shorthand
        try:
            remote.push(
                [f"refs/heads/{branch}:refs/heads/{branch}"], callbacks=callback
            )
            if callback.rejection:
                raise ValueError(callback.rejection)
        except Exception as exc:
            detail = _safe(str(exc), self._token)
            try:
                remote.fetch(callbacks=self._callback(remote))
                refreshed = self._tracking()
                if (
                    refreshed
                    and repo.ahead_behind(repo.head.target, refreshed.target)[1]
                ):
                    detail = "non-fast-forward: run git_sync(direction='pull') first."
            except Exception:
                logger.debug("git_push_rejection_probe_failed")
            raise PushBackendError(detail) from None

    def force_push(self, *, dry_run: bool = False) -> PushResult:
        """Push the current branch and report the tracked remote outcome."""
        with self._lock:
            try:
                repo = self._discover()
            except ConfigurationError as exc:
                if not dry_run:
                    self._health.push_failed(PUSH_REASON_PUSH_FAILED, str(exc))
                return _push_declined("", PUSH_REASON_PUSH_FAILED, str(exc))
            tracking = self._tracking()
            before = str(tracking.target) if tracking else ""
            if not repo or repo.head_is_unborn or not self._origin() or not tracking:
                if not dry_run:
                    self._observe_safe()
                return _push_declined(
                    before,
                    PUSH_REASON_NO_REMOTE,
                    "No remote-tracking branch (origin/<branch>) could be "
                    "resolved for the current branch.  Push the branch to "
                    "origin once (`git push -u origin <branch>`) so the "
                    "remote-tracking ref exists.",
                )
            if dry_run:
                return _push_declined(
                    before,
                    PUSH_REASON_DRY_RUN_UNSUPPORTED,
                    "Push dry run is not supported.",
                )
            try:
                ahead = repo.ahead_behind(repo.head.target, tracking.target)[0]
                self._push(self._sync_path)
            except PushBackendError as exc:
                reason = push_failure_reason(str(exc))
                self._health.push_failed(reason, str(exc))
                return _push_declined(before, reason, str(exc)[:200])
            except Exception as exc:
                detail = _safe(str(exc), self._token)
                self._health.push_failed(PUSH_REASON_PUSH_FAILED, detail)
                return _push_declined(before, PUSH_REASON_PUSH_FAILED, detail)
            finally:
                self._observe_safe()
            self._health.push_succeeded()
            return PushResult(True, ahead, before, str(repo.head.target))

    def sync_once(self, repo_path: Path, *, timer_driven: bool = False) -> bool:
        """Run one pull; outside the loop, refuse unsupported shapes."""
        del repo_path
        if self._closed:
            return False
        result = self._force_pull(dry_run=False, startup=not timer_driven)
        return result.applied and result.from_sha != result.to_sha

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        on_pull: Callable[[], object] | None = None,
        on_tick: Callable[[], object] | None = None,
    ) -> None:
        """Start periodic synchronization and retry pending pushes."""
        if self._closed or not self._enable_sync or pull_interval_s <= 0:
            return

        def loop() -> None:
            while not self._stop_event.is_set():
                try:
                    advanced = self.sync_once(repo_path, timer_driven=True)
                    if advanced and on_pull:
                        with self._pause() if self._pause else contextlib.nullcontext():
                            on_pull()
                    if on_tick:
                        on_tick()
                    self._scheduler.do_push_safe(retry=True)
                except Exception as exc:
                    logger.error(
                        "git_pull_loop_tick_failed error=%s",
                        _safe(str(exc), self._token),
                    )
                if self._stop_event.wait(pull_interval_s):
                    break

        with self._lock:
            if self._closed or self._thread or not self._origin():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=loop, name="Libgit2PullLoop", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the periodic synchronization thread."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
        thread.join(timeout=5)
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def flush(self) -> None:
        """Push any scheduled commits."""
        self._scheduler.flush()

    def close(self) -> None:
        """Stop synchronization and flush pending commits."""
        self._closed = True
        self.stop()
        self.flush()
