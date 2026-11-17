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

import frontmatter

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.libgit2 import (
    Libgit2Store,
    _filter_guard,
    _probe_policy,
    _pygit2,
    _tree_entry,
)
from markdown_vault_mcp.git.push_scheduler import PushBackendError, PushScheduler
from markdown_vault_mcp.git.types import PullResult, PushResult
from markdown_vault_mcp.scanner import parse_frontmatter

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
    return text.replace(token, "***") if token else text


def _normalize(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git")


def _callbacks(pg: Any, token: str | None, username: str, url: str) -> Any:
    class Callbacks(pg.RemoteCallbacks):  # type: ignore[misc]
        rejection: str | None = None

        def credentials(self, request_url: str, user: str | None, allowed: int) -> Any:
            expected, requested = urlsplit(url), urlsplit(request_url)
            if (
                token
                and expected.scheme == "https"
                and requested.scheme == "https"
                and expected.hostname == requested.hostname
                and expected.port == requested.port
                and allowed & pg.enums.CredentialType.USERPASS_PLAINTEXT
            ):
                return pg.UserPass(username, token)
            if _ssh(url) and allowed & pg.enums.CredentialType.SSH_KEY:
                return pg.KeypairFromAgent(user or "git")
            if _ssh(url) and allowed & pg.enums.CredentialType.USERNAME:
                return pg.Username(user or "git")
            raise ValueError("No supported credential available")

        def push_update_reference(self, _refname: str, message: str | None) -> None:
            self.rejection = message

    return Callbacks()


def _extra_policy(repo: Any) -> None:
    hooks_path = _tree_entry(repo.config, "core.hookspath")
    gitdir = Path(repo.path)
    common = gitdir / "commondir"
    if common.is_file():
        gitdir = (gitdir / common.read_text().strip()).resolve()
    hooks = Path(hooks_path).expanduser() if hooks_path else gitdir / "hooks"
    if not hooks.is_absolute():
        hooks = Path(repo.workdir) / hooks
    for name in ("pre-push", "post-merge", "post-checkout"):
        if os.access(hooks / name, os.X_OK):
            raise ConfigurationError(f"libgit2 cannot run {name} hooks. {_FALLBACK}")
    if _tree_entry(repo.config, "core.sshcommand"):
        raise ConfigurationError(f"libgit2 cannot honor core.sshCommand. {_FALLBACK}")


def _local_remote_policy(pg: Any, url: str, root: Path) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("", "file") or _ssh(url):
        return
    path = Path(unquote(parsed.path) if parsed.scheme else url)
    if not path.is_absolute():
        path = root / path
    repo = pg.Repository(str(path))
    configured = _tree_entry(repo.config, "core.hookspath")
    hooks = Path(configured).expanduser() if configured else Path(repo.path) / "hooks"
    if not hooks.is_absolute():
        hooks = Path(repo.path) / hooks
    if any(
        os.access(hooks / name, os.X_OK)
        for name in ("pre-receive", "update", "post-receive", "post-update")
    ):
        raise PushBackendError(
            f"libgit2 local transport bypasses remote hooks. {_FALLBACK}",
            reason="push_failed",
        )


def _check_tree(repo: Any, tree: Any, prefix: str = "") -> None:
    """Refuse incoming attributes which need external transformations."""
    for entry in tree:
        obj = repo[entry.id]
        path = f"{prefix}{entry.name}"
        if obj.type_str == "tree":
            _check_tree(repo, obj, f"{path}/")
        elif entry.name == ".gitattributes":
            data = obj.data.decode("utf-8", errors="replace")
            for line in data.splitlines():
                if not line.lstrip().startswith("#") and re.search(
                    r"(?:^|\s)(?:filter=|working-tree-encoding=)", line
                ):
                    raise ConfigurationError(
                        f"incoming external filter or encoding is unsupported. {_FALLBACK}"
                    )
        else:
            _filter_guard(repo, path)
            if repo.get_attr(path, "filter") == "lfs":
                raise ConfigurationError(
                    f"Git LFS is unsupported for {path!r}. {_FALLBACK}"
                )


def _frontmatter(content: bytes, other: str, date: str) -> bytes:
    text = content.decode("utf-8")
    try:
        post = parse_frontmatter(text)
    except Exception:
        post = frontmatter.Post(text)
    post.metadata["conflict_with"] = other
    post.metadata["conflict_date"] = date
    return frontmatter.dumps(post).encode("utf-8")


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
        pg = _pygit2()
        self._token, self._username = token, username
        self._sync_path = repo_path
        self._managed, self._enable_sync = managed, enable_sync
        self._write_initialized = False
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
            raise ConfigurationError(f"{_safe(str(exc), token)} {_FALLBACK}") from None
        bootstrap = RepoBootstrap(
            lock=self._lock, token=token, username=username, repo_url=repo_url
        )
        bootstrap.git_root = Path(self._repo.workdir) if self._repo else None
        self._scheduler = PushScheduler(
            lock=self._lock,
            bootstrap=bootstrap,
            health=self._health,
            enable_push=enable_sync,
            push_delay_s=push_delay_s,
            push=self._push,
            has_unpushed=self._has_unpushed,
        )

    def _clone_if_needed(self, pg: Any, url: str | None) -> None:
        if not url:
            raise ConfigurationError(f"Managed mode requires a remote URL. {_FALLBACK}")
        self._check_transport(pg, url, self._token)
        path = self._sync_path
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            pg.clone_repository(
                url,
                str(path),
                callbacks=_callbacks(pg, self._token, self._username, url),
            )
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
        _extra_policy(repo)
        if not repo.head_is_unborn:
            _check_tree(repo, repo.head.peel(self._pg.Commit).tree)
        origin = self._origin()
        if origin:
            self._check_transport(self._pg, origin.url, self._token)
        if self._managed and (
            not origin or _normalize(origin.url) != _normalize(url or "")
        ):
            raise ConfigurationError(
                f"Managed origin does not match configured remote. {_FALLBACK}"
            )

    @staticmethod
    def _check_transport(pg: Any, url: str, token: str | None) -> None:
        if token and _ssh(url):
            raise ConfigurationError(
                f"Token authentication requires HTTPS, not SSH. {_FALLBACK}"
            )
        if token and urlsplit(url).scheme.lower() == "http":
            raise ConfigurationError(
                f"Token authentication requires HTTPS. {_FALLBACK}"
            )
        if _ssh(url) and not (pg.features & pg.enums.Feature.SSH):
            raise ConfigurationError(
                f"This libgit2 build lacks SSH support. {_FALLBACK}"
            )

    @property
    def is_managed(self) -> bool:
        """Return whether this instance owns the clone."""
        return self._managed

    def _origin(self) -> Any | None:
        try:
            return self._repo.remotes["origin"] if self._repo else None
        except KeyError:
            return None

    def _tracking(self) -> Any | None:
        if self._repo is None or self._repo.head_is_unborn:
            return None
        branch = "HEAD" if self._repo.head_is_detached else self._repo.head.shorthand
        for name in (f"refs/remotes/origin/{branch}", "refs/remotes/origin/HEAD"):
            ref = self._repo.references.get(name)
            if ref:
                return ref.resolve()
        return None

    def _callback(self, remote: Any) -> Any:
        return _callbacks(self._pg, self._token, self._username, remote.url)

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

    def set_write_quiescer(
        self,
        pause_writes: Callable[[], AbstractContextManager[None]],
        drain_writes: Callable[[], bool],
    ) -> None:
        """Install the pause and drain functions for synchronized pulls."""
        self._pause, self._drain = pause_writes, drain_writes

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Fetch and reconcile the remote branch without invoking Git."""
        with self._quiesce(skip=dry_run), self._lock:
            result = self._pull(dry_run)
            if dry_run:
                return result
            if result.reason in (None, "rebased", "conflicts_resolved_with_siblings"):
                self._health.pull_succeeded()
            elif result.reason not in ("pull_disabled", "no_remote", "diverged"):
                self._health.pull_failed(result.reason, result.detail)
            self._observe_safe()
            return result

    def _observe_safe(self) -> None:
        try:
            self._observe()
        except Exception as exc:
            logger.warning("git_worktree_check_failed error=%s", type(exc).__name__)

    def _pull(self, dry_run: bool) -> PullResult:  # noqa: PLR0911, RUF100 -- each sync outcome has a distinct result.
        repo = self._repo
        before = str(repo.head.target) if repo and not repo.head_is_unborn else ""
        if not self._enable_sync:
            return PullResult.head_unchanged_failure(before, "pull_disabled")
        remote = self._origin()
        if remote is None or not before or repo is None:
            return PullResult.head_unchanged_failure(before, "no_remote")
        try:
            _probe_policy(repo)
            _extra_policy(repo)
            self._check_transport(self._pg, remote.url, self._token)
            remote.fetch(callbacks=self._callback(remote))
            tracking = self._tracking()
            if tracking is None:
                return PullResult.head_unchanged_failure(before, "no_remote")
            theirs = repo[tracking.target]
            _check_tree(repo, theirs.tree)
            ahead, behind = repo.ahead_behind(repo.head.target, theirs.id)
            if behind == 0:
                return PullResult(True, True, 0, before, before)
            if dry_run:
                return PullResult(
                    False,
                    ahead == 0,
                    behind,
                    before,
                    str(theirs.id),
                    "diverged" if ahead else None,
                )
            target, reason, siblings = self._merge(repo, theirs, ahead)
            self._apply(repo, before, target)
            return PullResult(
                True,
                ahead == 0,
                behind if not ahead else 0,
                before,
                str(target),
                reason,
                tuple(siblings),
            )
        except Exception as exc:
            detail = _safe(str(exc), self._token)
            return PullResult.head_unchanged_failure(
                before,
                "fetch_failed"
                if "tracking" not in locals()
                else "conflict_resolution_failed",
                detail,
            )

    def _apply(self, repo: Any, before: str, target: Any) -> None:
        with repo.transaction() as transaction:
            transaction.lock_ref("HEAD")
            head = repo.lookup_reference("HEAD")
            name = head.target if isinstance(head.target, str) else "HEAD"
            if name != "HEAD":
                transaction.lock_ref(name)
            if repo.state() or str(repo.head.target) != before:
                raise RuntimeError(
                    "HEAD changed or another Git operation is in progress"
                )
            repo.checkout_tree(
                repo[target],
                strategy=self._pg.GIT_CHECKOUT_SAFE
                | self._pg.GIT_CHECKOUT_DONT_OVERWRITE_IGNORED,
            )
            transaction.set_target(name, target, message="synchronize remote")

    def _merge(
        self, repo: Any, theirs: Any, ahead: int
    ) -> tuple[Any, str | None, list[str]]:
        if not ahead:
            return theirs.id, None, []
        ours = repo.head.peel(self._pg.Commit)
        base = repo[repo.merge_base(ours.id, theirs.id)]
        index = repo.merge_trees(base.tree, ours.tree, theirs.tree)
        siblings = self._resolve_conflicts(repo, index)
        tree = index.write_tree(repo)
        signature = self._pg.Signature(self._name, self._email)
        reason = "conflicts_resolved_with_siblings" if siblings else "rebased"
        subject = (
            f"conflict: saved {len(siblings)} MCP version(s) for manual reconciliation"
            if siblings
            else "Merge remote changes"
        )
        target = repo.create_commit(
            None, signature, signature, subject, tree, [ours.id, theirs.id]
        )
        return target, reason, siblings

    def _resolve_conflicts(self, repo: Any, index: Any) -> list[str]:
        if not index.conflicts:
            return []
        conflicts = list(index.conflicts)
        siblings: list[str] = []
        now = datetime.now(UTC)
        for ancestor, ours, theirs in conflicts:
            path = (theirs or ours or ancestor).path
            del index.conflicts[path]
            if ours is None:
                if theirs is not None:
                    index.add(theirs)
                continue
            original = Path(path)
            stamp = now.strftime("%Y%m%d-%H%M%S")
            name = original.with_name(
                f"{original.stem}.conflict-mcp-{stamp}{original.suffix}"
            )
            counter = 1
            while (
                _tree_entry(index, name.as_posix()) is not None
                or (Path(repo.workdir) / name).exists()
            ):
                name = original.with_name(
                    f"{original.stem}.conflict-mcp-{stamp}-{counter}{original.suffix}"
                )
                counter += 1
            date = now.isoformat(timespec="seconds")
            vault = self._sync_path.resolve()
            original_relative = (
                (Path(repo.workdir) / original).resolve().relative_to(vault).as_posix()
            )
            sibling_relative = (
                (Path(repo.workdir) / name).resolve().relative_to(vault).as_posix()
            )
            sibling_id = repo.create_blob(
                _frontmatter(repo[ours.id].data, original_relative, date)
            )
            if theirs is not None:
                original_id = repo.create_blob(
                    _frontmatter(repo[theirs.id].data, sibling_relative, date)
                )
                index.add(self._pg.IndexEntry(path, original_id, theirs.mode))
            index.add(self._pg.IndexEntry(name.as_posix(), sibling_id, ours.mode))
            siblings.append(sibling_relative)
        return siblings

    def _has_unpushed(self, _path: Path) -> bool:
        tracking = self._tracking()
        repo = self._repo
        return bool(
            repo
            and tracking
            and repo.ahead_behind(repo.head.target, tracking.target)[0]
        )

    def _push(self, _path: Path) -> None:
        remote, tracking = self._origin(), self._tracking()
        repo = self._repo
        if not repo or not remote or not tracking or repo.head_is_detached:
            raise PushBackendError("No remote branch", reason="no_remote")
        ahead, behind = repo.ahead_behind(repo.head.target, tracking.target)
        if behind:
            raise PushBackendError(
                "Remote has commits to reconcile; run git_sync(direction='pull') first.",
                reason="non_fast_forward",
            )
        if not ahead:
            return
        try:
            _probe_policy(repo)
            _extra_policy(repo)
            self._check_transport(self._pg, remote.push_url or remote.url, self._token)
            _local_remote_policy(
                self._pg, remote.push_url or remote.url, Path(repo.workdir)
            )
        except PushBackendError:
            raise
        except Exception as exc:
            raise PushBackendError(
                _safe(str(exc), self._token), reason="push_failed"
            ) from None
        callback = self._callback(remote)
        branch = repo.head.shorthand
        try:
            remote.push(
                [f"refs/heads/{branch}:refs/heads/{branch}"], callbacks=callback
            )
            if callback.rejection:
                raise ValueError(callback.rejection)
        except Exception as exc:
            detail = _safe(str(exc), self._token)
            reason = "push_failed"
            try:
                remote.fetch(callbacks=self._callback(remote))
                refreshed = self._tracking()
                if (
                    refreshed
                    and repo.ahead_behind(repo.head.target, refreshed.target)[1]
                ):
                    reason = "non_fast_forward"
                    detail = "Remote has commits to reconcile; run git_sync(direction='pull') first."
            except Exception:
                logger.debug("git_push_rejection_probe_failed")
            raise PushBackendError(detail, reason=reason) from None

    def force_push(self, *, dry_run: bool = False) -> PushResult:
        """Push the current branch and report the tracked remote outcome."""
        with self._lock:
            tracking = self._tracking()
            repo = self._repo
            before = str(tracking.target) if tracking else ""
            if not repo or not tracking or not self._origin():
                if not dry_run:
                    self._observe_safe()
                return PushResult(False, 0, before, before, "no_remote")
            if dry_run:
                return PushResult(False, 0, before, before, "dry_run_unsupported")
            try:
                ahead, _ = repo.ahead_behind(repo.head.target, tracking.target)
                self._push(self._sync_path)
            except PushBackendError as exc:
                self._health.push_failed(exc.reason or "push_failed", str(exc))
                return PushResult(False, 0, before, before, exc.reason, str(exc))
            except Exception as exc:
                detail = _safe(str(exc), self._token)
                self._health.push_failed("push_failed", detail)
                return PushResult(False, 0, before, before, "push_failed", detail)
            finally:
                self._observe_safe()
            self._health.push_succeeded()
            return PushResult(True, ahead, before, str(repo.head.target))

    def sync_once(self, repo_path: Path) -> bool:
        """Run one pull and report whether HEAD advanced."""
        del repo_path
        if self._closed:
            return False
        result = self.force_pull()
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
                    advanced = self.sync_once(repo_path)
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
            if self._closed or self._thread or not self._tracking():
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
