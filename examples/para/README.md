# PARA Examples

Ready-made templates and prompts for a [PARA](../../docs/guides/para.md) (Projects, Areas, Resources, Archive) workflow with markdown-vault-mcp.

## Templates

Five note templates covering the PARA lifecycle:

- **`inbox.md`** — Untyped quick capture; triage assigns the type later
- **`project.md`** — Concrete outcome with a deadline
- **`area.md`** — Ongoing responsibility with a standard and review cadence
- **`resource.md`** — Reference material on a topic
- **`weekly-review.md`** — Dated review note with preset sections

## Prompts

Four prompts that codify the canonical PARA workflow:

- **`para-capture-chats.md`** — Distill recent chat conversations (via `conversation_search` / `recent_chats` on Claude.ai) into Inbox notes for later triage
- **`para-triage.md`** — Classify an Inbox note into Project, Area, or Resource and move it
- **`para-project-kickoff.md`** — Define a project's outcome and surface related Resources, Areas, and past Archived projects
- **`para-weekly-review.md`** — Scan active projects for staleness, audit areas, and identify archive candidates; writes a dated review note

## Usage

### Templates

Mount the `templates/` directory to enable template-based note creation:

```bash
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/para/templates
markdown-vault-mcp serve
```

Then in Claude, use the `create_from_template` prompt to create new notes from templates interactively.

### Prompts

Mount the `prompts/` directory via `PROMPTS_FOLDER`:

```bash
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/para/prompts
markdown-vault-mcp serve
```

Then in Claude, call any of the four PARA prompts by name. On Claude.ai, prompts also appear in the compose area's `+` menu once the MCP server is added as a connector — one click to fire.

## Configuration

Recommended env vars for a PARA vault:

```bash
# Core
export MARKDOWN_VAULT_MCP_SOURCE_DIR=/path/to/vault
export MARKDOWN_VAULT_MCP_READ_ONLY=false

# Indexing — make type/status/tags/area filterable in search
export MARKDOWN_VAULT_MCP_INDEXED_FIELDS=type,status,tags,area

# Templates and Prompts
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/para/templates
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/para/prompts

# Embeddings (optional, but recommended — project-kickoff resurface relies on semantic search)
export MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/vault/.vault/embeddings.npy
export MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=fastembed

# Persistence
export MARKDOWN_VAULT_MCP_INDEX_PATH=/path/to/vault/.vault/index.db
```

## Recommended vault layout

```
vault/
  0-Inbox/        # quick capture, untyped
  1-Projects/     # active projects with outcomes
  2-Areas/        # ongoing responsibilities
  3-Resources/    # reference material
  4-Archive/      # status=archived items (any type)
  _templates/     # pointed at by TEMPLATES_FOLDER
```

The server does not enforce this layout. The prompts suggest these paths when they need to move notes (triage target, weekly-review location); they fall back to asking if your vault uses a different structure.

## See Also

- **PARA Guide**: [`docs/guides/para.md`](../../docs/guides/para.md) — comprehensive walkthrough
- **MCP Tools Reference**: [`docs/tools/index.md`](../../docs/tools/index.md) — all available tools
- **Design Document**: [`docs/design.md`](../../docs/design.md) — linking system and search algorithms
- **Zettelkasten alternative**: [`docs/guides/zettelkasten.md`](../../docs/guides/zettelkasten.md) — for idea-centric knowledge management
