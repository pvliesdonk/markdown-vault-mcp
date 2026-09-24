---
type: Reference
title: MCP model-facing text
description: Who reads each MCP description field, how clients consume and cut it, how FastMCP turns Python into those fields, and what vendors say a description should carry.
subject_version: "MCP 2026-07-28 (legacy 2025-11-25); FastMCP 4.0.5; mcp 2.2.0; fastmcp-pvl-core 9.0.0; client docs as of 2026-09-23"
valid_for: "MCP 2026-07-28 and FastMCP 4.x; client behaviour as of 2026-09"
generated:
  by: process:researching-references
  at: 2026-09-23T19:45:00+02:00
stale_after: 2027-03-23T00:00:00+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-23T20:30:00+02:00
status: stable
sources:
  - id: mcp-tools
    title: MCP specification 2026-07-28, Tools
    resource: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
    accessed: 2026-09-23
  - id: mcp-resources
    title: MCP specification 2026-07-28, Resources
    resource: https://modelcontextprotocol.io/specification/2026-07-28/server/resources
    accessed: 2026-09-23
  - id: mcp-prompts
    title: MCP specification 2026-07-28, Prompts
    resource: https://modelcontextprotocol.io/specification/2026-07-28/server/prompts
    accessed: 2026-09-23
  - id: mcp-schema
    title: MCP specification 2026-07-28, schema reference
    resource: https://modelcontextprotocol.io/specification/2026-07-28/schema
    accessed: 2026-09-23
  - id: mcp-schema-legacy
    title: MCP specification 2025-11-25, schema reference
    resource: https://modelcontextprotocol.io/specification/2025-11-25/schema
    accessed: 2026-09-23
  - id: mcp-changelog
    title: MCP specification 2026-07-28, changelog
    resource: https://modelcontextprotocol.io/specification/2026-07-28/changelog
    accessed: 2026-09-23
  - id: claude-define-tools
    title: Claude Developer Platform, Define tools
    resource: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
    accessed: 2026-09-23
  - id: claude-tool-use
    title: Claude Developer Platform, Tool use with Claude
    resource: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
    accessed: 2026-09-23
  - id: claude-tool-search
    title: Claude Developer Platform, Tool search tool
    resource: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
    accessed: 2026-09-23
  - id: claude-mcp-connector
    title: Claude Developer Platform, MCP connector
    resource: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
    accessed: 2026-09-23
  - id: anthropic-writing-tools
    title: Anthropic engineering, Writing effective tools for agents, with agents
    resource: https://www.anthropic.com/engineering/writing-tools-for-agents
    accessed: 2026-09-23
  - id: anthropic-context
    title: Anthropic engineering, Effective context engineering for AI agents
    resource: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
    accessed: 2026-09-23
  - id: claude-code-mcp
    title: Claude Code docs, Connect Claude Code to tools via MCP
    resource: https://code.claude.com/docs/en/mcp
    accessed: 2026-09-23
  - id: claude-code-env
    title: Claude Code docs, Environment variables
    resource: https://code.claude.com/docs/en/env-vars
    accessed: 2026-09-23
  - id: openai-functions
    title: OpenAI, Function calling guide
    resource: https://developers.openai.com/api/docs/guides/function-calling
    accessed: 2026-09-23
  - id: openai-developer-mode
    title: OpenAI, ChatGPT Developer mode
    resource: https://developers.openai.com/api/docs/guides/developer-mode
    accessed: 2026-09-23
  - id: openai-apps-metadata
    title: OpenAI Apps SDK, Optimize metadata
    resource: https://developers.openai.com/apps-sdk/guides/optimize-metadata
    accessed: 2026-09-23
  - id: openai-mcp-tool
    title: OpenAI, MCP servers (Responses API hosted tool)
    resource: https://developers.openai.com/api/docs/guides/tools-connectors-mcp
    accessed: 2026-09-23
  - id: gemini-functions
    title: Google, Function calling with the Gemini API
    resource: https://ai.google.dev/gemini-api/docs/function-calling
    accessed: 2026-09-23
  - id: vscode-tools
    title: VS Code docs, Use tools with agents
    resource: https://code.visualstudio.com/docs/agents/run/tools
    accessed: 2026-09-23
  - id: vscode-mcp
    title: VS Code docs, Add and manage MCP servers
    resource: https://code.visualstudio.com/docs/agent-customization/mcp-servers
    accessed: 2026-09-23
  - id: vscode-1104
    title: VS Code release notes, August 2025 (1.104)
    resource: https://code.visualstudio.com/updates/v1_104
    accessed: 2026-09-23
  - id: cursor-mcp
    title: Cursor docs, Model Context Protocol
    resource: https://cursor.com/docs/mcp
    accessed: 2026-09-23
  - id: claude-desktop-local
    title: Claude Help Center, Getting started with local MCP servers on Claude Desktop
    resource: https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
    accessed: 2026-09-23
  - id: claude-desktop-connectors
    title: Claude Help Center, Get started with custom connectors using remote MCP
    resource: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
    accessed: 2026-09-23
  - id: fastmcp-tools
    title: FastMCP docs, Tools
    resource: https://gofastmcp.com/servers/tools
    accessed: 2026-09-23
  - id: fastmcp-resources
    title: FastMCP docs, Resources and templates
    resource: https://gofastmcp.com/servers/resources
    accessed: 2026-09-23
  - id: fastmcp-prompts
    title: FastMCP docs, Prompts
    resource: https://gofastmcp.com/servers/prompts
    accessed: 2026-09-23
  - id: fastmcp-server
    title: FastMCP docs, The FastMCP server
    resource: https://gofastmcp.com/servers/server
    accessed: 2026-09-23
  - id: fastmcp-tool-search
    title: FastMCP docs, Tool search transform
    resource: https://gofastmcp.com/servers/transforms/tool-search
    accessed: 2026-09-23
  - id: fastmcp-upgrade
    title: FastMCP docs, Upgrading from FastMCP 3
    resource: https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3
    accessed: 2026-09-23
  - id: fastmcp-4952
    title: FastMCP issue 4952, parameterless tools expose Returns as part of their description
    resource: https://github.com/PrefectHQ/fastmcp/issues/4952
    accessed: 2026-09-23
  - id: pvl-core-instructions
    title: fastmcp-pvl-core, _instructions.py (composable server instructions)
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/main/src/fastmcp_pvl_core/_instructions.py
    accessed: 2026-09-23
  - id: mvm-budget
    title: markdown-vault-mcp, tests/test_client_surface_budget.py
    resource: https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/tests/test_client_surface_budget.py
    accessed: 2026-09-23
  - id: sep-2640
    title: SEP-2640, Skills Extension (Final, Extensions Track)
    resource: https://modelcontextprotocol.io/seps/2640-skills-extension
    accessed: 2026-09-23
  - id: fastmcp-skills
    title: FastMCP docs, Skills provider
    resource: https://gofastmcp.com/servers/providers/skills
    accessed: 2026-09-23
  - id: mvm-design
    title: markdown-vault-mcp, docs/design/design.md (client-facing surface budget)
    resource: https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/docs/design/design.md
    accessed: 2026-09-23
