---
type: Reference
title: Claude as an MCP client — tasks, elicitation, skills and file transfers
description: What Claude and Claude Code expect of an MCP server that returns tasks, asks for input, serves skills or transfers files, on protocol revision 2026-07-28
subject_version: "Claude Code docs and Claude connector docs as read on 2026-11-27; MCP tasks, skills and files extensions (2026-07-28 revision)"
valid_for: "Claude Code 2.1.x and MCP protocol revision 2026-07-28; re-research on a new protocol revision or when file transfers leave beta"
generated:
  by: process:researching-references
  at: 2026-11-27T21:45:00+01:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-27T22:00:00+01:00
stale_after: 2027-05-27T00:00:00+00:00
status: stable
sources:
  - id: cc-mcp
    title: Claude Code docs, Connect Claude Code to tools via MCP
    resource: https://code.claude.com/docs/en/mcp
    accessed: 2026-11-27
  - id: claude-files
    title: Claude connector docs, Building connectors, Files
    resource: https://claude.com/docs/connectors/building/files
    accessed: 2026-11-27
  - id: announcement
    title: "MCP servers can now ship skills, run tasks, and more with Claude (24 November 2026)"
    resource: https://claude.com/resources/articles/skills-over-mcp-tasks-and-more
    accessed: 2026-11-27
  - id: ext-tasks
    title: MCP extensions, Tasks overview
    resource: https://modelcontextprotocol.io/extensions/tasks/overview
    accessed: 2026-11-27
  - id: ext-skills
    title: MCP extensions, Skills overview (SEP-2640)
    resource: https://modelcontextprotocol.io/extensions/skills/overview
    accessed: 2026-11-27
  - id: ext-files
    title: MCP experimental extension, File uploads and downloads (draft specification and README)
    resource: https://github.com/modelcontextprotocol/experimental-ext-file-uploads/blob/main/specification/draft/file-uploads.md
    accessed: 2026-11-27
---

# Claude as an MCP client — tasks, elicitation, skills and file transfers

How Claude (claude.ai chats, the desktop app) and Claude Code treat four MCP
extensions this server may use: tasks, elicitation, skills and file
transfers. It records what the client sends, what it expects back, and where
each feature is available. It leaves out the protocol as a whole, MCP Apps and
tool search.

## Scope

- Covers: task negotiation, retention and polling; elicitation modes; skill
  serving and Claude's limits on served skills; the file-transfer extension
  and Claude's conditions for using it.
- Does not cover: FastMCP's implementation of these extensions (see
  [FastMCP 4](fastmcp-4.md)), MCP Apps, OAuth.
- Depended on by: `src/markdown_vault_mcp/_tasks.py` (task retention),
  `src/markdown_vault_mcp/_tools/writer.py` (the `okf_verify` elicitation),
  `src/markdown_vault_mcp/_skills.py` (served skills),
  `docs/design/design.md` § the task backend paragraph, § Skills served
  over MCP and § One-Time Transfer Links.

## Claims

### Tasks

- The tasks extension is `io.modelcontextprotocol/tasks`. A task's status is
  one of `working`, `input_required`, `completed`, `failed` and `cancelled`;
  the last three are terminal. Clients use `tasks/get`, `tasks/update` and
  `tasks/cancel`. [source: ext-tasks]
- On a protocol revision 2026-07-28 connection, Claude Code puts its client
  capabilities in each request at
  `params._meta["io.modelcontextprotocol/clientCapabilities"]`, with
  `"io.modelcontextprotocol/tasks": {}` under `extensions`. A `tools/call`
  that cannot become a task omits the extension. [source: cc-mcp]
- A server answers with a task only when the request carries the extension;
  otherwise the call fails with the tool error `Unsupported result type 'task'
  for tools/call`. [source: cc-mcp]
- MCP tasks need Claude Code v2.1.304 or later. Some uses need later
  versions: v2.1.331 for subagent calls, the VS Code extension and the
  desktop app. [source: cc-mcp]
- Claude asks servers to "Keep a finished task's result for at least an
  hour, and longer if you can", because a person can exit while a task runs,
  resume later, and then gets the result only if the server still knows the
  task. [source: cc-mcp] [pins: tests/test_tasks.py::TestRetention::test_default_outlasts_upstream_quarter_hour]
- `ttlMs` reports how long the server keeps the task, counted from the
  task's creation: the longest run time plus the time the result is kept, or
  `null` for no limit. Claude Code does not drop a task because its `ttlMs`
  has passed. [source: cc-mcp] [pins: tests/test_tasks.py::TestRetention::test_client_receives_retention_as_ttl_ms]
- `pollIntervalMs` guidance is 10000 to 30000 for a job that runs longer than
  a minute and 2000 for a shorter one. Outside the cloud, Claude Code clamps
  it to 250–30000 and uses 2000 when it is absent or `null`. [source: cc-mcp]
