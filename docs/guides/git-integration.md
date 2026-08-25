# Git Integration

Use this guide to choose and configure the right git mode for your deployment.

## Modes

1. **Managed** (`GIT_REPO_URL` + `GIT_TOKEN`)
   The server owns clone, periodic pull, commit, and deferred push.
2. **Unmanaged / commit-only** (no `GIT_REPO_URL`, existing git repo)
   The server stages and commits writes, but never pulls or pushes.
3. **No-git** (default)
   The vault is treated as a plain directory with no git operations.

## Managed Mode (Recommended For Containerized Deployments)

Use managed mode when the server should fully own git synchronization.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/your-org/your-vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=github_pat_xxx
MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S=600
MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S=30
```

Behavior:

- If `SOURCE_DIR` is empty at startup, the server clones `GIT_REPO_URL` into it.
- If `SOURCE_DIR` is already a git repo, the server verifies `origin` matches `GIT_REPO_URL`.
- Writes are committed and pushed after the configured idle delay.
- Periodic pull uses fast-forward-only updates.

Two mechanisms sit alongside the periodic loop, both described below: a
GitHub webhook that pulls the moment someone pushes, and the `git_sync`
tool for pulling or pushing on demand from inside a conversation.

## Push-Triggered Pull: GitHub Webhook

The periodic loop leaves reads up to `GIT_PULL_INTERVAL_S` seconds behind
the remote (default 600). In a multi-author vault, where a teammate or
another instance commits from elsewhere, that window is what the webhook
closes: GitHub delivers a `push` event, the server pulls and reindexes
straight away, and staleness drops to delivery latency, a couple of
seconds in practice.

Generate a secret and set it:

```bash
MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

Setting the secret mounts `POST /github-webhook` on the HTTP and SSE
transports. Under stdio there is no HTTP server, so nothing is mounted and
the setting has no effect. In the GitHub repository, add a webhook pointing
at `https://<your-host>/github-webhook` with content type
`application/json`, the same secret, and the `push` event selected.

Behavior:

- Every delivery's `X-Hub-Signature-256` header is verified (HMAC-SHA256,
  constant-time). An invalid or missing signature returns 401 and no git
  operation runs.
- A `push` event pulls first, then reindexes only when HEAD actually moved.
  A push to a branch the vault does not track leaves HEAD where it was, so
  it costs a fetch and nothing more.
- `ping`, GitHub's handshake delivery, answers `pong`; every other event
  returns 200 and does nothing.