---

# MCP model-facing text

Every description a FastMCP server registers is sent to a client, and most
of it reaches a model as a hint at the moment it chooses a call. This page
records who reads each field, what clients do with it (truncate, index,
show to a human), how FastMCP turns a docstring into it, and what the model
vendors say a description should carry. The `writing-model-facing-text`
skill turns these facts into a recipe; this page holds the evidence.

## Scope

- Covers: the `description`, `title` and annotation fields of tools,
  resources, resource templates and prompts; server `instructions`; how
  Claude Code, the Claude API, VS Code, Cursor and ChatGPT consume them;
  FastMCP 4's docstring mapping; pvl-core's instructions builder; skills
  served over MCP (SEP-2640) as a place for long-form workflow prose.
- Does not cover: tool *result* design beyond the vendor guidance quoted;
  elicitation; MCP Apps UI resources; prompt caching.
- Depended on by: `src/<module>/tools.py`, `resources.py`, `prompts.py`
  (their docstrings are the wire descriptions), `server.py` (the
  instructions builder), `tests/test_model_facing_text.py`, and the
  `writing-model-facing-text` and `tool-registration` skills.

## Claims

### Who each field is for (the protocol)

- `Tool.description` is "a human-readable description of the tool. This can
  be used by clients to improve the LLM's understanding of available tools.
  It can be thought of like a 'hint' to the model." [source: mcp-schema]
