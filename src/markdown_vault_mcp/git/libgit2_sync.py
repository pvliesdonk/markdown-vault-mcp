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
from markdown_vault_mcp.git._run import _normalize_remote, redact
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.libgit2 import (
    Libgit2Store,
    _filter_guard,
    _probe_policy,
    _pygit2,
    _tree_entry,
)
from markdown_vault_mcp.git.push_scheduler import PushBackendError, PushScheduler
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_FETCH_FAILED,
    PullResult,
    PushResult,
)
from markdown_vault_mcp.scanner import parse_frontmatter
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


def _callbacks(pg: Any, token: str | None, username: str, url: str) -> Any:
    class Callbacks(pg.RemoteCallbacks):  # type: ignore[misc]
        rejection: str | None = None
        credential_attempts = 0

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
                self.credential_attempts += 1
                if self.credential_attempts > 3:
                    raise ValueError("Credential attempts exhausted")
                return pg.UserPass(username, token)
            if _ssh(url) and allowed & pg.enums.CredentialType.SSH_KEY:
                return pg.KeypairFromAgent(user or "git")
            if _ssh(url) and allowed & pg.enums.CredentialType.USERNAME:
                return pg.Username(user or "git")
            raise ValueError("No supported credential available")

        def certificate_check(
            self, _certificate: None, valid: bool, _host: bytes
        ) -> bool:
            return valid

        def push_update_reference(self, _refname: str, message: str | None) -> None:
            self.rejection = message

    return Callbacks()


def _extra_policy(repo: Any) -> None:
    hooks_path = _tree_entry(repo.config, "core.hookspath")
    gitdir = Path(repo.path)
    common = gitdir / "commondir"
    if is_regular_file(common):
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
    if not path_exists(path):
        return
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


def _check_tree(repo: Any, commit: Any, baseline: Any = None) -> None:
    """Check effective incoming attributes before checkout."""
    pg = _pygit2()
    flags = pg.enums.AttrCheck.INDEX_ONLY | pg.enums.AttrCheck.INCLUDE_COMMIT

    def walk(tree: Any, prefix: str = "") -> Iterator[str]:
        for entry in tree:
            path = f"{prefix}{entry.name}"
            obj = repo[entry.id]
            if obj.type_str == "tree":
                yield from walk(obj, f"{path}/")
            else:
                yield path

    paths = None
    if baseline is not None:
        deltas = list(repo.diff(baseline.tree, commit.tree).deltas)
        attributes_changed = any(
            Path(d.new_file.path).name == ".gitattributes"
            or Path(d.old_file.path).name == ".gitattributes"
            for d in deltas
        )
        if attributes_changed and any(
            Path(entry.path).name == ".gitattributes" for entry in repo.index
        ):
            raise ConfigurationError(
                f"incoming attribute changes cannot be safely evaluated against the existing index. {_FALLBACK}"
            )
        if not any(
            Path(d.new_file.path).name == ".gitattributes"
            or Path(d.old_file.path).name == ".gitattributes"
            for d in deltas
        ):
            paths = (
                d.new_file.path for d in deltas if d.new_file.id != pg.Oid(hex="0" * 40)
            )
    for path in paths if paths is not None else walk(commit.tree):
        encoding = repo.get_attr(path, "working-tree-encoding", flags, commit.id)
        name = repo.get_attr(path, "filter", flags, commit.id)
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
        ):
            raise ConfigurationError(
                f"incoming external filter {name!r} (including Git LFS) is unsupported. {_FALLBACK}"
            )
        _filter_guard(repo, path)


