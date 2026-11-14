---
description: "Read and change a running vault from a terminal, on another machine or inside the server's Docker container."
kind: how-to
---

# Use a vault from the terminal

The `markdown-vault-mcp` command can look things up in a vault without Claude, from a quick search to a count of every matching note. It can also change a hosted vault through the existing server tools. It reads either a vault on the same machine or one that a server elsewhere publishes over MCP. The [command reference](../reference/cli.md) lists every command and option.

## From another machine

### Install the command

```bash
uv tool install markdown-vault-mcp
```

`pipx install markdown-vault-mcp` works as well. A remote lookup needs no extras, because the vault and its index stay on the server.

### Point it at the server

Create `~/.config/markdown-vault-mcp/client.toml`:

```toml
[remote]
url = "https://vault.example/mcp"
token_env = "VAULT_ACCESS_TOKEN"
```

`url` is the server's complete MCP endpoint, path included: its public address followed by `MARKDOWN_VAULT_MCP_HTTP_PATH`. `token_env` names the environment variable that holds the bearer token, so the token itself never lands in the file. Put the token there before running a command:

```bash
export VAULT_ACCESS_TOKEN="your-bearer-token"
```

The server must accept that token, as a [bearer token server](../deploy/authentication.md) does. The command can't sign in interactively; with OIDC, pass an access token you already hold. To query a different server for one command, pass `--server` with its endpoint; `--local` reads the vault on this machine instead.

### Look something up

```bash
markdown-vault-mcp search "release notes"
markdown-vault-mcp read "Projects/plan.md" --section "Next steps"
markdown-vault-mcp links to "Projects/plan.md"
markdown-vault-mcp query count "release" --scope body
markdown-vault-mcp query aggregate --by tag
markdown-vault-mcp query path "relase plan" --fuzzy
```

Output is plain text by default. Add `--json` for the server's structured result, for scripts, or `--agent` for one escaped record per line. A remote lookup never falls back to a local vault. If the server can't answer, the command says why and exits 1.

## Change the hosted vault

The writer commands require a remote endpoint, selected as above. Each leaf accepts `--server`, `--timeout`, `--json` or `--agent`; JSON and agent output cannot be combined. Writers accept no local vault or index options. Server permissions and the [security model](../security-model.md) apply.

### Replace, create or edit a note

```bash
markdown-vault-mcp note write team/status.md 'Ready for review'
markdown-vault-mcp note write team/new.md 'First draft' --create --frontmatter '{"tags": ["team"]}'
markdown-vault-mcp note edit team/status.md --old-text Ready --new-text Approved
markdown-vault-mcp note edit team/status.md --line-start 1 --line-end 2 --new-text ''
```

`CONTENT`, `--old-text` and `--new-text` are literal text. Empty write content and an empty replacement are supported; the server rejects empty append content and empty old-text selectors. The command does not trim text or read files or stdin. Frontmatter must be a JSON object.

By default, `note write` replaces an existing note: it reads the whole live note, then passes its etag to `write`. Every `note edit` does the same. Supply a nonempty `--if-match ETAG` to skip that read. A missing note, a read limit, an unusable etag or another read failure stops the operation before mutation. Edit forwards an old-text selector, an inclusive one-based line pair, or both together; the server validates selector completeness, bounds and matching. An empty replacement deletes the selection.

`--create` skips the read and sends a native write without an etag. It conflicts with `--if-match`. This is not an atomic create-if-absent guarantee: default server overwrite protection refuses an existing note, but a server allowing blind overwrites can replace it.

### Other hosted changes

```bash
markdown-vault-mcp note append team/status.md 'Next action' --if-match abc123
markdown-vault-mcp note append team/new.md 'First entry' --create-if-missing
markdown-vault-mcp note delete team/old.md --if-match abc123
markdown-vault-mcp note rename team/status.md team/ready.md
markdown-vault-mcp folder move archive/old archive/new
markdown-vault-mcp fetch https://example.com/article inbox/article.md --download-timeout 20
markdown-vault-mcp okf generate index --folder team
markdown-vault-mcp okf seed log
markdown-vault-mcp git sync --direction both --dry-run
```

Append, delete, rename and fetch accept optional `--if-match` values and keep the server's policy when omitted. Rename always updates note links. Fetch downloads on the server; its `--download-timeout` bounds that download, while `--timeout` bounds the complete CLI operation. Git direction is `pull`, `push` or `both`.

Commands discover the tools once and fail naming an unavailable tool, including on a read-only server. A stale etag returns the server's conflict. There is no mutation retry or local fallback. After a timeout or interrupt, the result may be unknown; check the hosted vault before trying again. Plain and agent output escape terminal controls; `--json` retains native result data.

`okf verify PATH` always exits 1 before resolving credentials or connecting. This CLI cannot ask for human review confirmation; use an MCP client that supports elicitation. It cannot record human review, even when the server trusts an authenticated subject.

### Request hosted reindexing

```bash
markdown-vault-mcp index reindex --force --json
```

The command returns the server's result without polling. `status=completed` includes its counts; `status=working`, `job_id` and `poll_with` mean the job was accepted. Writes do not trigger an extra CLI reindex or rebuild embeddings.

Local `admin index`, `admin reindex` and `admin embeddings import` keep their existing behavior. The shipped `index --source-dir DIR --index-path PATH --force` invocation still runs local indexing and prints its deprecation message. Parent local options cannot be combined with the hosted `index reindex` child. `index --help` shows help without indexing.

## Inside the server's Docker container

The image carries the same command. Run inside the container, it reads the server's own index with the container's settings, so it needs no address and no token, and it never writes to the index or the vault:

```bash
docker compose exec -u appuser markdown-vault-mcp markdown-vault-mcp search "release notes"
```

Run it as `appuser`, the user the server runs as. As root, which is what `docker exec` uses by default, a vault that is a git checkout fails with `Managed mode requires SOURCE_DIR to be empty or a git repository`, because git refuses a checkout owned by another user.

The command also prints the server's startup log lines on stderr. Set `MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING` for the call to keep the terminal to the results. An alias for a container started with `docker run --name my-vault` puts both in one word:

```bash
alias vault='docker exec -u appuser -e MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING my-vault markdown-vault-mcp'
vault search "release notes"
```

What the command can reach on either route is in the [security model](../security-model.md).
