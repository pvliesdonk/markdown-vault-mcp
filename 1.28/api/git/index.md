# Git Integration

The `git` module provides:

- Auto-commit + deferred push for write operations (via `on_write`)
- Periodic pull (ff-only) primitives used by the server to keep the working tree up to date

## Quick Start

```
from pathlib import Path
from markdown_vault_mcp import Collection, GitWriteStrategy

strategy = GitWriteStrategy(
    token="ghp_your_token",
    push_delay_s=30,
)

collection = Collection(
    source_dir=Path("/path/to/vault"),
    read_only=False,
    on_write=strategy,
)

# Writes are now auto-committed and pushed
collection.write("notes/new.md", "Hello world")

# Clean up on shutdown
collection.close()
```

## API Reference

## `GitWriteStrategy(token=None, username='x-access-token', repo_url=None, managed=False, enable_pull=True, enable_push=True, push_delay_s=30.0, commit_name=None, commit_email=None, git_lfs=True, repo_path=None)`

Stateful git strategy: commit per write, deferred push.

On each callback invocation:

1. Stages the changed file (`git add` or `git add -u` for deletes).
1. Commits with an auto-generated message (`"operation: path"`).
1. Resets the push timer — push fires after `push_delay_s` of idle.

Push is deferred to a background `threading.Timer` that resets on each write. When the timer fires (no writes for `push_delay_s`), all accumulated local commits are pushed in a single `git push`.

On startup, any unpushed local commits (from a previous crash) are pushed immediately.

Parameters:

