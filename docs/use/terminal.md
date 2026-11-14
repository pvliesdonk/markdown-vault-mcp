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

`CONTENT`, `--old-text` and `--new-text` are literal text. Empty write content and an empty replacement are supported; the server rejects empty append content and empty old-text selectors. The command does not trim text or read files or standard input. Frontmatter must be a JSON object with finite numeric values.

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

Commands discover the tools once and fail naming an unavailable tool, including on a read-only server. A stale etag returns the server's conflict. Commands never retry a mutation or fall back locally. After a timeout or interrupt, the result may be unknown; check the hosted vault before trying again. Plain and agent output escape terminal controls; `--json` retains native result data.

`okf verify PATH` always exits 1 before resolving credentials or connecting. This CLI cannot ask for human review confirmation; use an MCP client that supports elicitation. It cannot record human review, even when the server trusts an authenticated subject.

### Request hosted reindexing

```bash
markdown-vault-mcp index reindex --force --json
```

The command returns the server's result without polling. A slow reindex returns an accepted job while work continues on the server. `status=completed` includes its counts; `status=working`, `job_id` and `poll_with` mean the job was accepted. Writes do not trigger an extra CLI reindex or rebuild embeddings.

Local `admin index`, `admin reindex` and `admin embeddings import` keep their existing behavior. The shipped `index --source-dir DIR --index-path PATH --force` invocation still runs local indexing and prints its deprecation message. Parent local options cannot be combined with the hosted `index reindex` child. `index --help` shows help without indexing.

## Error diagnostics

Reader and hosted writer commands send errors to stderr. Plain output starts with `ERROR:` and may include a second `hint:` line. `--agent` produces `ERR <command> <kind> code=<reason>: <message>` and an optional `Hint:` line. Both escape terminal controls. `--json` produces one JSON error object:

```json
{"error":{"kind":"usage","code":"invalid_server_url","message":"Server URL must be HTTP(S) with a host and no credentials, query or fragment.","exit_code":2,"hint":"Set --server, MARKDOWN_VAULT_MCP_SERVER_URL or [remote] url to the complete HTTP(S) MCP endpoint."}}
```

`error.code` is a stable symbolic reason; `exit_code` is the numeric process status. An absent or empty hint is omitted. Categories are `usage`, `config`, `remote`, `timeout`, `connection`, `operation` and `internal`. Scripts can select a recovery path by code without parsing the message. Success JSON retains the native arrays, objects and receipts.

| Code | Meaning and recovery |
| --- | --- |
| `client_config_missing` | The explicitly selected client file is absent; correct `MARKDOWN_VAULT_MCP_CLIENT_CONFIG`. |
| `invalid_client_config` | Client TOML cannot be read or has unsupported fields or values; check syntax, permissions and `[remote]`. |
| `invalid_timeout` | Use positive finite values for `--timeout` and `[remote] timeout_seconds`. |
| `invalid_server_url` | Set a complete HTTP(S) endpoint without credentials, query or fragment. |
| `invalid_client_token` | Correct the selected bearer token environment variable; line breaks and blank tokens are refused. |
| `conflicting_options` | Choose compatible backend, source, output or creation options. |
| `invalid_request` | Correct the request using the command's `--help`; exact queries require keyword mode. |
| `configuration_error` | Check the local vault configuration and required settings. |
| `operation_failed` | A typed library operation failed; check vault state and server logs. |
| `invalid_payload` | A read result is malformed; check result format and compatibility. |
| `query_failed` | An unexpected exact-query fault occurred; check the index and logs. |
| `remote_client_unavailable` | Install the declared client dependency with `uv pip install "markdown-vault-mcp[mcp]"`. |
| `server_url_required` | Select a remote endpoint with `--server` or the client profile. |
| `remote_tool_unsupported` | Upgrade the server or choose an available operation. |
| `remote_options_unsupported` | Upgrade the server or omit unsupported options. |
| `remote_tool_failed` | The server refused or failed the tool; check capabilities, request and logs. |
| `invalid_remote_payload` | The server returned an unsupported or malformed response; check compatibility and logs. |
| `missing_remote_etag` | A required whole-note read lacked an etag; no mutation was submitted. |
| `remote_timeout` | Check availability and the deadline. For writes, check current state before retrying. |
| `remote_connection_failed` | Check the endpoint and bearer token source; interactive login is unsupported. For writes, check current state before retrying. |
| `invalid_if_match` | Supply a nonempty etag or omit `--if-match`. |
| `invalid_frontmatter` | Pass a JSON object with finite numeric values. |
| `invalid_download_timeout` | Set `--download-timeout` to positive finite seconds. |
| `elicitation_required` | Use a client supporting human elicitation; nothing was written. |

Client selection and argument refusals exit 2; library, payload and remote operation failures exit 1. Interrupts exit 130. Writers never retry. A failed response does not establish whether a mutation was submitted. Inspect the current vault state before trying again after a timeout, a connection or tool failure, or a malformed response.

`--json` and `--agent` conflict with exit 2 and a JSON diagnostic. `okf verify` always refuses with exit 1 before reading configuration or making an RPC, including when both selectors are given. `Typer` parser errors retain their normal format. Local configuration and bootstrap logs can precede a structured diagnostic on stderr; the renderer does not control those logs.

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
