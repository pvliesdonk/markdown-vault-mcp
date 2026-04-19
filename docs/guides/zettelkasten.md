# Zettelkasten with markdown-vault-mcp

A Zettelkasten is a personal knowledge management system based on atomic notes, cross-linking, and emergent discovery. This guide shows how to use markdown-vault-mcp as your Zettelkasten backend, leveraging its search, linking, and graph navigation tools to build a connected knowledge base.

!!! note
    This is one of many ways to organize a vault with markdown-vault-mcp. The server is a generic markdown collection backend — Zettelkasten conventions are applied in this guide but not required or enforced by the server.

## Vault Setup

### Recommended folder structure

Two approaches work well with markdown-vault-mcp:

**Flat structure** (simplest):
```
vault/
  note1.md
  note2.md
  note3.md
  _templates/
    fleeting.md
    literature.md
    permanent.md
    moc.md
```

All notes live in the root. Folders are only for attachments and metadata.

**Minimal folders**:
```
vault/
  Inbox/
    fleeting-note.md
  Notes/
    permanent-note.md
  Archive/
    old-note.md
  _templates/
  assets/
    diagram.pdf
```

Organize by workflow stage or topic, but **rely on links, not folders, for knowledge structure.** Folders are convenience — the graph is the system.

### Frontmatter schema

Recommend this structure for all notes:

```yaml
---
title: "Note title"
type: fleeting | literature | permanent | moc
tags: [tag1, tag2]
source: "URL or citation"  # for literature notes only
created: YYYY-MM-DD
---
```

**Field meanings:**

- `title` — The note's heading. Used for display in lists and searches.
- `type` — Note category: `fleeting` (quick capture), `literature` (extracted from source), `permanent` (your own synthesis), `moc` (map of content / hub note). Helps you review notes by stage.
- `tags` — List of keywords. Searchable via `filters={"tags": "value"}`.
- `source` — Full URL or bibliographic citation. Used to credit the original source for literature notes.
- `created` — ISO 8601 date when the note was written. Useful for reviewing note age.

### Make frontmatter fields searchable

Configure `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` to make `type` and `tags` structured filters in search:

```bash
export MARKDOWN_VAULT_MCP_INDEXED_FIELDS=type,tags
```

Then you can search by note type or tag using the Python API:

```python
results = collection.search("query", filters={"type": "permanent"})
results = collection.search("query", filters={"tags": "system-design"})
```

Or restrict to a folder via the CLI:

```bash
markdown-vault-mcp search "query" --folder Notes
```

### Filename conventions

The server uses file paths as note identity. Choose any naming scheme:

- **Timestamp-based:** `YYYYMMDDHHMM-distributed-consensus.md` — sorts chronologically, no collisions
- **Title-based:** `distributed-consensus.md` — human-readable, easier to read in logs
- **Luhmann-style IDs:** `1a.2b.3c.md` — mimics paper ZK, encodes hierarchy (rarely needed)

Pick one and stick to it. The server handles all three equally well.

## Workflow

Four-stage workflow for building your Zettelkasten:

### 1. Capture (Fleeting Notes)

Write quick, unpolished notes as they occur. Use the `fleeting` template:

```bash
# Create a new fleeting note interactively
markdown-vault-mcp serve &
# Then in Claude, call: create_from_template(template_name='fleeting')
```

Or programmatically:

```python
from markdown_vault_mcp import Collection

collection = Collection(source_dir="/path/to/vault")
collection.write(
    "Inbox/quick-idea.md",
    content="Distributed systems are hard.",
    frontmatter={"type": "fleeting", "tags": ["systems"]}
)
```

**Goals for this stage:**
- Capture the idea without judgment
- Don't polish or structure
- One idea per note
- Review daily or weekly

**Review inbox:**

```bash
markdown-vault-mcp search "*" --folder Inbox
```

### 2. Develop (Literature → Permanent)

Expand fleeting notes into permanent knowledge. Two paths:

