# MCP Prompts

Prompt templates guide the LLM through multi-step workflows using the vault tools. Write prompts (`research`, `discuss`, `create_from_template`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

## Quick Reference

| Prompt | Parameters | Category | Description |
|--------|------------|----------|-------------|
| [`summarize`](#summarize) | `path` | Read | Structured summary of a document |
| [`research`](#research) | `topic` | Write | Search, synthesize, and create a research note |
| [`discuss`](#discuss) | `path` | Write | Analyze and suggest improvements using `edit` |
| [`create_from_template`](#create_from_template) | `template_name` (optional) | Write | Create a new note from a template in your templates folder |
| [`related`](#related) | `path` | Read | Find related notes and suggest cross-references |
| [`compare`](#compare) | `path1`, `path2` | Read | Side-by-side comparison of two documents |
| [`propose-links`](#propose-links) | `scope`, `per_note_limit` (both optional) | Write | Propose meaningful new links between semantically-close notes that aren't already connected |

---

## `summarize`

Read a document and produce a structured summary with key themes and takeaways.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to summarize |

**Workflow:** Calls `read` on the given path, then produces a concise summary covering the document's main topics and key points.

## `research`

Search for a topic, synthesize findings across multiple documents, and create a new research note.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | string | The topic to research |

**Workflow:**

1. Calls `search` with the topic (uses hybrid mode if available)
2. Reads the top 3-5 results
3. Writes a structured summary with source links to `Research/{topic-slug}.md`

!!! note "Write prompt"
    This prompt creates a new document and is only available when `READ_ONLY=false`.

## `discuss`

Analyze a document and suggest improvements, applying changes via `edit` (not `write`).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to discuss |

**Workflow:**

1. Calls `read` to review the document
2. Identifies specific improvements (factual corrections, clarity, structure, completeness)
3. Presents proposed changes to the user
4. Applies approved changes using `edit` calls

!!! note "Write prompt"
    This prompt modifies existing documents and is only available when `READ_ONLY=false`.

## `create_from_template`

Create a new note by adapting a template from your configured templates folder.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `template_name` | string \| null | Optional template filename/path relative to `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` |

**Workflow:**

1. If `template_name` is not provided, calls `list_documents(folder=<templates folder>)`
2. Calls `read` on the selected template path
3. Presents template structure and asks the user for values
4. Proposes/collects target path for the new note
5. Calls `write` with the filled content

!!! note "Template convention"
    Templates are regular markdown files. Set `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` (default `_templates`) to control where template files live.

!!! note "Write prompt"
    This prompt creates a new document and is only available when `READ_ONLY=false`.

## `related`

Find related notes via search and suggest cross-references as markdown links.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to find related notes for |

**Workflow:**

1. Calls `read` to extract main topics and key terms
2. Calls `search` using those terms (prefers semantic mode)
3. Presents a list of related documents with connection explanations

This is a read-only prompt — it does not modify any documents.

## `compare`

Read two documents and produce a side-by-side comparison.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path1` | string | Relative path to the first document |
| `path2` | string | Relative path to the second document |

**Workflow:** Reads both documents and presents a comparison covering:

- What both documents agree on
- Where they differ or contradict
- Information present in one but absent from the other

## `propose-links`

Scan a bounded set of notes for semantically close pairs that aren't already linked, apply LLM judgment to keep only the meaningful connections, and write them to the vault on confirmation.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `scope` | string \| null | Candidate set: a folder path (e.g. `"1-Projects"`), the literal `"recent"` (default; notes modified in the last 30 days), or the literal `"all"`. No trailing slashes. |
| `per_note_limit` | integer \| null | Max candidates per note to evaluate. Defaults to 5. |

**Workflow:**

1. Resolve `scope` to a list of notes, warning if more than 100 notes match.
2. For each note, gather candidates via `get_similar` (with a `search(mode='keyword')` fallback when embeddings aren't configured).
3. Filter out pairs where A already links to B (via `get_outlinks`).
4. LLM judgment: read each candidate's title and opening to confirm the connection is meaningful, not merely lexical.
5. Pick direction (one-way `A → B`) and placement per note shape (inline citation, `## Related` section, hub bullet list, footnote — whichever fits).
6. Show a batch preview of every proposed edit.
7. Write approved edits; skip failures (e.g., `ConcurrentModificationError`) and report reasons.

!!! note "Write prompt"
    This prompt modifies documents and is only available when `READ_ONLY=false`.

!!! note "Embeddings recommended"
    `propose-links` falls back to keyword search without embeddings, but the quality of candidates is noticeably better when `get_similar` is available. See [Embeddings](guides/embeddings.md) for setup.

## Ambient patterns without prompts

Not every LLM-native workflow needs a codified MCP prompt. With a capable model and the server's tools, several high-value flows work from prose intent alone. The examples below document these composable patterns — which tools the model orchestrates and why each pattern doesn't need its own prompt.

### Capture a URL as a note

> "Fetch https://example.com/article, summarize as a Resource note under `3-Resources/`, and link any existing notes on the topic."

**Tools composed:** `fetch` → LLM summarization → `search` (to find related existing notes) → `write` (with frontmatter and wikilinks).

**Why no codified prompt:** the only knob is the target folder, and the user expresses it in the ask. No structure worth pre-specifying.

### Research a topic into interlinked notes

> "Research product security regulations, compare the major frameworks, and create a set of interlinked notes — one per regulation, plus a map-of-content."

**Tools composed:** client-side web search → LLM synthesis → multiple `write` calls with `[[wikilinks]]` connecting the resulting notes.

**Why no codified prompt:** modern LLMs cross-link naturally when asked for "a set of interlinked notes." The single-note version (`research` prompt) handles the simpler case where one note is enough.

### Distill conversations into Inbox notes

> "Summarize today's conversations into Inbox notes — one per topic."

**Tools composed:** `conversation_search` + `recent_chats` (Claude.ai client-side) → LLM distillation → `write` per topic.

**Why (partially) codified:** the [`para-capture-chats`](guides/para.md#using-the-para-prompts) prompt exists as the one-click version because it has platform-specific tool names to call out and constraints on what to skip (pure Q&A, debugging). Outside the PARA pack, the ambient ask works fine.

### Split and merge captures

> "Split this Inbox note into two — one for the Postgres upgrade, one for the CRA compliance work."
>
> "Merge this into `3-Resources/distributed-consensus.md` instead of creating a duplicate."

**Tools composed:** `read` + `search` (to find merge target) + `write` (new notes or extended target) + `delete` (the source note).

**Why no codified prompt:** the split/merge heuristic is codified inside `para-triage` where it's most useful. Outside triage, the ambient ask is a direct one-sentence instruction.

### Ad-hoc link proposal for a single note

> "Get the context for `1-Projects/migrate-postgres.md` and identify any notes we haven't linked yet."

**Tools composed:** `get_context` (surfaces the `similar` field) → LLM filtering → `edit` or `write` to add selected links.

**Why no codified prompt:** [`related`](#related) covers the find-candidates case read-only; the per-note write case is a natural extension and doesn't add enough structure to warrant a standalone prompt. The vault-wide sweep *is* codified, as [`propose-links`](#propose-links).

## How to invoke prompts

Three invocation affordances, roughly in order of convenience:

### 1. Claude.ai: the `+` menu (recommended)

On Claude.ai, once the server is added as a connector, every prompt appears in the compose area's `+` menu. Click `+`, select **connectors**, pick the server, pick a prompt — Claude opens with the invocation scaffolded. No typing; no remembering argument names.

This is the best UX for frequent prompts (`propose-links`, `summarize`, the PARA / Zettelkasten workflow prompts).

### 2. Claude Code: the `/` menu

In Claude Code, MCP prompts appear in the slash-command menu after the server is configured in the workspace's MCP settings. Same effect as the Claude.ai `+` menu — the prompt is pre-scaffolded.

### 3. Plain conversation

Every prompt can be invoked from prose ("use the propose-links prompt with scope='1-Projects'"). The model resolves the name and calls the prompt. This is the fallback — more typing, but works in any MCP client.

The ambient-pattern flows above *only* use plain conversation; they don't have a prompt name to invoke. The trade-off: no menu shortcut, but no prompt to maintain either.