def _frontmatter(content: bytes, other: str, date: str) -> bytes:
    text = content.decode("utf-8")
    try:
        post = parse_frontmatter(text)
    except Exception:
        post = frontmatter.Post(text)
    post.metadata["conflict_with"] = other
    post.metadata["conflict_date"] = date
    return frontmatter.dumps(post).encode("utf-8")


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
            detail = _safe(str(exc), token)
            if _FALLBACK not in detail:
                detail = f"{detail} {_FALLBACK}"
            raise ConfigurationError(detail) from None
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
        if path_exists(path) and (not is_directory(path) or any(path.iterdir())):
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
        origin = self._origin()
        if self._enable_sync:
            _extra_policy(repo)
            if not repo.head_is_unborn:
                _check_tree(repo, repo.head.peel(self._pg.Commit))
            if origin:
                self._check_transport(self._pg, origin.url, self._token)
                push_url = origin.push_url or origin.url
                self._check_transport(self._pg, push_url, self._token)
                try:
                    _local_remote_policy(self._pg, push_url, Path(repo.workdir))
                except PushBackendError as exc:
                    raise ConfigurationError(str(exc)) from None
        if self._managed and (
            not origin or _normalize_remote(origin.url) != _normalize_remote(url or "")
        ):
            raise ConfigurationError(
                f"Managed origin does not match configured remote. {_FALLBACK}"
            )

    @staticmethod
    def _check_transport(pg: Any, url: str, token: str | None) -> None:
        if _ssh(url):
            raise ConfigurationError(
                f"Native SSH host key verification is unavailable. {_FALLBACK}"
            )
        if token and urlsplit(url).scheme.lower() == "http":
            raise ConfigurationError(
                f"Token authentication requires HTTPS. {_FALLBACK}"
            )
        del pg

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

    def _pull(self, dry_run: bool) -> PullResult:  # noqa: PLR0911, RUF100 -- each sync outcome has a distinct result.
        repo = self._repo
        before = str(repo.head.target) if repo and not repo.head_is_unborn else ""
        if not self._enable_sync:
            return PullResult.head_unchanged_failure(before, "pull_disabled")
        remote = self._origin()
        if remote is None or not before or repo is None:
            return PullResult.head_unchanged_failure(before, "no_remote")
        reason = PULL_REASON_FETCH_FAILED
        try:
            _probe_policy(repo)
            _extra_policy(repo)
            self._check_transport(self._pg, remote.url, self._token)
            remote.fetch(callbacks=self._callback(remote))
            tracking = self._tracking()
            if tracking is None:
                return PullResult.head_unchanged_failure(before, "no_remote")
            theirs = repo[tracking.target]
            ahead, behind = repo.ahead_behind(repo.head.target, theirs.id)
            if behind == 0:
                return PullResult(True, True, 0, before, before)
            _check_tree(repo, theirs, repo.head.peel(self._pg.Commit))
            if dry_run:
                return PullResult(
                    False,
                    ahead == 0,
                    behind,
                    before,
                    str(theirs.id),
                    "diverged" if ahead else None,
                )
            reason = PULL_REASON_CONFLICT_RESOLUTION_FAILED
            target, result_reason, siblings = self._merge(repo, theirs, ahead)
            self._apply(repo, before, target)
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
            return PullResult.head_unchanged_failure(
                before,
                reason,
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
            while _tree_entry(index, name.as_posix()) is not None or path_exists(
                Path(repo.workdir) / name
            ):
                name = original.with_name(
                    f"{original.stem}.conflict-mcp-{stamp}-{counter}{original.suffix}"
                )
                counter += 1
            date = now.isoformat(timespec="seconds")
            vault = self._sync_path.resolve()
            try:
                original_relative = (
                    (Path(repo.workdir) / original)
                    .resolve()
                    .relative_to(vault)
                    .as_posix()
                )
                sibling_relative = (
                    (Path(repo.workdir) / name).resolve().relative_to(vault).as_posix()
                )
            except ValueError:
                original_relative, sibling_relative = path, name.as_posix()
                in_vault = False
            else:
                in_vault = True
            ours_data, theirs_data = _conflict_contents(
                original,
                repo[ours.id].data,
                repo[theirs.id].data if theirs is not None else None,
                (original_relative, sibling_relative, date),
            )
            sibling_id = repo.create_blob(ours_data)
            if theirs is not None:
                original_id = repo.create_blob(theirs_data)
                index.add(self._pg.IndexEntry(path, original_id, theirs.mode))
            index.add(self._pg.IndexEntry(name.as_posix(), sibling_id, ours.mode))
            if in_vault:
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