**From external sources (literature notes):**
1. Read an article, book section, or paper
2. Create a `literature` note with key extracts and your interpretation
3. Use the `discuss` MCP prompt to strengthen and clarify the writing
4. Convert into a `permanent` note once synthesized

**From existing notes:**
1. Use `get_context(path)` to see what already links to this note
2. Use `search(query, mode='hybrid')` to find related notes
3. Use `edit` to expand the note with new ideas and links
4. Change `type: fleeting` to `type: permanent` once mature

**Example workflow:**

```python
# See the note's current neighborhood
context = collection.get_context("Inbox/consensus-algorithms.md")
print(f"Backlinks: {context.backlinks}")
print(f"Similar notes: {context.similar}")

# Search for related notes
results = collection.search("consensus", mode="hybrid", limit=20)

# Once expanded, update the note
collection.edit(
    "Inbox/consensus-algorithms.md",
    old_text="type: fleeting",
    new_text="type: permanent"
)
```

### 3. Connect (Build the Graph)

Linking transforms isolated notes into a knowledge network. Four tools support this:

**Explore the neighborhood:**

```python
# See everything connected to this note
context = collection.get_context("Notes/consensus.md")
```

Returns:
- `backlinks` — notes that link here (who cites this idea?)
- `outlinks` — notes this links to (what does this build on?)
- `similar` — semantically related notes not yet linked
- `folder_notes` — other notes in the same folder
- `tags` — frontmatter tags for grouping
- `modified_at` — last modification timestamp

**Find related notes you haven't linked yet:**

```python
similar = collection.get_similar("Notes/consensus.md", limit=10)
for note in similar:
    print(f"Similar: {note.title} ({note.score:.2f})")
```

**Discover indirect connections:**

```python
# Find the shortest path between two ideas
path = collection.get_connection_path(
    source="Notes/distributed-systems.md",
    target="Notes/fault-tolerance.md",
    max_depth=5
)
if path:
    print(f"Connection: {' -> '.join(path)}")
else:
    print("No connection found within max_depth")
```

**Add links to notes:**

Edit the note and add `[[wikilink]]` or `[text](path.md)` references:

```python
collection.edit(
    "Notes/consensus.md",
    old_text="## Evidence",
    new_text="## Evidence\n\nSee [[Byzantine Fault Tolerance]] for formal proofs."
)
```

All three link formats work:
- `[[wikilinks]]` — shortest, preferred for internal links
- `[Markdown links](path.md)` — standard markdown
- `[Reference-style][1]\n\n[1]: path.md` — separates link text from destination

**Rename safely with link updates:**

When a note title changes, rename it and update all backlinks automatically:

```python
collection.rename(
    "Notes/old-title.md",
    "Notes/new-title.md",
    update_links=True  # Rewrites [[old-title]] to [[new-title]] in all notes
)
```

### 4. Review (Maintain the Graph)

Weekly reviews keep your vault healthy and connected:

**Check vault statistics:**

```python
stats = collection.stats()
print(f"Documents: {stats.document_count}")
print(f"Broken links: {stats.broken_link_count}")
print(f"Orphan notes: {stats.orphan_count}")
```

**Find isolated notes:**

```python
orphans = collection.get_orphan_notes()
for note in orphans:
    print(f"Isolated: {note.path}")
    # Either link it to the rest of the vault, or delete it
```

**Find and fix broken links:**

```python
broken = collection.get_broken_links()
for link in broken:
    print(f"Broken: {link.source_path} -> {link.target_path}")
    # Fix the link or delete the file it points to
```

**Identify hub notes (candidates for MOCs):**

```python
hubs = collection.get_most_linked(limit=10)
for hub in hubs:
    print(f"{hub.title}: {hub.backlink_count} inbound links")
```

## Maps of Content (MOCs)

A **MOC** (Map of Content) is a curated hub note that aggregates links to related notes on a theme. MOCs surface the structure of your vault and guide exploration.

