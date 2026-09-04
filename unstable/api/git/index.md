# Git Integration

The `git` module provides:

- Auto-commit + deferred push for write operations (via `on_write`)
- Periodic pull (ff-only) primitives used by the server to keep the working tree up to date

## Quick Start

```
from pathlib import Path
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings

strategy = GitWriteStrategy(
    token="ghp_your_token",
    push_delay_s=30,
)

vault = Vault(
    source_dir=Path("/path/to/vault"),
    settings=VaultSettings(read_only=False),
    on_write=strategy,
)

# Writes are now auto-committed and pushed
vault.writer.write("notes/new.md", "Hello world")

# Clean up on shutdown
vault.close()
```

## API Reference

## `GitWriteStrategy(token=None, username='x-access-token', repo_url=None, managed=False, enable_pull=True, enable_push=True, push_delay_s=30.0, commit_name=None, commit_email=None, commit_name_claim=None, commit_email_claim=None, git_lfs=True, repo_path=None)`

Stateful git strategy: commit per tool call, deferred push.

On each callback invocation:

1. Stages the changed file (`git add` or `git add -u` for deletes).
1. Commits with an auto-generated message (`"operation: path"`).
1. Resets the push timer — push fires after `push_delay_s` of idle.

Writes fired by one MCP tool call are grouped by the dispatcher (#1264) and arrive at :meth:`on_write_batch`, which stages every path the same scoped way and commits them once as `"<tool>: N files"`. A call that touched a single file commits under that file's own path instead, so ordinary writes read in `git log` exactly as they always have. A write with no owning tool call takes the per-write path above.

Push is deferred to a background `threading.Timer` that resets on each write. When the timer fires (no writes for `push_delay_s`), all accumulated local commits are pushed in a single `git push`.

On startup, any unpushed local commits (from a previous crash) are pushed immediately.

Parameters:

| Name                 | Type    | Description                                                                                                                                                                                                           | Default                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `token`              | \`str   | None\`                                                                                                                                                                                                                | PAT for HTTPS push via GIT_ASKPASS. None uses SSH or pre-configured credentials.                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `username`           | `str`   | Username used with token auth. Defaults to "x-access-token" (GitHub-compatible).                                                                                                                                      | `'x-access-token'`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `repo_url`           | \`str   | None\`                                                                                                                                                                                                                | Remote URL expected in managed mode.                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `managed`            | `bool`  | When True, ensure the repo exists under repo_path: clone into an empty directory or validate origin on existing repos.                                                                                                | `False`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `enable_pull`        | `bool`  | Enable fetch + ff-only sync methods.                                                                                                                                                                                  | `True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `enable_push`        | `bool`  | Enable deferred push behavior.                                                                                                                                                                                        | `True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `push_delay_s`       | `float` | Seconds of idle before pushing. 0 disables the timer (push only on :meth:close).                                                                                                                                      | `30.0`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `commit_name`        | \`str   | None\`                                                                                                                                                                                                                | Git committer name; defaults to :attr:DEFAULT_COMMIT_NAME.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `commit_email`       | \`str   | None\`                                                                                                                                                                                                                | Git committer email; defaults to :attr:DEFAULT_COMMIT_EMAIL.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `commit_name_claim`  | \`str   | None\`                                                                                                                                                                                                                | OIDC claim key configured for the author name.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `commit_email_claim` | \`str   | None\`                                                                                                                                                                                                                | OIDC claim key configured for the author email. .. deprecated:: The two claim kwargs no longer drive claim extraction — the strategy runs on the write-callback dispatcher thread, where no request token exists (#1218). Claims are now resolved at the MCP tool edge into the principal passed per invocation (register the keys via :func:markdown_vault_mcp.\_identity.configure_identity_claims). They remain accepted, and still inform the startup identity warning (:meth:\_check_identity). |
| `git_lfs`            | `bool`  | When True (default), run git lfs pull during lazy initialisation so LFS pointers are resolved before the first write is committed. Requires git-lfs to be on PATH; failures are logged at ERROR and never propagated. | `True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `repo_path`          | \`Path  | None\`                                                                                                                                                                                                                | Optional repository path used for startup validation. When set together with token, startup raises :class:~markdown_vault_mcp.exceptions.ConfigurationError if origin uses SSH transport instead of HTTPS.                                                                                                                                                                                                                                                                                           |

Example::

```
strategy = GitWriteStrategy(token="ghp_...", push_delay_s=30)
vault = Vault(on_write=strategy, ...)
# ... writes happen, push deferred ...
strategy.close()  # final flush
```

### `is_managed`

Whether this strategy owns a managed clone of a remote repository.

Part of the :class:`~markdown_vault_mcp.git.interfaces.Syncer` seam (#1229): the `git_sync` tool gates on it, and did so by reading the private attribute before the promotion.

### `validate_startup(repo_path)`

Validate startup git settings for token-authenticated workflows.

### `on_write_batch(items, tool_name)`

Commit every write from one tool call as a single commit.

Mirrors :meth:`__call__`'s guards and push scheduling; only the commit boundary differs, and for a call that wrote a single file not even that — see :func:`_stage_and_commit_batch`. The author identity is taken from the first item's principal: one tool call has one acting principal, so the items cannot legitimately disagree.

Parameters:

| Name        | Type                       | Description                                           | Default    |
| ----------- | -------------------------- | ----------------------------------------------------- | ---------- |
| `items`     | `Sequence[WriteBatchItem]` | The tool call's writes, in the order they were fired. | *required* |
| `tool_name` | `str`                      | The MCP tool that produced them.                      | *required* |

### `__call__(path, content, operation, *, old_path=None, principal=None)`

WriteCallback interface: stage + commit, then schedule push.

Parameters:

| Name        | Type             | Description                                                                           | Default                                                                                                                                                                                                                                                 |
| ----------- | ---------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`      | `Path`           | Absolute path of the file the operation landed on. For a rename this is the new path. | *required*                                                                                                                                                                                                                                              |
| `content`   | `str`            | File content at write time; unused here, since staging reads the working tree.        | *required*                                                                                                                                                                                                                                              |
| `operation` | `WriteOperation` | The kind of write that occurred.                                                      | *required*                                                                                                                                                                                                                                              |
| `old_path`  | \`Path           | None\`                                                                                | For a rename, the absolute path the file moved from, so staging can be scoped to it and path (#894).                                                                                                                                                    |
| `principal` | \`Principal      | None\`                                                                                | The identity performing the write, resolved at the MCP tool edge and snapshotted through the dispatcher queue (#1160). Its display name / email become the commit's --author; None fields (or no principal) fall back to the static committer identity. |

### `resolve_force_repo()`

Return the working tree path used by `force_*` methods.

Returns:

| Type   | Description                  |
| ------ | ---------------------------- |
| `Path` | The configured working tree. |

Raises:

| Type           | Description                                                                                                                                                                 |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeError` | When no repo_path was configured at construction time. The force\_\* methods require an explicit working tree because they cannot infer one from a per-write callback path. |

### `head_sha(git_root)`

Return the current HEAD SHA of *git_root*.

Parameters:

| Name       | Type   | Description               | Default    |
| ---------- | ------ | ------------------------- | ---------- |
| `git_root` | `Path` | The working tree to read. | *required* |

Returns:

| Type  | Description        |
| ----- | ------------------ |
| `str` | The full HEAD SHA. |

### `branch_name(git_root)`

Return the checked-out branch name of *git_root*.

A detached HEAD yields `"HEAD"` from git itself. Failures to invoke git at all propagate; the caller decides whether a fallback is appropriate.

Parameters:

| Name       | Type   | Description               | Default    |
| ---------- | ------ | ------------------------- | ---------- |
| `git_root` | `Path` | The working tree to read. | *required* |

Returns:

| Type  | Description      |
| ----- | ---------------- |
| `str` | The branch name. |

### `force_pull(*, dry_run=False)`

Pull from `origin` synchronously and return a structured result.

The remote-tracking branch is resolved as `origin/<current-branch>` (see :meth:`_tracking_ref`) so this method works even when branch tracking (`@{upstream}`) was never configured on the local clone — falling back to `origin/HEAD` for a detached checkout.

Acquires :attr:`_lock` for the duration so the periodic pull loop and the per-write commit path cannot race against the fetch / merge / rebase pipeline. This blocks writes for the network round-trip; that is acceptable for the interactive `git_sync` tool and mirrors what :meth:`sync_once` already does.

Before the merge it self-quiesces via :meth:`_quiesce_writes`: new writes are paused and the deferred-commit queue is drained (best-effort, time-bounded) so a write that landed just before the pull is committed first and the merge runs on a clean tree (#571). Skipped under `dry_run` (which only fetches and never touches the working tree).

Divergent history is detected before the merge (`git merge-base --is-ancestor`, #1292) and goes straight to the same rebase + Syncthing-style sibling write path used by :meth:`sync_once`; a working tree that refuses an otherwise available fast-forward falls through to it too (see :func:`~markdown_vault_mcp.git.conflict.resolve_rebase_conflicts` and :func:`~markdown_vault_mcp.git.conflict.write_conflict_files`). When the conflict-resolution path produces sibling files HEAD has advanced to the remote and :attr:`PullResult.applied` is `True` with :attr:`PullResult.reason` set to `"conflicts_resolved_with_siblings"`.

After a successful HEAD advance — fast-forward or sibling resolution — :meth:`_lfs_pull` runs so any LFS pointers in the new commits are materialised before the caller sees the working tree.

A strategy built without remote sync — unmanaged / commit-only mode, where `enable_pull` is `False` — has no remote to pull from, so this returns `applied=False` with reason `"pull_disabled"` **before running any git command** (#1128). Without that gate the pipeline ran `git fetch origin` on a remoteless checkout (answering `"fetch_failed"`, which reads as retryable) and raised `CalledProcessError` out of `head_sha` on a vault that is not a git repository at all. `enable_pull` gated only the periodic loop before; it now gates every pull.

Parameters:

| Name      | Type   | Description                                                                                                                                                                                                                                                                                | Default |
| --------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- |
| `dry_run` | `bool` | When True, runs git fetch and computes the would-be pull without modifying HEAD. A projection that predicts work returns applied=False with commits_pulled set to the count that would have been pulled; one that finds nothing to pull returns the same applied=True the real call would. | `False` |

Returns:

| Type         | Description                                        |
| ------------ | -------------------------------------------------- |
| `PullResult` | class:PullResult describing the operation. See the |
| `PullResult` | reason field for the full enumeration of outcomes. |

Raises:

| Type           | Description                                          |
| -------------- | ---------------------------------------------------- |
| `RuntimeError` | When the strategy was constructed without repo_path. |

### `force_push(*, dry_run=False)`

Push local commits to `origin`, recording what the remote said.

Holds the strategy-wide lock across both the push (:meth:`_push_locked`) and the recording of its outcome, so the interactive push feeds the same sync-health tracker as the deferred one (#1287) and cannot publish out of order against it (PR #1300). The two observe the same remote, so a caller warned about stranded writes stops being warned as soon as either of them lands.

Parameters:

| Name      | Type   | Description              | Default |
| --------- | ------ | ------------------------ | ------- |
| `dry_run` | `bool` | See :meth:\_push_locked. | `False` |

Returns:

| Name  | Type         | Description                                    |
| ----- | ------------ | ---------------------------------------------- |
| `The` | `PushResult` | class:PushResult the push produced, unchanged. |

Raises:

| Type           | Description                                          |
| -------------- | ---------------------------------------------------- |
| `RuntimeError` | When the strategy was constructed without repo_path. |

### `sync_health()`

Report whether this clone is known not to be reaching its remote.

Read without taking the strategy-wide lock, so a write response never blocks behind an in-flight pull.

Returns:

| Name | Type         | Description |
| ---- | ------------ | ----------- |
| `A`  | \`SyncHealth | None\`      |
|      | \`SyncHealth | None\`      |
|      | \`SyncHealth | None\`      |
|      | \`SyncHealth | None\`      |

### `sync_once(repo_path)`

Fetch and update once, returning True if HEAD advanced.

Thin adapter over :meth:`_pull_pipeline` (#879) — the periodic pull loop and the interactive `git_sync` tool now share one fetch → classify → ff-only → rebase → sibling implementation, so the loop gets the pipeline's safe conflict handling: defensive rebase abort and an upstream restore that drops paths whose restore failed instead of committing stale local content over them.

The pipeline self-quiesces before the merge via :meth:`_quiesce_writes` (pause new writes + drain the deferred-commit queue, best-effort/time-bounded) so a write racing the periodic pull is committed first and the merge runs on a clean tree (#571). The pause is held for the whole fetch + merge — including the network round-trip — so MCP writes block for the pull's duration; acceptable for a periodic background pull (default every 600 s) and a fast fetch.

### `set_write_quiescer(pause_writes, drain_writes)`

Wire the write-quiescing callables used before a pull (#571).

Called once by the owner (`Vault`) after the write-callback dispatcher exists, so both the interactive `force_pull` and the periodic `sync_once` can pause new writes and drain pending commits before the merge — independent of whether the periodic pull loop is started.

Parameters:

| Name           | Type                                         | Description                                                                                                                                                                                             | Default    |
| -------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| `pause_writes` | `Callable[[], AbstractContextManager[None]]` | Context manager that blocks new file mutations while held (acquires the shared file-write lock).                                                                                                        | *required* |
| `drain_writes` | `Callable[[], bool]`                         | Blocks until all already-queued write callbacks have been committed; returns True when the queue drained (or there was nothing to drain), False if it did not finish or the dispatcher worker has died. | *required* |

### `start(*, repo_path, pull_interval_s, on_pull=None)`

Start a periodic fetch + ff-only update loop in a daemon thread.

### `stop()`

Stop the pull loop thread if it is running.

### `flush()`

Block until any pending push completes.

Cancels the idle timer and pushes immediately if there are pending local commits. Thin delegation to :meth:`PushScheduler.flush`, which owns the timer/pending-flag mechanics (#893).

### `close()`

Cancel timer, flush pending push, mark strategy as closed.

Sequencing: mark closed first (new writes become no-ops), stop the periodic pull thread, then flush the push scheduler so the final push happens with no pull tick racing it.

### `get_file_history(repo_path, path, since, limit, until=None, *, is_dir=False)`

Return commits that touched *path* (or the whole vault).

Parameters:

| Name        | Type   | Description                                                                                                            | Default                                                                                                                                                                                                                                                                                    |
| ----------- | ------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `repo_path` | `Path` | Path inside the git repository (used to locate the root).                                                              | *required*                                                                                                                                                                                                                                                                                 |
| `path`      | \`Path | None\`                                                                                                                 | Absolute path of the file (or directory, when is_dir) to filter on, or None for the entire vault.                                                                                                                                                                                          |
| `is_dir`    | `bool` | When True, scope history to path's subtree instead of treating it as a single file (see :func:query.get_file_history). | `False`                                                                                                                                                                                                                                                                                    |
| `since`     | \`str  | None\`                                                                                                                 | Passed as --since to git log (ISO 8601 or git date expression such as "1 week ago"). None disables the filter.                                                                                                                                                                             |
| `limit`     | `int`  | Maximum number of commits to return (capped at 100).                                                                   | *required*                                                                                                                                                                                                                                                                                 |
| `until`     | \`str  | None\`                                                                                                                 | Passed as --until to git log (same format as since). None disables the filter. When both since and until are given the window is bounded on both sides, inclusive at both endpoints (git's --since / --until semantics: a commit whose committer date equals either boundary is included). |

Returns:

| Type                 | Description                                                |
| -------------------- | ---------------------------------------------------------- |
| `list[HistoryEntry]` | List of :class:HistoryEntry ordered from newest to oldest. |

Raises:

| Type         | Description                                                           |
| ------------ | --------------------------------------------------------------------- |
| `ValueError` | If git log exits non-zero (e.g. an invalid since / until expression). |

### `get_file_diff(repo_path, path, ref, per_commit, since_timestamp=None, limit=None, *, summarize_binary=False)`

Return a unified diff of *path* from *ref* to HEAD.

Exactly one of *ref* or *since_timestamp* must be supplied. When *since_timestamp* is given, it is resolved via `git rev-list --before=<ts> -1 HEAD` to the most recent commit at or before that instant. Boundary is **inclusive**: a commit whose committer date equals *since_timestamp* IS the resolved ref.

Parameters:

| Name               | Type   | Description                                                                                                      | Default                                                                                                                                                                                                                 |
| ------------------ | ------ | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo_path`        | `Path` | Path inside the git repository.                                                                                  | *required*                                                                                                                                                                                                              |
| `path`             | `Path` | Absolute path of the file to diff.                                                                               | *required*                                                                                                                                                                                                              |
| `ref`              | \`str  | None\`                                                                                                           | The git ref (SHA or expression) to diff from. Mutually exclusive with since_timestamp.                                                                                                                                  |
| `per_commit`       | `bool` | When False, return a single unified diff string. When True, return one :class:CommitDiff per intervening commit. | *required*                                                                                                                                                                                                              |
| `since_timestamp`  | \`str  | None\`                                                                                                           | ISO 8601 datetime string resolved to a commit SHA via git rev-list --before. Mutually exclusive with ref.                                                                                                               |
| `limit`            | \`int  | None\`                                                                                                           | When per_commit is True, cap the number of commits walked to the limit most recent ones (clamped to [1, 100]). Ignored when per_commit is False. None means unbounded (still capped by the underlying ref..HEAD range). |
| `summarize_binary` | `bool` | When True and the file is binary, return a --stat summary instead of a patch (#342).                             | `False`                                                                                                                                                                                                                 |

Returns:

| Type  | Description        |
| ----- | ------------------ |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |

Raises:

| Type         | Description                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------- |
| `ValueError` | If ref is not found in history, since_timestamp cannot be resolved, or a git subprocess exits non-zero. |

### `get_file_at_ref(query_)`

Return a note's content as it stood at a revision (#1137).

Resolution is by note identity, walked out of git's own add and rename records; where those records do not reach the revision asked for, this raises rather than returning another note's content.

Parameters:

| Name     | Type            | Description                                       | Default    |
| -------- | --------------- | ------------------------------------------------- | ---------- |
| `query_` | `RevisionQuery` | The note, revision, and read cap being asked for. | *required* |

Returns:

| Type              | Description                                                 |
| ----------------- | ----------------------------------------------------------- |
| `RevisionContent` | The content and the path the note carried at that revision. |

Raises:

| Type         | Description                                                                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ValueError` | If the vault is not git-backed, the revision is not an ancestor of HEAD, the note's identity cannot be traced to it, or the content is unreadable as a note. |

### `committed_revision(repo_path, path)`

Return the commit whose stored content for *path* is what is on disk.

Parameters:

| Name        | Type   | Description                     | Default    |
| ----------- | ------ | ------------------------------- | ---------- |
| `repo_path` | `Path` | Path inside the git repository. | *required* |
| `path`      | `Path` | Absolute path of the note.      | *required* |

Returns:

| Type  | Description |
| ----- | ----------- |
| \`str | None\`      |
| \`str | None\`      |
| \`str | None\`      |

## `git_write_strategy(token=None, push_delay_s=0, git_lfs=True)`

Create a :class:`GitWriteStrategy` callback.

Convenience wrapper around :class:`GitWriteStrategy`. With the default `push_delay_s=0`, commits happen per-write but push only fires when :meth:`~GitWriteStrategy.close` or :meth:`~GitWriteStrategy.flush` is called.

When used via :class:`~markdown_vault_mcp.vault.Vault`, `Vault.close()` automatically calls the strategy's `close()`, so pushes flush on shutdown. Callers using this as a bare `WriteCallback` must retain a reference and call `close()` explicitly.

.. deprecated:: Prefer :class:`GitWriteStrategy` directly for access to :meth:`~GitWriteStrategy.flush` and :meth:`~GitWriteStrategy.close`.

.. note:: The default `push_delay_s=0` here differs from :class:`GitWriteStrategy`'s default of `30.0`. This preserves backward compatibility (push on close/flush only).

Parameters:

| Name           | Type    | Description                                             | Default             |
| -------------- | ------- | ------------------------------------------------------- | ------------------- |
| `token`        | \`str   | None\`                                                  | PAT for HTTPS push. |
| `push_delay_s` | `float` | Push delay in seconds (default 0 = push on close only). | `0`                 |
| `git_lfs`      | `bool`  | When True (default), run git lfs pull during init.      | `True`              |

Returns:

| Name | Type               | Description                                     |
| ---- | ------------------ | ----------------------------------------------- |
| `A`  | `GitWriteStrategy` | class:GitWriteStrategy instance (also satisfies |
|      | `GitWriteStrategy` | data:~markdown_vault_mcp.types.WriteCallback).  |
