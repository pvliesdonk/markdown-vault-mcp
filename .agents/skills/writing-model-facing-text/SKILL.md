---
name: writing-model-facing-text
description: >-
  Use when writing or changing a tool, parameter, resource or prompt description, a tool title or annotation, or a server-instructions snippet, and when reviewing such text: it decides which sentences the model can act on and where every other fact goes instead.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Writing model-facing text

Every docstring and `description=` a FastMCP server registers is sent to
the client on the wire, and most of it reaches the model as a hint at one
moment: when it chooses a tool and fills in the arguments. Text the model
cannot act on at that moment is not harmless. It costs context on every
turn, Claude Code cuts each tool description and each server's
instructions at 2,048 characters and keeps the start, and a model told
about an operator command or a resource it cannot fetch acts on it anyway.
The evidence for every rule here is in
`docs/design/reference/mcp-model-facing-text.md`; read it before arguing
with a rule.

## Who reads each surface

| Surface | Reader | Read when | What clients do with it |
| --- | --- | --- | --- |
| Tool description | the model | choosing a tool, filling arguments | put in the system prompt; Claude Code truncates at 2,048; tool search indexes it |
| Parameter description | the model | filling that one argument | property in the JSON schema; tool search indexes it |
| Tool `title`, annotations | the client | rendering a label, deciding whether to confirm | hints only; spec says clients must treat them as untrusted |
| Server instructions | the model | session start | system prompt; the one text Claude Code loads before any tool under tool search; truncated at 2,048; spec: should not duplicate tool descriptions |
| Resource description | a human in a picker, sometimes the model | browsing or attaching context | application-driven: the model may never see the list and often cannot fetch it |
| Prompt and prompt-argument descriptions | a human in a slash-command picker | picking a prompt | user-controlled |
| Tool result and error text | the model | after the call | the only surface that can say what to do next given the state |
| Skill served over MCP (SEP-2640) | the model, after the host lists its name and description and the model asks to load it | on demand, only on a host that implements the extension | `skill://` resources; other clients see plain resources; hosts treat the body as untrusted data the user may refuse to load |

## The two gates

Every sentence passes both or moves.

1. **Actionable by the model at the moment it chooses or fills in this
   call.** A CLI command, an environment variable, a URL, a timeout
   default, an exception class, a framework name or how the code works
   fails this gate: the model cannot run, set, open or catch any of them.
2. **Said once, in the surface read at that moment.** A fact the model
   needs for one argument lives in that parameter's description, not also
   in the tool description and again in instructions.

## Where each fact goes

