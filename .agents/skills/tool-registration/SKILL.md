---
name: tool-registration
description: >-
  Use when adding, renaming, or documenting an MCP tool: the registration checklist, the get_server_info tool, tool icons, and the public import-surface guard.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Tool Registration

## Tool Registration Checklist

Every MCP tool you register must carry the full set of metadata below — not just the behaviour. A tool that works but lacks a title, hints, or docs is incomplete. When adding or changing a tool, verify each item:

- **Title** — a human-readable `annotations.title` (e.g. `"Search Vault"`). Title-aware clients (notably VS Code, which honours only `title` and `readOnlyHint` among annotations) render this as the tool's label; without it they fall back to the raw machine name. Set it inline in the tool's `annotations={...}` dict.
- **Behavioural hints** — `readOnlyHint`, and where they apply `destructiveHint` / `idempotentHint`, in the same `annotations` dict. These describe side effects accurately (a destructive tool must set `destructiveHint=True`).
- **Icon** — an entry wired via `register_tool_icons(...)` or `@mcp.tool(icons=[...])` (see [Tool icons](#tool-icons)).
- **Docstring** — a Google-style docstring; FastMCP surfaces it as the tool description and per-parameter docs.
- **Docs entry** — a row in your published tools reference (e.g. `docs/tools/index.md`) so the tool is documented for users (per the Documentation Discipline section in `AGENTS.md`).
- **Enforcement test** — keep a test that enumerates the registered tools and asserts each carries the metadata above (at minimum a non-empty `annotations.title`). Enumerate the *full* registry, not just the client-facing listing, so app-only / hidden tools cannot slip past. Such a test turns this checklist into a CI gate: a future tool added without a title fails loudly rather than silently shipping its machine name.

### Tool icons

Drop SVG / PNG / ICO / JPEG files into `src/markdown_vault_mcp/static/icons/` and bulk-attach them to registered tools via `fastmcp_pvl_core.register_tool_icons(mcp, {"tool_name": "filename.svg"}, static_dir=...)` at the end of `register_tools()` — or attach at decoration time with `@mcp.tool(icons=[make_icon(STATIC / "x.svg")])` (where `STATIC = Path(__file__).parent / "static" / "icons"` is a shorthand you define at module level). The scaffold ships an empty `static/icons/` directory; commented-out wiring lives in `tools.py`.

## Server Info Tool (`get_server_info`)

`make_server()` registers `get_server_info` (via `fastmcp_pvl_core.register_server_info_tool`) so operators can answer "is the latest fix actually deployed?" with a single MCP call. The default response carries `server_name`, `server_version`, and `core_version`.

For services that talk to a remote upstream (e.g. paperless, an HTTP API), wire the upstream version inside the `DOMAIN-UPSTREAM-START` / `DOMAIN-UPSTREAM-END` sentinel in `src/markdown_vault_mcp/server.py`. Pass `upstream_version=` (a zero-arg callable returning a dict / str / None) and optionally `upstream_label="<service>"` (default `"upstream"`). The simplest pattern is a module-level upstream client (typically constructed from env vars at import time) whose version method is referenced from the callable — `CurrentContext()` is a FastMCP DI marker that only resolves inside parameter defaults, so it cannot be called directly from a zero-arg provider. The block is preserved across `copier update`.

## Public import surface guard

`tests/test_import_surface.py` (template-owned, re-rendered on template updates) asserts the set of public names importable from the `markdown_vault_mcp` package root against the project-owned snapshot `tests/public_import_surface.txt` (seeded once, never re-rendered by template updates — yours). The surface is enumerated in a fresh interpreter — every non-underscore name in `dir(package)` or `__all__` that resolves via `getattr` — so lazy `__getattr__` re-exports count, incidental submodule imports from earlier tests do not, and a root holding only a docstring and `__version__` has an empty surface. When the test fails:

- **A name disappeared** — that is a breaking change to the public library interface. Either restore the name, or regenerate the snapshot (`uv run python tests/test_import_surface.py --update`) **and** mark the commit/PR breaking (`feat!:` / `fix!:`, or a `BREAKING CHANGE:` footer) per the versioning policy's public-library-interface tier (pvliesdonk/fastmcp-server-template#342).
- **A name appeared** — not breaking; regenerate the snapshot and commit it alongside the change, so the snapshot diff stays the reviewable record of every surface change.

The guard covers the package root only — submodule paths, env vars, and CLI flags are out of its scope.
