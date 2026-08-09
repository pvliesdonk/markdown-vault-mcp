# Zettelkasten Examples

Ready-made templates and prompt for a [Zettelkasten](../../docs/guides/zettelkasten.md) workflow with markdown-vault-mcp.

## Templates

Four note templates for the four stages of knowledge development:

- **`fleeting.md`** — Quick capture of raw ideas
- **`literature.md`** — Extracted knowledge from external sources
- **`permanent.md`** — Your own synthesized understanding
- **`moc.md`** — Map of Content (hub note aggregating related notes)

## Prompts

- **`zettelkasten.md`** — Five-step workflow for connecting a note to the vault: read, survey neighborhood, discover connections, suggest links, check for MOC opportunities

## Usage

### Templates

Mount the `templates/` directory to enable template-based note creation:

```bash
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/zettelkasten/templates
markdown-vault-mcp serve
```

Then in Claude, use the `create_from_template` prompt to create new notes from templates interactively.

### Prompts

If your MCP server supports `PROMPTS_FOLDER`, mount the `prompts/` directory:

```bash
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/zettelkasten/prompts
markdown-vault-mcp serve
```

Then in Claude, call the `zettelkasten` prompt on any note to discover connections and suggested links.

## Configuration

Recommended env vars for a Zettelkasten vault:

```bash
# Core
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/vault
export MARKDOWN_VAULT_MCP_READ_ONLY=false

# Indexing (indexed fields are also keyword-searchable by default;
# set MARKDOWN_VAULT_MCP_SEARCHABLE_FIELDS=none to keep them filter-only)
export MARKDOWN_VAULT_MCP_INDEXED_FIELDS=type,tags

# Templates and Prompts
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/zettelkasten/templates
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/zettelkasten/prompts

# Embeddings (optional)
export MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/vault/.vault/embeddings.npy
export MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=fastembed  # or ollama, openai

# Persistence
export MARKDOWN_VAULT_MCP_INDEX_PATH=/path/to/vault/.vault/index.db
```

## Using with OKF

These templates work as-is inside an [OKF](../okf/README.md) bundle: every one already sets a `type` (`fleeting` / `literature` / `permanent` / `moc`), so a vault built from them passes `okf_validate` from the first note. To turn OKF on, add `okf_version: "0.2"` to the root `index.md`. Zettelkasten leans on links and tags rather than a lifecycle `status`, so there is no `status` collision to resolve; add OKF's `sources` and `status` fields to literature and permanent notes if you want provenance and lifecycle tracking. See [Using Zettelkasten with OKF](../../docs/guides/zettelkasten.md#using-zettelkasten-with-okf).

## See Also

- **Zettelkasten Guide**: [`docs/guides/zettelkasten.md`](../../docs/guides/zettelkasten.md) — comprehensive walkthrough
- **OKF pack**: [`examples/okf/`](../okf/README.md) — declare and enforce the Open Knowledge Format on this vault
- **MCP Tools Reference**: [`docs/tools/index.md`](../../docs/tools/index.md) — all available tools
- **Design Document**: [`docs/design/design.md`](../../docs/design/design.md) — linking system and search algorithms
- **PARA alternative**: [`docs/guides/para.md`](../../docs/guides/para.md) — for action-oriented Projects/Areas/Resources/Archive workflows
