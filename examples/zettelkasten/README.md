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

# Indexing
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

## See Also

- **Zettelkasten Guide**: [`docs/guides/zettelkasten.md`](../../docs/guides/zettelkasten.md) — comprehensive walkthrough
- **MCP Tools Reference**: [`docs/tools/index.md`](../../docs/tools/index.md) — all available tools
- **Design Document**: [`docs/design.md`](../../docs/design.md) — linking system and search algorithms
- **PARA alternative**: [`docs/guides/para.md`](../../docs/guides/para.md) — for action-oriented Projects/Areas/Resources/Archive workflows
