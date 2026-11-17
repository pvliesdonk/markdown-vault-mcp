---
type: Reference
title: MCP Apps font loading under CSP
description: Binary font faces and the MCP Apps example content security policy.
subject_version: "MCP Apps specification and CSS Font Loading Level 3, 2026-11-16"
valid_for: "Recheck when the MCP Apps CSP example or browser font loading changes."
generated:
  by: process:researching-references
  at: 2026-11-16T00:00:00+00:00
stale_after: 2027-05-16T00:00:00+00:00
sources:
  - id: mcp
    title: MCP Apps security and CSP construction
    resource: https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx
    accessed: 2026-11-16
  - id: font-loading
    title: CSS Font Loading Module Level 3
    resource: https://drafts.csswg.org/css-font-loading/
    accessed: 2026-11-16
  - id: newsreader
    title: Fontsource Newsreader package
    resource: https://registry.npmjs.org/@fontsource-variable/newsreader/-/newsreader-5.3.0.tgz
    accessed: 2026-11-16
  - id: public-sans
    title: Fontsource Public Sans package
    resource: https://registry.npmjs.org/@fontsource-variable/public-sans/-/public-sans-5.3.0.tgz
    accessed: 2026-11-16
  - id: plex
    title: IBM Plex Mono package
    resource: https://registry.npmjs.org/@ibm/plex-mono/-/plex-mono-2.5.0.tgz
    accessed: 2026-11-16
---

# MCP Apps font loading under CSP

- The MCP Apps example builds `font-src 'self'` plus declared resource domains; it does not add `data:`. [source: mcp]
- The `FontFace` constructor accepts binary font data as well as a URL source. [source: font-loading]
- Chromium loads five binary faces under `font-src 'self'` with no extra requests or CSP violations; a data URL font is blocked. The test permits `data:` and `unsafe-eval` for scripts because the existing module and browser instrumentation need them. [observed: tests/test_vault_apps_accessibility.py::test_embedded_fonts_load_under_strict_csp] [pins: tests/test_vault_apps_accessibility.py::test_embedded_fonts_load_under_strict_csp]
- Fontsource Newsreader 5.3.0 and Public Sans 5.3.0 use OFL 1.1 without a reserved name; IBM Plex Mono 2.5.0 uses OFL 1.1 and reserves "Plex". [source: newsreader] [source: public-sans] [source: plex]

The project's chosen files and fallback behavior are in the [design](../design.md#mcp-apps).