| Fact | Goes in |
| --- | --- |
| What the tool does and what comes back | tool description, first sentence |
| When to choose it over a sibling tool | tool description, second sentence |
| What one argument means, its format, what the default does | that parameter's description |
| A value that must come from an earlier call (an etag from `read`) | that parameter's description, one sentence |
| A sequence that spans tools, or what this server is for | an instructions snippet, `WORKFLOWS` or `CAPABILITIES` role |
| A fact about this deployment (read-only, which instance) | instructions `INSTANCE` role, driven by config, never hand-written prose |
| What to do after the call given its outcome (index stale, task queued) | the result or the error text of that call |
| Side effects: read-only, destructive, idempotent | `annotations=`; plus one clause in the description only when it changes the choice |
| Operator configuration, env vars, CLI commands, limits | `docs/configuration.md` and the operator guides |
| How it is implemented, links to framework docs, `Returns:`, `Raises:` | a `#` comment, or a docstring section FastMCP strips |
| Policy the client enforces (confirm before deleting) | nowhere on the server: the client owns the human in the loop |
| A long procedure, worked examples, a workflow that spans many calls | a skill served over MCP (SEP-2640; FastMCP's `SkillsDirectoryProvider`), pointed at from instructions; never the only home of a fact a call depends on |

## Recipe: a tool

The docstring is the description. Its parts, in order:

1. One sentence: verb, object, what comes back. `Search notes by keyword
   and meaning; returns paths, titles and snippets ranked by score.`
2. One sentence on when to choose it over its siblings, only if a sibling
   exists. `Use read for a single known path.`
3. One constraint the model must honour for the call to succeed that no
   parameter carries. Optional.

Then an `Args:` section with one sentence per parameter: meaning, format,
what omitting it does. `if_match: Etag from read; omit for a new file.`
Use `Field(description=...)` instead when the entry needs a constraint the
schema should also carry. Set `annotations={"title": ..., "read_only_hint":
...}` on every tool.

Two things FastMCP 4.0.5 does that change how you write:

- A docstring with no `Args:` entry ships **verbatim**, `Returns:` and
  `Raises:` included. A parameterless tool therefore passes
  `description=` on the decorator, or keeps a one-line docstring with no
  sections.
- A resource docstring is never parsed. Keep it to one line.

## Recipe: an instructions snippet

`instructions_for(mcp).add(text, role=InstructionRole.WORKFLOWS,
requires_tools=(...))` in the `DOMAIN-WIRING` block. Two sentences at
most: the sequence that spans tools, naming them. Under tool search the
model sees this text before any tool description, so it says what the
server is for and which tool starts a task; it never restates a tool's
own description. pvl-core warns above 1,536 units for generated text and
drops the snippet when a required tool is hidden, so name the tools in
`requires_tools`.

## Skills served over MCP

SEP-2640 (Final) ships a skill directory as `skill://<name>/SKILL.md`
resources, and FastMCP's `SkillsDirectoryProvider` does the same today
under a URI shape the working group is still reconciling. A host that
implements it shows the model the skill's name and description and loads
the body only when the model asks; any other client sees ordinary
resources, and every host treats the body as untrusted text the user may
decline to load. So a skill is where the long workflow goes, and its
frontmatter description follows the tool-description recipe above. It is
never where the one sentence a call depends on goes: that stays in the
description, the parameter or the result, and instructions may name the
skill's URI for hosts that can read it.

The test case is markdown-vault-mcp's PARA guide
(`docs/guides/para.md`, about 4,500 words): a folder layout, a frontmatter
schema per note type, a five-stage capture-to-archive loop and the tool
sequence for each stage. No call depends on any of it, so none of it
belongs in a tool description or in instructions, which already cannot
hold it; the model needs all of it the moment a user says "run my PARA
vault". That is a skill: `skills/para/SKILL.md` served by the provider,
one instructions sentence naming its URI, and the tool descriptions
unchanged. The same holds for a Zettelkasten or research-workflow guide.

## Recipe: a resource or prompt

- Resource: one line saying what the resource contains, written for a
  human scanning a list. Never an instruction to the model ("read this
  before writing"): if the model must know it, put it where the model
  reads, a tool result or an instructions snippet.
- Prompt: one line saying what the prompt produces; each argument one
  clause in `Args:`, annotated as a bare `str`. FastMCP 4.0.5 appends a
  JSON-schema sentence to any argument annotated otherwise (`Annotated`,
  `Literal`, `str | None` included), and under
  `from __future__ import annotations` to every argument, so keep that
  import out of the prompts module.

## Worked example

Before, the same etag rule in three places:

```python
@mcp.tool(annotations={"read_only_hint": False})
async def save_recipe(path: str, content: str, if_match: str | None = None):
    """Create a new recipe or overwrite an existing one.

    To change an existing recipe: call read_recipe first, edit the text it
    returned, and pass its etag as if_match. If the file changed in the
    meantime the call fails with a conflict error and nothing is written;
    re-read, re-apply your edit, and save again with the new etag. Never
    retry a conflict by dropping if_match.

    Args:
        if_match: Etag from read_recipe. Required when updating an
            existing recipe; omit only when creating a new file.
    """


instructions_for(mcp).add(
    "To edit a recipe: read_recipe, modify, save_recipe with the etag as "
    "if_match. On a conflict, re-read and re-apply; never retry without "
    "if_match.",
    role=InstructionRole.WORKFLOWS,
    requires_tools=("read_recipe", "save_recipe"),
)
```

After, each fact once, where it is read:

```python
@mcp.tool(annotations={"title": "Save Recipe", "read_only_hint": False})
async def save_recipe(path: str, content: str, if_match: str | None = None):
    """Create or replace a recipe file; returns its path and new etag.

    Args:
        path: Vault-relative path ending in .md.
        content: Complete Markdown for the file, frontmatter included.
        if_match: Etag from read_recipe; omit for a new file.
    """
```

The conflict error's text says "re-read and retry with the new etag"; the
instructions snippet is gone because nothing spans tools.

## Measure it

Print what the client receives, not what the source says:

```python
mcp = make_server()  # outside any event loop
async with Client(mcp) as c:
    for t in await c.list_tools():
        print(t.name, utf16_code_units(t.description or ""), t.description)
```

`tests/test_model_facing_text.py` (template-owned) fails on a leaked
docstring section in any description, a tool with no description, a `str`
prompt argument carrying the JSON-schema sentence, and a tool description
or the instructions over 2,048 units. A project with many tools adds a budget
test like markdown-vault-mcp's `tests/test_client_surface_budget.py`.

## Review checklist

- Read each description as the model: is every sentence something it can
  act on when choosing this call?
- Is each fact in exactly one surface?
- Any URL, env var, CLI command, exception class or framework name? Move it.
- Does a resource or prompt description instruct the model? Move it.
- Does an instructions snippet restate a tool description? Cut it.
- Does a parameterless tool, or a resource, ship a docstring section?