- In cloud sessions and claude.ai chats, status checks can come less often
  than `pollIntervalMs` asks: a new task's interval is raised to at least
  1000 ms, and an omitted one means at most 10 seconds between checks. Checks
  on an older task slow to every 10 seconds after a minute and every
  30 seconds after 10 minutes. [source: cc-mcp]
- For an HTTP, SSE or claude.ai connector server, a per-request timer covers
  each request up to the server's first response byte. It is the greatest of
  60 seconds, the server's tool timeout and `MCP_TIMEOUT`. On an HTTP or
  connector server the task answer has to start within it, so the server
  accepts the job and answers before doing the work. [source: cc-mcp]
- A claude.ai connector in the desktop app gets no tasks in local sessions.
  In cloud sessions, servers from `.mcp.json` or a plugin get no tasks, while
  claude.ai connectors that support tasks on revision 2026-07-28 do.
  [source: cc-mcp]

### Elicitation

- Tasks and elicitation are generally available in Claude and Claude Code
  for Pro, Max and Ultra plans, and rolling out to Team and Enterprise
  organizations. A server can pause to ask the user for input through a form
  or a link. [source: announcement]
- Claude Code shows a form-mode request as a dialog with the server's form
  fields, and asks whether to open a URL-mode request's link in the browser.
  On revision 2026-07-28 connections it declares `elicitation: {form: {},
  url: {}}`. [source: cc-mcp] [pins: tests/test_okf_write.py::TestOkfVerifyElicit::test_writes_on_affirmative_elicitation, tests/test_okf_write.py::TestOkfVerifyElicit::test_legacy_connection_uses_server_initiated_elicitation]

### Skills

- The skills extension is `io.modelcontextprotocol/skills` (SEP-2640,
  Final). A server that declares it must implement `skills/list` and
  `skills/get`, and serves skill files through `resources/read`.
  [source: ext-skills] [pins: tests/test_served_skills.py::test_skills_list_names_the_bundled_skills, tests/test_served_skills.py::test_skill_file_reads_back_byte_for_byte]
- A `SKILL.md` must begin with YAML frontmatter holding `name` and
  `description`, and the last segment of its parent directory's path must
  match `name`. Servers should not exceed 512 files or 16 MiB per skill.
  [source: ext-skills] [pins: tests/test_served_skills.py::test_skills_list_names_the_bundled_skills]
- In Claude Code, MCP skills are in public beta and need v2.1.331 or later. A
  skill's name is `<server>:<skill>`; connector and plugin servers add a
  prefix, such as `claude_ai_Docs:pdf`. [source: cc-mcp]
- Skills work for claude.ai connectors, servers the user adds, and servers
  from plugins. Each server shows up to 100 skills. [source: cc-mcp]
- A scanned skill is limited to 100 KB per text file and 400 KB in total.
  [source: cc-mcp] [pins: tests/test_served_skills.py::test_served_skill_fits_claudes_scan_limits]
- MCP skills are not supported in cloud sessions, `claude -p`, `claude
  remote-control` server mode, GitHub Actions or the Agent SDK, nor for
  servers in the desktop app. On Team and Enterprise plans they are off until
  the organization turns them on. [source: cc-mcp]
- Claude warns that "Servers can change skills at any time": a served skill
  follows the server's version, not the client's. [source: cc-mcp]

### File transfers

- The file-transfer extension is `io.modelcontextprotocol/files`. Its README
  says it is experimental, not part of the MCP specification and not yet
  proposed as a SEP. The draft says the working group plans a SEP once a
  reference implementation exists. [source: ext-files]
- A tool marks a file argument with `x-mcp-file` in its `inputSchema`. An
  out-of-band upload is requested with a result of `resultType:
  "upload_required"`. Servers must not reject inline `data` because a
  request did not declare the extension. [source: ext-files]
- Claude transfers files only for connectors added from the Connectors
  Directory whose address exactly matches the listing. A server added by URL
  is not linked: "Claude sends it no files and removes any files from its
  tools' results. Your server receives no error." [source: claude-files]
- Anthropic must approve the hosts that store a connector's files, at least
  one even when every file goes through the MCP server. File transfers are in
  beta. [source: claude-files]
- Claude does not send inline `data` in a call that declares `upload`.
  [source: claude-files]
- Claude's documentation says FastMCP 4.0.10 turns an upload request that a
  tool returns into an ordinary result, so Claude sends no file.
  [source: claude-files]

## Where this project departs from the subject

- Task retention defaults to two hours from creation rather than the
  upstream 15 minutes, which is shorter than Claude asks for; see
  `docs/design/design.md`, the task backend paragraph (#2529).
- File transfers keep the server's own one-time upload and download links
  (`docs/design/design.md` § One-Time Transfer Links). Self-hosted vaults
  are added by URL, so Claude would send them no files through the
  extension.

## Not covered

- Whether claude.ai chats answer a revision 2026-07-28 `input_required`
  elicitation from a connector the same way Claude Code does. A live
  `okf_verify` call from claude.ai would settle it.
- Whether FastMCP 4.2.6 can carry an `upload_required` result. The vendor
  sentence above names 4.0.10 only.