- `Resource.description` and `ResourceTemplate.description` carry the same
  "hint to the model" sentence. [source: mcp-schema]
- `Prompt.description` is only "an optional description of what this prompt
  provides"; the schema gives it no "hint to the model" sentence.
  [source: mcp-schema]
- Tools are "model-controlled": "the language model can discover and invoke
  tools automatically based on its contextual understanding and the user's
  prompts". [source: mcp-tools]
- Resources are "application-driven, with host applications determining how
  to incorporate context based on their needs"; a host may expose them "for
  explicit selection", let the user search them, or "implement automatic
  context inclusion, based on heuristics or the AI model's selection". The
  protocol "does not mandate any specific user interaction model".
  [source: mcp-resources]
- Prompts are "user-controlled, meaning they are exposed from servers to
  clients with the intention of the user being able to explicitly select
  them for use", typically "through user-initiated commands in the user
  interface ... For example, as slash commands". [source: mcp-prompts]
- Server instructions (2026-07-28, `DiscoverResult.instructions`) are
  "natural-language guidance describing the server and its features. This
  can be used by clients to improve an LLM's understanding of available
  tools (e.g., by including it in a system prompt). It should focus on
  information that helps the model use the server effectively and should
  not duplicate information already in tool descriptions."
  [source: mcp-schema]
- The legacy field (2025-11-25, `InitializeResult.instructions`) reads
  "Instructions describing how to use the server and its features ... It
  can be thought of like a 'hint' to the model. For example, this
  information MAY be added to the system prompt." [source: mcp-schema-legacy]
- 2026-07-28 "removed the `initialize`/`notifications/initialized`
  handshake" and added `server/discover`; the spec calls 2026-07-28 and
  later "modern" and 2025-11-25 and earlier "legacy". [source: mcp-changelog]
- All `ToolAnnotations` properties "are hints. They are not guaranteed to
  provide a faithful description of tool behavior (including descriptive
  properties like `title`)", and "clients MUST consider tool annotations to
  be untrusted unless they come from trusted servers". [source: mcp-tools]
- Hint defaults: `readOnlyHint` false, `destructiveHint` true,
  `idempotentHint` false, `openWorldHint` true; the last three are
  "meaningful only when `readOnlyHint == false`". [source: mcp-schema]
- Tool display-name precedence is `title`, then `annotations.title`, then
  `name`; `name` is "intended for programmatic or logical use".
  [source: mcp-schema]
- Resource annotations are `audience` (`"user"`, `"assistant"`, or both),
  `priority` (0 "entirely optional" to 1 "effectively required") and
  `lastModified`; "clients can use these annotations to: filter resources
  based on their intended audience / prioritize which resources to include
  in context". [source: mcp-resources]
- The spec states no length limit for any `description`, `title` or
  `instructions`; the only length rule nearby is that tool names "SHOULD be
  between 1 and 128 characters". [source: mcp-tools]
- The spec's own design guidance puts a behavioural fact in the description
  when it changes the model's choice: "the server's retention policy should
  be stated in the creation tool's description (e.g., 'baskets expire after
  24 hours of inactivity') so the model can see it when deciding to create
  state". [source: mcp-tools]
- "Clients SHOULD provide tool execution errors to language models to
  enable self-correction", so error text is a model-facing surface too.
  [source: mcp-tools]

### What clients do with the text