- A delivery whose pull did not apply returns 503, so GitHub retries it
  instead of marking it delivered. A pull that keeps failing, such as an
  unresolved conflict, exhausts the retries and waits for the next
  periodic tick. Divergent history is not a failure: it flows through the
  Syncthing-style sibling resolution described under
  [`git_sync`](#manual-sync-git_sync-tool) below.
- A delivery to a server with no managed remote returns 200, not 503. There
  is nothing to pull and a retry cannot change that, so the delivery is
  recorded rather than retried. Each one logs a warning naming the
  problem, and the server logs the same warning once at startup.
- A delivery arriving while the initial index build is still running is
  handled, not dropped. The pull is a pure git operation and runs
  regardless of index state; only the reindex is skipped, and the boot
  reconciliation pass that follows the build picks the pulled changes up.

!!! warning "Managed mode only"
    Set the secret only where the server owns the remote. Outside managed
    mode the endpoint is inert: it verifies signatures and answers 200, but
    there is no remote to pull from, so every delivery is a no-op. The
    server says so at startup and on each delivery, because a webhook that
    is quietly doing nothing looks the same as one that is working.

    This is a no-op rather than a failure as of 4.1. Before that the pull
    path ignored the sync switch unmanaged mode turns off and ran
    `git fetch origin` anyway: a checkout with no reachable `origin`
    failed that fetch and answered 503, burning GitHub's retries on every
    push, and against a vault that was not a git repository at all the
    pull raised out of the handler.

Keep `GIT_PULL_INTERVAL_S` enabled. The webhook narrows the staleness
window; the loop is what catches the deliveries the webhook loses.

!!! note "The file watcher steps aside"
    Setting `GITHUB_WEBHOOK_SECRET` disables the filesystem watcher, the
    same way `GIT_PULL_INTERVAL_S > 0` does. Git rewrites the working tree
    during a checkout, and a watcher firing mid-checkout would scan a
    partial tree. Reindexing stays driven by the webhook and the periodic
    loop. See [File Watcher](../configuration.md#file-watcher).

The variable itself is listed in the
[configuration reference](../configuration.md#github-webhook-push-triggered-pull).

## Manual sync: `git_sync` tool

The periodic loops are time-based: pull every
`MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` seconds (default 600), push
`MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` seconds after the last write
(default 30). For workflows where the LLM needs to confirm "your changes
are now on the remote" before telling the user to check another device,
or wants to pull in remote edits *right now* before continuing the
conversation, call `git_sync` directly:

```
git_sync(direction="both")
```

Use `direction="pull"` or `direction="push"` to skip a leg. In
`direction="both"` mode the push leg only runs when the pull leg
succeeded; otherwise `push` stays `null` and the LLM should inspect
`pull.reason` (and `pull.conflict_files`) before retrying.

`dry_run=true` previews what a pull *would* do (useful for "is there
anything new on origin?") without risking an in-conversation conflict.
The push leg has no safe local "would this be accepted" probe, so a
dry-run push always returns `applied=false` with
`reason="dry_run_unsupported"`.

!!! info "Conflict outcome: Syncthing-style sibling resolution"
    When the pull would otherwise need an interactive merge, the server
    follows the [#232](https://github.com/pvliesdonk/markdown-vault-mcp/issues/232)
    Syncthing-style flow:

    - The pull **succeeds** (`pull.applied=true`,
      `pull.reason="conflicts_resolved_with_siblings"`).
    - HEAD advances to the remote tip — the canonical path now reflects
      the remote (remote wins).
    - The local versions that conflicted are preserved as
      `<basename>.conflict-mcp-<timestamp>.md` siblings on the same
      path; their vault-relative paths are listed in
      `pull.conflict_files`.
    - `pull.commits_pulled` is `0` on this path because the rebase
      replays your local commits *on top of* the remote tip — the
      counting model only reports linear-history catch-ups.

    The LLM (or a downstream agent) is expected to read the listed
    sibling(s), reconcile the local content against the remote, and
    `delete` the sibling once merged.

!!! note "Writes landing during a pull"
    A write whose deferred git commit has not yet run when a pull starts is
    never lost. Before every real (non-dry-run) pull (periodic or `git_sync`),
    the server pauses new writes and drains the deferred-commit queue (a
    `dry_run` preview only fetches and never quiesces), so in the normal case the
    just-written file is committed first and the merge runs on a clean tree
    ([#571](https://github.com/pvliesdonk/markdown-vault-mcp/issues/571)). If
    that write and the remote touched the same file, it flows through the
    Syncthing-style sibling resolution above rather than failing. The drain is
    best-effort and time-bounded: if it cannot finish in time, the pull logs a
    warning and proceeds anyway; the write is still safely on disk and is
    committed on the next opportunity, at worst reverting to the pre-#571
    behavior (a non-fast-forward push that the next reconcile resolves).

The full enumeration of `pull.reason` and `push.reason` values lives in
the [`git_sync` tool reference](../tools/index.md#git_sync).

`git_sync` is hidden when the deployment isn't in managed git mode (no
`MARKDOWN_VAULT_MCP_GIT_REPO_URL` set) or when
`MARKDOWN_VAULT_MCP_READ_ONLY=true`.

## Unmanaged / Commit-Only Mode

Use unmanaged mode when another process controls pull/push, but you still want MCP writes committed locally.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
# No GIT_REPO_URL
# No GIT_TOKEN required
MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME=markdown-vault-mcp
MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL=noreply@markdown-vault-mcp
```

Behavior:

- If `SOURCE_DIR` is a git repo, writes are committed locally.
- No periodic pull.
- No push.

## No-Git Mode

Use no-git mode when you only need file persistence.

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
# No git env vars required
```

Behavior:

- Files are written to disk.
- No staging, commits, pulls, or pushes.

## Provider Username Reference

`MARKDOWN_VAULT_MCP_GIT_USERNAME` controls the HTTPS username prompt:

- GitHub: `x-access-token`
- GitLab: `oauth2`
- Bitbucket: account username

## Git LFS

If your vault tracks large files (PDFs, images) with [Git LFS](https://git-lfs.com), the server runs `git lfs pull` on startup to resolve LFS pointers into actual file content. This is enabled by default.

Set `MARKDOWN_VAULT_MCP_GIT_LFS=false` to skip the LFS pull. Use this when:

- Your vault does not use Git LFS
- `git-lfs` is not installed in your environment
- You want faster startup and don't need LFS-tracked attachments

```bash
MARKDOWN_VAULT_MCP_GIT_LFS=false
```

## Legacy Compatibility

`GIT_TOKEN` without `GIT_REPO_URL` still works for backward compatibility and logs a deprecation warning.