**When to create a MOC:**

- You have 5+ related permanent notes on a theme
- You frequently search for the same topic
- You want to guide someone (or your future self) through a domain

**How to build one:**

1. Identify the theme — e.g., "Distributed Systems"
2. Search for related notes:
   ```python
   results = collection.search("distributed systems", mode="hybrid", limit=30)
   ```
3. Use `get_most_linked()` to find existing hubs:
   ```python
   hubs = collection.get_most_linked(limit=10)
   ```
4. Create a new `moc` template note:
   ```python
   collection.write(
       "Notes/Distributed-Systems-MOC.md",
       content="# Distributed Systems\n\n## Core concepts\n...",
       frontmatter={
           "type": "moc",
           "tags": ["moc"],
           "title": "Distributed Systems (MOC)"
       }
   )
   ```
5. Add `[[wikilinks]]` to permanent notes grouped by depth or topic
6. Link the MOC back from related permanent notes:
   ```python
   collection.edit(
       "Notes/consensus.md",
       old_text="## Related",
       new_text="## Related\n\nSee [[Distributed-Systems-MOC]] for the full map."
   )
   ```

**MOC structure example:**

```markdown
---
title: "Distributed Systems (MOC)"
type: moc
tags: [moc, systems]
created: YYYY-MM-DD
---

# Distributed Systems

A map of the core concepts in distributed systems.

## Foundations

The basics: what makes systems distributed?

- [[Time and Ordering]]
- [[Consensus Algorithms]]
- [[Fault Tolerance]]

## Architectures

How to build real systems:

- [[Replication]]
- [[Sharding]]
- [[Load Balancing]]

## Challenges

Hard problems we keep solving:

- [[Byzantine Fault Tolerance]]
- [[Network Partitions]]
- [[CAP Theorem]]

## See also

- [[Scalability]] — related but distinct topic
```

## Using Templates

Templates accelerate note creation. The `fleeting`, `literature`, `permanent`, and `moc` templates are provided in `examples/zettelkasten/templates/`.

**Configure the template folder:**

```bash
export MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER=/path/to/examples/zettelkasten/templates
markdown-vault-mcp serve
```

**Use in Claude via the `create_from_template` prompt:**

The prompt will:
1. List available templates
2. Ask you to choose one
3. Gather required values (title, source, etc.)
4. Create the note with filled-in frontmatter

**Invoke via MCP prompt:**

`create_from_template` is an MCP prompt, not a Python API method. Invoke it through your MCP client (e.g., Claude):

```
Use the create_from_template prompt with template_name="literature"
```

The prompt will call `list_documents(folder=<templates_folder>)` to enumerate templates, then `read` the chosen one, then `write` the filled note — all through vault tools.

## Using the Zettelkasten Prompt

The `examples/zettelkasten/prompts/zettelkasten.md` prompt guides you through connecting a note to your vault in five steps:

1. **Read and understand** — extract the central claim
2. **Survey the neighborhood** — see existing backlinks and similar notes
3. **Discover broader connections** — search for related permanent notes
4. **Suggest links** — present new connections with context
5. **Check for MOC opportunity** — flag if a new MOC would help

**Configure prompt mounting:**

If your MCP server supports `PROMPTS_FOLDER`:

```bash
export MARKDOWN_VAULT_MCP_PROMPTS_FOLDER=/path/to/examples/zettelkasten/prompts
markdown-vault-mcp serve
```

Then in Claude, the `zettelkasten` prompt is available for use.

**Fire the prompt from Claude.ai's `+` menu.** Once the server is added as a connector on Claude.ai, every MCP prompt — including `zettelkasten` — appears in the compose area's `+` menu. Click `+`, select **connectors**, pick the server, pick the prompt. Claude opens with the invocation scaffolded, so you don't need to remember the arguments. See [How to invoke prompts](../prompts.md#how-to-invoke-prompts) for other clients.

