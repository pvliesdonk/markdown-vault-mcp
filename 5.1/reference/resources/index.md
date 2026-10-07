# Resources

Most JSON resources here serve the same data as a tool, whose reference describes the fields:

- `stats://vault` as [`stats`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#stats), without its `okf` section;
- `tags://vault/{field}` as [`list_tags`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#list_tags), with `tags://vault` grouping every indexed field;
- `folders://vault` as [`list_folders`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#list_folders);
- `toc://vault/{path}` as [`get_toc`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#get_toc);
- `similar://vault/{path}` as [`get_similar`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#get_similar), which needs [embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/embeddings/index.md);
- `recent://vault` as [`get_recent`](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/reader/#get_recent), with an ISO 8601 `modified_at_iso` added to each note.

`config://vault` has no tool twin. It reports the vault path, read-only mode, the indexed and required frontmatter fields, the effective exclude patterns, the templates folder, the conventions file and the folders that carry one, the Open Knowledge Format mode and whether the vault declares a bundle, search by meaning, and the attachment extensions. `ui://markdown_vault_mcp/app.html` is the [vault explorer](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/vault-explorer/index.md)'s page, for clients that render MCP Apps.

Every JSON resource reports index freshness in the read's `_meta.index_stale`, `true` while a write is still being indexed. A resource can't wait for the index to catch up; the matching tool's `wait_for_pending_writes` can.

## Reading a resource

A resource is data this server serves at a URI, for a client to attach to the conversation rather than for the model to call. In Claude Code, mention one as `@<server>:<uri>`, where `<server>` is the name you gave this server when you added it; a resource template takes its parameters in the URI.

## `vault_config`

`config://vault` (application/json)

The vault's settings: read-only mode, indexed fields, exclusions, OKF state.

## `vault_stats`

`stats://vault` (application/json)

Vault statistics: note, chunk and link counts and search capabilities.

## `vault_tags`

`tags://vault` (application/json)

Every value of every indexed frontmatter field, grouped by field.

## `vault_folders`

`folders://vault` (application/json)

Every folder in the vault that holds notes.

## `vault_recent`

`recent://vault` (application/json)

The 20 most recently modified notes.

## `_app_shell`

`ui://markdown_vault_mcp/app.html` (text/html;profile=mcp-app)

## `vault_tags_by_field`

`tags://vault/{field}` (application/json)

Every value of one indexed frontmatter field.

Parameters: `field`.

## `vault_toc`

`toc://vault/{path}` (application/json)

Heading outline of a note, or of up to 200 notes under a folder.

Parameters: `path`.

## `vault_similar`

`similar://vault/{path}` (application/json)

The 10 notes closest in meaning to a note.

Parameters: `path`.