| Name           | Type    | Description                                                                                                                                                                                                           | Default                                                                                                                                                                                                    |
| -------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `token`        | \`str   | None\`                                                                                                                                                                                                                | PAT for HTTPS push via GIT_ASKPASS. None uses SSH or pre-configured credentials.                                                                                                                           |
| `username`     | `str`   | Username used with token auth. Defaults to "x-access-token" (GitHub-compatible).                                                                                                                                      | `'x-access-token'`                                                                                                                                                                                         |
| `repo_url`     | \`str   | None\`                                                                                                                                                                                                                | Remote URL expected in managed mode.                                                                                                                                                                       |
| `managed`      | `bool`  | When True, ensure the repo exists under repo_path: clone into an empty directory or validate origin on existing repos.                                                                                                | `False`                                                                                                                                                                                                    |
| `enable_pull`  | `bool`  | Enable fetch + ff-only sync methods.                                                                                                                                                                                  | `True`                                                                                                                                                                                                     |
| `enable_push`  | `bool`  | Enable deferred push behavior.                                                                                                                                                                                        | `True`                                                                                                                                                                                                     |
| `push_delay_s` | `float` | Seconds of idle before pushing. 0 disables the timer (push only on :meth:close).                                                                                                                                      | `30.0`                                                                                                                                                                                                     |
| `commit_name`  | \`str   | None\`                                                                                                                                                                                                                | Git committer name; defaults to :attr:DEFAULT_COMMIT_NAME.                                                                                                                                                 |
| `commit_email` | \`str   | None\`                                                                                                                                                                                                                | Git committer email; defaults to :attr:DEFAULT_COMMIT_EMAIL.                                                                                                                                               |
| `git_lfs`      | `bool`  | When True (default), run git lfs pull during lazy initialisation so LFS pointers are resolved before the first write is committed. Requires git-lfs to be on PATH; failures are logged at ERROR and never propagated. | `True`                                                                                                                                                                                                     |
| `repo_path`    | \`Path  | None\`                                                                                                                                                                                                                | Optional repository path used for startup validation. When set together with token, startup raises :class:~markdown_vault_mcp.exceptions.ConfigurationError if origin uses SSH transport instead of HTTPS. |

Example::

```
strategy = GitWriteStrategy(token="ghp_...", push_delay_s=30)
collection = Collection(on_write=strategy, ...)
# ... writes happen, push deferred ...
strategy.close()  # final flush
```

### `validate_startup(repo_path)`

Validate startup git settings for token-authenticated workflows.

### `__call__(path, content, operation)`

WriteCallback interface: stage + commit, then schedule push.

### `sync_once(repo_path)`

Fetch and update once, returning True if HEAD advanced.

Tries fast-forward first; falls back to rebase when the local and upstream branches have diverged (e.g. Obsidian and MCP both committed on different files). Aborts on true conflicts.

### `start(*, repo_path, pull_interval_s, pause_writes=None, on_pull=None)`

Start a periodic fetch + ff-only update loop in a daemon thread.

### `stop()`

Stop the pull loop thread if it is running.

### `flush()`

Block until any pending push completes.

Cancels the idle timer and pushes immediately if there are pending local commits.

### `close()`

Cancel timer, flush pending push, mark strategy as closed.

### `get_file_history(repo_path, path, since, limit, until=None)`

Return commits that touched *path* (or the whole vault).

Parameters:

| Name        | Type   | Description                                               | Default                                                                                                                                                                                                                                                                                 |
| ----------- | ------ | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo_path` | `Path` | Path inside the git repository (used to locate the root). | *required*                                                                                                                                                                                                                                                                              |
| `path`      | \`Path | None\`                                                    | Absolute path of the file to filter on, or None for the entire vault.                                                                                                                                                                                                                   |
| `since`     | \`str  | None\`                                                    | Passed as --since to git log (ISO 8601 or git date expression such as "1 week ago"). None disables the filter.                                                                                                                                                                          |
| `limit`     | `int`  | Maximum number of commits to return (capped at 100).      | *required*                                                                                                                                                                                                                                                                              |
| `until`     | \`str  | None\`                                                    | Passed as --until to git log (same format as since). None disables the filter. When both since and until are given the window is bounded on both sides, inclusive at both endpoints (git's --since / --until semantics: a commit whose author date equals either boundary is included). |

Returns:

| Type                 | Description                                                |
| -------------------- | ---------------------------------------------------------- |
| `list[HistoryEntry]` | List of :class:HistoryEntry ordered from newest to oldest. |

Raises:

| Type         | Description                                                           |
| ------------ | --------------------------------------------------------------------- |
| `ValueError` | If git log exits non-zero (e.g. an invalid since / until expression). |

### `get_file_diff(repo_path, path, ref, per_commit, since_timestamp=None, limit=None)`

Return a unified diff of *path* from *ref* to HEAD.

Exactly one of *ref* or *since_timestamp* must be supplied. When *since_timestamp* is given, it is resolved to the most recent commit at or before that instant via `git rev-list` (boundary inclusive: a commit whose author date equals *since_timestamp* is selected).

Parameters:

| Name              | Type   | Description                                                                                                      | Default                                                                                                                                                                                                                 |
| ----------------- | ------ | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo_path`       | `Path` | Path inside the git repository.                                                                                  | *required*                                                                                                                                                                                                              |
| `path`            | `Path` | Absolute path of the file to diff.                                                                               | *required*                                                                                                                                                                                                              |
| `ref`             | \`str  | None\`                                                                                                           | The git ref (SHA or expression) to diff from. Mutually exclusive with since_timestamp.                                                                                                                                  |
| `per_commit`      | `bool` | When False, return a single unified diff string. When True, return one :class:CommitDiff per intervening commit. | *required*                                                                                                                                                                                                              |
| `since_timestamp` | \`str  | None\`                                                                                                           | ISO 8601 datetime string resolved to a commit SHA via git rev-list --before. Mutually exclusive with ref.                                                                                                               |
| `limit`           | \`int  | None\`                                                                                                           | When per_commit is True, cap the number of commits walked to the limit most recent ones (clamped to [1, 100]). Ignored when per_commit is False. None means unbounded (still capped by the underlying ref..HEAD range). |

Returns:

| Type  | Description        |
| ----- | ------------------ |
| \`str | list[CommitDiff]\` |
| \`str | list[CommitDiff]\` |

Raises:

| Type         | Description                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------- |
| `ValueError` | If ref is not found in history, since_timestamp cannot be resolved, or a git subprocess exits non-zero. |

## `git_write_strategy(token=None, push_delay_s=0, git_lfs=True)`

Create a :class:`GitWriteStrategy` callback.

Convenience wrapper around :class:`GitWriteStrategy`. With the default `push_delay_s=0`, commits happen per-write but push only fires when :meth:`~GitWriteStrategy.close` or :meth:`~GitWriteStrategy.flush` is called.

When used via :class:`~markdown_vault_mcp.collection.Collection`, `Collection.close()` automatically calls the strategy's `close()`, so pushes flush on shutdown. Callers using this as a bare `WriteCallback` must retain a reference and call `close()` explicitly.

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
