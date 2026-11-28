# Resources

Most JSON resources here serve the same data as a tool, whose reference describes the fields:

- `stats://vault` as [`stats`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#stats), without its `okf` section;
- `tags://vault/{field}` as [`list_tags`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#list_tags), with `tags://vault` grouping every indexed field;
- `folders://vault` as [`list_folders`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#list_folders);
- `toc://vault/{path}` as [`get_toc`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#get_toc);
- `similar://vault/{path}` as [`get_similar`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#get_similar), which needs [embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/embeddings/index.md);
- `recent://vault` as [`get_recent`](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/reader/#get_recent), with an ISO 8601 `modified_at_iso` added to each note.

`config://vault` has no tool twin. It reports the vault path, read-only mode, the indexed and required frontmatter fields, the effective exclude patterns, the templates folder, the conventions file and the folders that carry one, the Open Knowledge Format mode and whether the vault declares a bundle, search by meaning, and the attachment extensions. `ui://markdown_vault_mcp/app.html` is the [vault explorer](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/vault-explorer/index.md)'s page, for clients that render MCP Apps.

The `skill://` resources are two skills. `vault-workflow` tells the model which vault tool to use when. `okf-spec` holds the text of the [Open Knowledge Format](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/okf/#reading-the-specification) version the server follows, and is absent when OKF support is off. A client that supports skills over MCP, such as Claude Code, lists each skill by name and description and loads it only when needed; to any other client the files are ordinary resources.

Every JSON resource reports index freshness in the read's `_meta.index_stale`, `true` while a write is still being indexed. A resource can't wait for the index to catch up; the matching tool's `wait_for_pending_writes` can.

## Reading a resource

A resource is data this server serves at a URI, for a client to attach to the conversation rather than for the model to call. In Claude Code, mention one as `@<server>:<uri>`, where `<server>` is the name you gave this server when you added it; a resource template takes its parameters in the URI.

## `vault_config`

`config://vault` (application/json)

Vault settings, indexed fields, exclusions, and OKF detection and write state.

## `vault_stats`

`stats://vault` (application/json)

Vault statistics, search capabilities and known problems exchanging changes.

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

## `okf-spec/LICENSE.md`

`skill://okf-spec/LICENSE.md` (text/markdown)

## `okf-spec/PROVENANCE.md`

`skill://okf-spec/PROVENANCE.md` (text/markdown)

## `okf-spec/SKILL.md`

`skill://okf-spec/SKILL.md` (text/markdown)

Use when you create, edit, check or interpret notes in an Open Knowledge Format (OKF) bundle in this vault; holds the full text of OKF 0.3, the version this server reads and writes.

## `okf-spec/SPEC.md`

`skill://okf-spec/SPEC.md` (text/markdown)

## `okf-spec/_manifest`

`skill://okf-spec/_manifest` (application/json)

File listing for okf-spec

## `vault-workflow/SKILL.md`

`skill://vault-workflow/SKILL.md` (text/markdown)

Use when the user asks you to search, read, or reason about notes in their markdown vault; says which vault tool to use when, and how to chain them for good results.

## `vault-workflow/_manifest`

`skill://vault-workflow/_manifest` (application/json)

File listing for vault-workflow

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
