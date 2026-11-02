# Query a vault from the terminal

The `markdown-vault-mcp` command can look things up in a vault without Claude, from a quick search to a count of every matching note. It reads either a vault on the same machine or one that a server elsewhere publishes over MCP. The [command reference](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/reference/cli/index.md) lists every command and option.

## From another machine

### Install the command

```
uv tool install markdown-vault-mcp
```

`pipx install markdown-vault-mcp` works as well. A remote lookup needs no extras, because the vault and its index stay on the server.

### Point it at the server

Create `~/.config/markdown-vault-mcp/client.toml`:

```
[remote]
url = "https://vault.example/mcp"
token_env = "VAULT_ACCESS_TOKEN"
```

`url` is the server's complete MCP endpoint, path included: its public address followed by `MARKDOWN_VAULT_MCP_HTTP_PATH`. `token_env` names the environment variable that holds the bearer token, so the token itself never lands in the file. Put the token there before running a command:

```
export VAULT_ACCESS_TOKEN="your-bearer-token"
```

The server must accept that token, as a [bearer token server](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/deploy/authentication/index.md) does. The command can't sign in interactively; with OIDC, pass an access token you already hold. To query a different server for one command, pass `--server` with its endpoint; `--local` reads the vault on this machine instead.

### Look something up

```
markdown-vault-mcp search "release notes"
markdown-vault-mcp read "Projects/plan.md" --section "Next steps"
markdown-vault-mcp links to "Projects/plan.md"
markdown-vault-mcp query count "release" --scope body
markdown-vault-mcp query aggregate --by tag
markdown-vault-mcp query path "relase plan" --fuzzy
```

Output is plain text by default. Add `--json` for the server's structured result, for scripts, or `--agent` for one escaped record per line. A remote lookup never falls back to a local vault. If the server can't answer, the command says why and exits 1.

## Inside the server's Docker container

The image carries the same command. Run inside the container, it reads the server's own index with the container's settings, so it needs no address and no token, and it never writes to the index or the vault:

```
docker compose exec -u appuser markdown-vault-mcp markdown-vault-mcp search "release notes"
```

Run it as `appuser`, the user the server runs as. As root, which is what `docker exec` uses by default, a vault that is a git checkout fails with `Managed mode requires SOURCE_DIR to be empty or a git repository`, because git refuses a checkout owned by another user.

The command also prints the server's startup log lines on stderr. Set `MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING` for the call to keep the terminal to the results. An alias for a container started with `docker run --name my-vault` puts both in one word:

```
alias vault='docker exec -u appuser -e MARKDOWN_VAULT_MCP_LOG_LEVEL=WARNING my-vault markdown-vault-mcp'
vault search "release notes"
```

What the command can reach on either route is in the [security model](https://pvliesdonk.github.io/markdown-vault-mcp/6.1/security-model/index.md).