- Claude Code "truncates each tool description and each server's
  instructions at 2,048 characters by default. Keep them concise, and put
  critical details near the start." The limit is
  `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` (v2.1.280+); "characters" are
  JavaScript string units, so pvl-core measures in UTF-16 code units.
  [source: claude-code-mcp] [source: claude-code-env]
  [source: pvl-core-instructions]
  [pins: tests/test_model_facing_text.py::test_descriptions_fit_the_claude_code_cut]
- Claude Code defers MCP tools by default: "only tool names and server
  instructions load at session start"; server authors should write
  instructions that say "what category of tasks your tools handle, when
  Claude should search for your tools, key capabilities your server
  provides". `ENABLE_TOOL_SEARCH=auto` loads tools upfront only while their
  definitions stay under 10% of the context window. [source: claude-code-mcp]
- In Claude Code, resources are fetched when a user types
  `@server:protocol://path`, and "Claude Code automatically provides tools
  to list and read MCP resources when servers support them"; prompts run as
  `/servername:promptname` with whitespace-split arguments.
  [source: claude-code-mcp]
- The Claude API builds a system prompt from tool definitions ("Here are
  the functions available in JSONSchema format"); tool names, descriptions
  and schemas are billed input tokens, plus a fixed tool-use preamble
  (286 tokens on Claude Opus 5.5). [source: claude-define-tools]
  [source: claude-tool-use]
- Tool search on the Claude API indexes "tool names, descriptions, argument
  names, and argument descriptions"; Anthropic recommends it at "10 or more
  tools" or "more than 10k tokens" of definitions, and says selection
  "degrades once you exceed 30-50 available tools". [source: claude-tool-search]
- The Claude API MCP connector supports "only tool calls"; prompts and
  resources need the client-side SDK helpers. [source: claude-mcp-connector]
- VS Code refuses a request with more than 128 tools, includes server
  instructions in its base prompt since 1.104, attaches resources through
  "Add Context > MCP Resources", and runs prompts as `/<server>.<prompt>`.
  [source: vscode-tools] [source: vscode-1104] [source: vscode-mcp]
- OpenAI's hosted MCP tool documents tool listing and calling only; the
  guide never mentions resources or prompts, and `allowed_tools` exists
  because "exposing many tools to the model can result in high cost and
  latency". [source: openai-mcp-tool]
- ChatGPT Developer mode asks for tool descriptions with "'Use this when...'
  guidance", a note of "disallowed/edge cases", and server instructions for
  "cross-tool guidance such as required tool sequences"; "keep the first
  512 characters self-contained". [source: openai-developer-mode]
- Cursor lists tools, prompts and resources as supported; its current docs
  state no tool-count cap. [source: cursor-mcp] The often-cited "first 40
  tools" limit is [unverified]: it appears only in forum threads, and a
  Cursor page or changelog entry would settle it.
- How Claude Desktop and claude.ai present resources and prompts, and
  whether their model can read a resource on its own, is [unverified]: the
  support articles describe only the connectors menu for tools
  [source: claude-desktop-local] [source: claude-desktop-connectors]; a
  support article naming the resource picker, or a session against a
  server that exposes one resource, would settle it.

### What the vendors say a description should carry

- Anthropic: the description is "by far the most important factor in tool
  performance" and should cover "what the tool does, when it should be used
  (and when it shouldn't), what each parameter means and how it affects the
  tool's behavior, any important caveats or limitations"; "aim for at least
  3-4 sentences for each tool description". [source: claude-define-tools]
- Anthropic's good example describes one tool in five sentences: what it
  retrieves, the accepted input, what it returns, when to use it, and what
  it does not return. The poor example is "Gets the stock price for a
  ticker." with an undescribed parameter. [source: claude-define-tools]
- Anthropic: "consolidate related operations into fewer tools", namespace
  names by service, and "return only high-signal information".
  [source: claude-define-tools]
- Anthropic: "think of how you would describe your tool to a new hire ...
  make it explicit"; name parameters unambiguously ("instead of a parameter
  named user, try a parameter named user_id"); "even small refinements to
  tool descriptions can yield dramatic improvements". [source: anthropic-writing-tools]
- Anthropic: "if a human engineer can't definitively say which tool should
  be used in a given situation, an AI agent can't be expected to do
  better"; the aim is "the smallest possible set of high-signal tokens".
  [source: anthropic-context]
- OpenAI: "explicitly describe the purpose of the function and each
  parameter (and its format), and what the output represents"; "use the
  system prompt to describe when (and when not) to use each function";
  "pass the intern test"; "don't make the model fill arguments you already
  know"; "combine functions that are always called in sequence"; "aim for
  fewer than 20 functions available at the start of a turn". Functions "are
  injected into the system message" and billed as input tokens.
  [source: openai-functions]
- OpenAI: for deferred tools "put detailed guidance in the function
  description and keep the namespace description concise". The API
  documents a 64-character maximum for a function name and no maximum for
  its description. [source: openai-functions]
- OpenAI Apps SDK: "start with 'Use this when...' and call out disallowed
  cases"; parameter docs "describe each argument, include examples, and use
  allowed values for constrained inputs". [source: openai-apps-metadata]
- Gemini: "Function and Parameter Descriptions: Be clear and specific";
  "Tool Selection: Keep active set to 10-20 tools maximum"; no description
  length limit is documented. [source: gemini-functions]

### How FastMCP builds the fields (4.0.5)

- A tool's description is "the free-form text above the `Args` section";
  `Returns`, `Raises` and `Example` sections "are excluded from the
  description". [source: fastmcp-tools] That holds only when the docstring
  has an `Args:` section that names at least one parameter: the parser
  keeps a parsed result only if it found parameter entries and otherwise
  returns the whole docstring. [observed: fastmcp 4.0.5, a `FastMCP` with three tools listed through
  `fastmcp.Client.list_tools()`: a parameterless tool and a one-parameter
  tool without `Args:` both shipped `Returns:` and `Raises:` verbatim; the
  same docstring with an `Args:` entry shipped summary and long paragraph
  only]
  [pins: tests/test_model_facing_text.py::test_descriptions_carry_no_docstring_sections]
- Issue 4952 reports that leak for parameterless tools; it is open, and the
  fix PRs were closed unmerged as of 2026-09-23. [source: fastmcp-4952]
- A resource or template description is the raw docstring with no parsing
  at all: `Args:` and `Returns:` reach the wire. [observed: the same listing,
  via `list_resources()` and `list_resource_templates()`, shipped the
  `Args:` and `Returns:` sections; `resources/function_resource.py` and
  `resources/template.py` call `inspect.getdoc` directly] [source: fastmcp-resources]
- A prompt follows the tool rule: parsed when `Args:` exists, raw
  otherwise; an argument annotated as anything but a bare `str` gets
  "Provide a value matching the following JSON schema: ... Encode
  non-string values as JSON." appended to its description, `Annotated[str,
  Field(...)]`, `Literal[...]` and `str | None` included. [observed: the
  same listing via `list_prompts()`, a `list[int]` argument; and a prompt
  with those three annotations, each shipping the sentence with its schema]
  [source: fastmcp-prompts] In a module with `from __future__ import
  annotations` the check sees the string `'str'`, not the type, and appends
  the sentence to a plain `str` argument too. [observed: fastmcp 4.0.5, the same
  `context: str` prompt argument with an `Args:` entry, listed from a
  module with and one without the import: "The text." alone without it,
  the sentence appended with it]
  [pins: tests/test_model_facing_text.py::test_prompt_arguments_carry_no_string_schema_note]
- `description=` on the decorator replaces the docstring description, and
  "docstring-derived parameter descriptions still apply". [source: fastmcp-tools]
- A parameter description comes from `Field(description=...)` or
  `Annotated[T, "text"]` first and from the docstring `Args:` entry only
  where the schema has none. Defaults appear as `default` and drop the
  parameter from `required`. [source: fastmcp-tools] [observed: the same listing]
- Parameters with a `Depends()` default, and `Context` parameters, are
  removed from the schema before it is built. [source: fastmcp-tools]
  `exclude_args` is gone in FastMCP 4; `Depends` is the replacement.
  [source: fastmcp-upgrade]
- Every tool gets a `title`: the decorator's `title=`, else
  `annotations.title`, else the name with underscores replaced and
  title-cased. Resources and prompts get no derived title. [observed: the
  same listing; a tool named `ping` with no title listed as "Ping"] [source: fastmcp-tools]
- Tool annotations are documented as a way to "communicate how tools behave
  to client applications without consuming token context in LLM prompts";
  FastMCP accepts a snake_case dict and emits camelCase. [source: fastmcp-tools]
- A resource `annotations=` dict is coerced to the protocol's
  `audience`/`priority`/`lastModified`; keys such as `readOnlyHint` are
  dropped silently. [observed: fastmcp 4.0.5, a resource
  registered with `annotations={"readOnlyHint": True}` listed with
  `annotations` `{}`] The FastMCP resources page still documents
  `readOnlyHint`/`idempotentHint` for resources. [source: fastmcp-resources]
- `FastMCP(instructions=...)` is sent as `InitializeResult.instructions` on
  a legacy session and `DiscoverResult.instructions` on a modern one; the
  FastMCP client reads either. [observed: fastmcp 4.0.5, `Client(mcp,
  mode="legacy")` negotiated 2025-11-25 and `mode="auto"` 2026-07-28, both
  returning the same `client.instructions`] [source: fastmcp-server]
- FastMCP's own tool-search transform indexes "tool names, descriptions,
  parameter names, and parameter descriptions". [source: fastmcp-tool-search]
- FastMCP publishes no page on writing descriptions; its only prose is that
  instructions "help clients (and the LLMs behind them) understand what your
  server does and how to use it effectively". [source: fastmcp-server]

### Skills served over MCP (SEP-2640)

- SEP-2640 "Skills Extension" is Final on the Extensions Track (created
  2026-04-23): "each file in a skill directory is exposed as an MCP
  resource, conventionally under the `skill://` URI scheme"; a skill "MUST
  contain a `SKILL.md` file at its root" whose frontmatter carries at least
  `name` and `description`; a server declares
  `io.modelcontextprotocol/skills` under `capabilities.extensions` and
  then MUST implement `skills/list` and `skills/get`. [source: sep-2640]
- Its motivation names the instructions ceiling: "Server instructions are
  delivered as the `instructions` field of the `server/discover` result
  and are practically bounded in size. Complex workflows, such as the
  875-line mcpGraph skill, do not fit this model." [source: sep-2640]
- The `SKILL.md` resource's `description` "SHOULD be set from the
  `description` field of the `SKILL.md` YAML frontmatter" and its
  `mimeType` SHOULD be `text/markdown`. [source: sep-2640]
- A host assembles a registry from `skills/list` reading "only the
  listing: the host MUST NOT fetch `SKILL.md`", surfaces each skill's
  `name` and `description` in the model's context, and fetches the body
  only when the model asks to load it: "the name and description tell the
  model what a skill is for". [source: sep-2640]
- "A server MAY direct the agent to specific skill URIs from its
  `instructions` field. This requires no discovery machinery on the host;
  the URI is simply present in the model's context and readable via
  `resources/read`." [source: sep-2640]
- A skill cannot be relied on: "a client that does not implement this
  extension sees `skill://` resources as ordinary resources", and
  "explicit user policy governs whether a skill is loaded at all".
  [source: sep-2640]
- Hosts "MUST treat MCP-served skill content as untrusted model input",
  "MUST NOT treat skill resources as higher-authority than other context",
  "MUST NOT allow MCP-served skill content to cause host-side code
  execution without explicit per-skill user approval", and MUST ignore
  `allowed-tools` for MCP-origin skills unless the user approved the
  grant. [source: sep-2640]
- Per-skill limits are 512 resources and 16 MiB in total; "servers SHOULD
  NOT serve a skill that exceeds either limit". [source: sep-2640]
- Host support as of 2026-09: prototypes in gemini-cli, codex and
  fast-agent; "Claude Code: prototyped internally at Anthropic; not yet
  public". [source: sep-2640]
- FastMCP's `SkillsDirectoryProvider` (`mcp.add_provider(...)`) exposes a
  skill directory's files as `skill://<name>/...` resources plus a
  synthetic `skill://<name>/_manifest` JSON listing sizes and SHA-256
  hashes, with the `SKILL.md` description taken from its frontmatter.
  [source: fastmcp-skills] The SEP records that this provider "diverges on
  URI structure, discovery (per-skill `_manifest` vs. central index), and
  metadata mapping; coordinating that migration is a near-term Working
  Group priority". [source: sep-2640]
- On fastmcp 4.0.5 the provider lists `skill://demo/SKILL.md` (description
  from the frontmatter, `text/markdown`), `skill://demo/_manifest` and a
  `skill://demo/{path*}` template, and declares no
  `io.modelcontextprotocol/skills` extension in its capabilities, so a
  SEP-2640 host sees only ordinary resources. [observed: fastmcp 4.0.5,
  `SkillsDirectoryProvider(roots=...)` over one skill directory, listed
  through `fastmcp.Client`: the negotiated capabilities carried
  `extensions: {"io.modelcontextprotocol/ui": {}}` alone]

### pvl-core's instructions builder and the measured surface

- `instructions_for(mcp)` composes snippets by `InstructionRole`; the
  render order is identity, routing, instance, policy, capabilities,
  workflows, documentation, and a snippet whose `requires_tools` are absent
  or operator-hidden is dropped at `finalize_instructions`. Contributors
  may use only `INSTANCE`, `CAPABILITIES` and `WORKFLOWS`.
  [source: pvl-core-instructions]
- pvl-core exports `utf16_code_units` and never truncates: it logs a
  warning when generated instructions cross their 1,536-unit target and
  another when the final text crosses 2,048; the design notes that "the
  MCP protocol does not define this limit". [source: pvl-core-instructions]
- markdown-vault-mcp measures its whole model-visible surface with a FastMCP
  client after parsing: at the 2026-09 baseline, 1,507 units of
  instructions, 22,853 of tool descriptions and 27,259 of input schemas,
  against a 58,500-unit ceiling; parameterless tools there pass an explicit
  `description=` because of the 4952 leak. [source: mvm-design]
  [source: mvm-budget]

## Where this project departs from the subject

- Anthropic asks for at least three or four sentences per tool; this
  project's skill asks for the sentences the model can act on when it
  chooses the call and nothing more, and treats Claude Code's 2,048-unit
  cut as the ceiling (decided in
  `.agents/skills/writing-model-facing-text/SKILL.md`, "The two gates" and
  "Recipe: a tool"). The two agree in practice: Anthropic's own good
  example is five short sentences and about 430 characters.
- SEP-2640 makes a skill the home for long workflow prose, and this project
  treats it as an addition only: a host may not implement the extension,
  may not load the skill, and must treat its content as untrusted, so every
  fact a call depends on stays in the description, the parameter or the
  result, and the skill carries what a capable client may load on top
  (decided in the same skill, "Skills served over MCP").
- OpenAI puts "when and when not to use" in the system prompt; on MCP that
  slot is server instructions, which the 2026-07-28 spec says should not
  duplicate tool descriptions. This project puts the when-to-choose sentence
  in the tool description and reserves instructions for what spans tools
  (decided in the same skill, "Where each fact goes").

## Not covered

- Whether a client that keeps only tool names at session start (Claude Code
  with tool search) needs a larger instructions "discovery seed" than the
  1,536-unit target; only an evaluation against such a client would settle
  it.
- How Claude Desktop, claude.ai and Cursor surface resources and prompts
  to the user or the model (see the [unverified] claims above).
- NumPy- and Sphinx-style docstrings: FastMCP tries those parsers too, but
  only Google style was probed.
