# Prompts

The built-in prompts below come with this server. `summarize`, `summarize-subtree`, `related` and `compare` only read the vault. `research`, `discuss`, `propose-links` and `create_from_template` write to it, so a server with `MARKDOWN_VAULT_MCP_READ_ONLY=true` hides those four.

`MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` adds your own prompts from Markdown files, and a file named after a built-in prompt replaces it. The [PARA](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/para/index.md) and [Zettelkasten](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/zettelkasten/index.md) pages each come with such a prompt pack.

## Running a prompt

A prompt is a template this server fills from the arguments listed with it and hands to your client as a message, so a task starts with the server's own instructions. It runs the same tools with the same access as anything you type. How to start one depends on the client:

- **Claude Code** lists each prompt in the `/` menu as `/<server>:<prompt> (MCP)`, where `<server>` is the name you gave this server when you added it. Typing `/mcp__<server>__<prompt>` runs it too; arguments follow it separated by spaces, each a single token.
- **claude.ai and Claude Desktop** offer a connector's prompts from the compose area's **+** menu once the connector is added.

## `summarize`

Summarize a vault document with structured coverage of main topics and key points.

| Argument | Required | Description           |
| -------- | -------- | --------------------- |
| `path`   | yes      | Path of the document. |

`summarize` with `path="Projects/roadmap.md"` covers one note. For a folder or several notes, use [`summarize-subtree`](#summarize-subtree).

## `research`

Search the vault for a topic and save what the best matches say as a new research note.

| Argument | Required | Description            |
| -------- | -------- | ---------------------- |
| `topic`  | yes      | The topic to research. |

`research` with `topic="Product security regulations"` searches this vault only, not the web: hybrid search, or keyword search without embeddings. It reads the three to five best matches and writes `product-security-regulations.md`, with `tags: [research]` and a link to each source, in the folder the vault's conventions name for research notes; it asks you for a folder when they name none. It never overwrites: if that path exists it picks another name, and with no matches it writes nothing.

## `discuss`

Review a vault note and edit it: factual corrections, clarity, structure and completeness, shown to you first.

| Argument | Required | Description                      |
| -------- | -------- | -------------------------------- |
| `path`   | yes      | Path to the document to analyze. |

`discuss` with `path="Notes/raft.md"` lists the changes it proposes before touching the note, then applies each as a targeted `edit`, never a full rewrite, so the note's frontmatter stays as it is.

## `related`

Find related notes and suggest cross-references. Read-only: changes no documents.

| Argument | Required | Description                                     |
| -------- | -------- | ----------------------------------------------- |
| `path`   | yes      | Path to the document to find related notes for. |

## `compare`

Compare two vault notes: agreements, contradictions, and unique information in each.

| Argument | Required | Description                  |
| -------- | -------- | ---------------------------- |
| `path1`  | yes      | Path to the first document.  |
| `path2`  | yes      | Path to the second document. |

## `propose-links`

Propose links between closely related notes that aren't connected yet, and add the ones you approve.

| Argument         | Required | Description                                                                                                                                                                                 |
| ---------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scope`          | no       | Candidate set. Accepts a folder path (such as '1-Projects'), the literal 'recent' (default; notes modified in the last 30 days), or the literal 'all'. No trailing slashes on folder paths. |
| `per_note_limit` | no       | Max candidates per note to evaluate (default 5).                                                                                                                                            |

`propose-links` with `scope="1-Projects"` compares each note in that folder with its closest notes. It drops pairs that are already linked or that a folder's conventions rule out, then shows every proposed link as one numbered preview; nothing is written until you approve. Leave `scope` empty for notes changed in the last 30 days, or pass `"all"`. Above 100 notes it asks first. Without [embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/embeddings/index.md) it falls back to keyword matches, which find fewer real connections.

## `create_from_template`

Create a new note from one of the vault's templates.

| Argument        | Required | Description                                                                         |
| --------------- | -------- | ----------------------------------------------------------------------------------- |
| `template_name` | no       | Template to use, such as `"meeting-notes.md"`; leave empty to choose from the list. |

`create_from_template` with `template_name="meeting.md"` reads `_templates/meeting.md`. It asks for the values it needs, proposes a path in the folder the vault's conventions name for it (or asks for one when they name none), then writes it. It asks before replacing an existing file. Leave `template_name` empty to choose from the folder's list. `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` moves the templates folder; the [PARA](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/para/#using-templates) and [Zettelkasten](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/zettelkasten/#using-templates) pages come with template sets.

## `summarize-subtree`

Summarize a folder or a set of notes with your own model, in batches that keep note bodies out of the conversation.

| Argument | Required | Description                                                                                                                |
| -------- | -------- | -------------------------------------------------------------------------------------------------------------------------- |
| `paths`  | yes      | One or more note paths and/or folder prefixes (such as 'projects/alpha' or 'notes/a.md, notes/b.md'), separated by commas. |
| `focus`  | no       | Optional free-text steer, such as 'extract action items'. Empty produces a general summary.                                |

`summarize-subtree` with `paths="Projects/alpha"` and `focus="open decisions"` summarizes the folder with your client's own model, in batches, handing them to subagents where the client has them. It ends by naming the notes it covered, and it only reads. When the operator has set up a summarization backend, the prompt first offers the [`summarize` tool](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/reference/tools/summarize/#summarize), which does the same in one call.