**Use the prompt:**

```
In Claude, call: zettelkasten(path='Notes/my-idea.md')
```

The prompt will walk you through connecting the note, discovering related ideas, and optionally creating a MOC.

## Tips and Best Practices

### Start with statistics

Before writing your first note, run `stats()` to understand your vault's shape:

```python
stats = collection.stats()
print(f"Documents: {stats.document_count}")
print(f"Broken links: {stats.broken_link_count}")
print(f"Orphaned notes: {stats.orphan_count}")
```

This gives you a baseline for the monthly review.

### Use `get_context()` before every edit

`get_context(path)` is your primary navigation tool. Before editing any note, call it to see:

- Who already links to this note (validation that it matters)
- What this note links to (avoid circular references)
- Semantically similar notes (discover missed connections)
- Folder notes (all other notes in the same folder, from `context.folder_notes`)

### Hybrid search beats keyword-only

Always prefer `mode='hybrid'` when searching:

```python
# Good
collection.search("consensus", mode="hybrid", limit=20)

# Weaker (keyword only)
collection.search("consensus", mode="keyword", limit=20)
```

Hybrid search combines full-text ranking (exact matches, stemming) with semantic similarity (meaning), so you get both precision and discovery.

### Review orphans weekly

Orphaned notes (no inbound or outbound links) are knowledge dead zones. Schedule a weekly check:

```python
orphans = collection.get_orphan_notes()
for note in orphans:
    # Either integrate it: add outlinks to existing notes
    # Or delete it: it wasn't worth connecting
```

This prevents accumulating notes you're not thinking about.

### Use `get_connection_path()` for cross-domain discovery

When you want to see how two seemingly distant topics relate, use `get_connection_path()`:

```python
path = collection.get_connection_path(
    source="Notes/machine-learning.md",
    target="Notes/philosophy.md",
    max_depth=6
)
# Might return: ["machine-learning", "artificial-intelligence", "consciousness", "philosophy"]
```

This reveals unexpected bridges between domains.

### Link aggressively, prune minimally

Don't agonize over link relevance. If two notes touch on similar ideas, link them. The search tools and `get_context()` will help you rediscover connections. Over-linking is better than under-linking — the network gets richer as you explore.

Exception: Delete broken links immediately. `get_broken_links()` makes this easy.

### Tag for grouping, not taxonomy

Tags are not a rigid classification system. Use them for grouping and quick filtering:

```python
# Good
tags: [systems, distributed, algorithms]

# Avoid (over-specific)
tags: [system-type-1, category-a-variant-b]
```

Use search and links for discovery. Tags are just shortcuts.

### Let Claude split or merge fleeting notes

Two shape operations that an LLM handles cleanly but manual workflows usually skip:

- **Split.** When a fleeting note contains two ideas — one literature reference and one nascent permanent claim — ask Claude to split it into two notes. Each is then developed independently.
- **Merge.** When a fleeting note restates or extends an existing permanent note, ask Claude to merge it (add as a new paragraph or `## Extension` section) rather than letting near-duplicates accumulate. The [`search`](../tools/index.md#search) + `read` + `write` + `delete` composition handles this in a single prompt turn.

Resist pre-splitting or pre-merging before review. Claude does both in one pass.

## Next Steps

- **Read the design document** for details on the linking system and search algorithms: [`docs/design.md`](../design.md)
- **Explore the MCP tools** to understand the full API: [`tools/index.md`](../tools/index.md)
- **Review the examples** for templates and prompts: [`examples/zettelkasten/`](../../examples/zettelkasten/)
- **Prefer an action-oriented workflow?** Try the [PARA guide](para.md) — Projects, Areas, Resources, Archive with triage, kickoff, and weekly review prompts
- **Ambient patterns**: [`docs/prompts.md`](../prompts.md#ambient-patterns-without-prompts) — flows the LLM handles from prose alone (URL capture, research, split/merge, ad-hoc link proposal)
