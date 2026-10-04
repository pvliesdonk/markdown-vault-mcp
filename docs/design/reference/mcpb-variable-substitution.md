---
type: Reference
title: MCPB bundle variable substitution and user_config defaults
description: Which ${...} variables an MCP Bundle (formerly DXT) host substitutes in mcp_config and in user_config defaults, how defaults merge with stored values, and whether a per-extension data directory exists
subject_version: "MCPB manifest 0.4; @anthropic-ai/mcpb 2.1.2 (2a788100) and main at 70fe3b34 (2026-04-22)"
valid_for: "MCPB manifest 0.x and @anthropic-ai/mcpb 2.x; re-check when a CLI release after 2.1.2 ships or Claude Desktop changes its extension loader"
generated:
  by: process:researching-references
  at: 2026-10-05T16:45:00+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-10-05T16:45:00+02:00
stale_after: 2027-04-05T16:45:00+02:00
status: stable
sources:
  - id: manifest-spec
    title: MCPB Manifest.json Spec, "Variable Substitution" and "User Configuration" (identical at v2.1.2 and main)
    resource: https://github.com/modelcontextprotocol/mcpb/blob/70fe3b34cd6dff1b3bba046638edc72a6467a4fb/MANIFEST.md
    accessed: 2026-10-05
  - id: readme
    title: MCPB README, statement that Claude for macOS and Windows uses this repository's code
    resource: https://github.com/modelcontextprotocol/mcpb/blob/70fe3b34cd6dff1b3bba046638edc72a6467a4fb/README.md
    accessed: 2026-10-05
  - id: config-v212
    title: src/shared/config.ts at v2.1.2 (replaceVariables, getMcpConfigForManifest, hasRequiredConfigMissing)
    resource: https://github.com/modelcontextprotocol/mcpb/blob/2a788100a60db19a6b1c018fb1cf84ae85de9537/src/shared/config.ts
    accessed: 2026-10-05
  - id: config-main
    title: src/shared/config.ts at main 70fe3b34
    resource: https://github.com/modelcontextprotocol/mcpb/blob/70fe3b34cd6dff1b3bba046638edc72a6467a4fb/src/shared/config.ts
    accessed: 2026-10-05
  - id: validate-main
    title: src/node/validate.ts at main 70fe3b34 (validateCommandVariables, added in f14863b, not in any release)
    resource: https://github.com/modelcontextprotocol/mcpb/blob/70fe3b34cd6dff1b3bba046638edc72a6467a4fb/src/node/validate.ts
    accessed: 2026-10-05
  - id: npm-mcpb
    title: "@anthropic-ai/mcpb on npm (dist-tag latest = 2.1.2)"
    resource: https://www.npmjs.com/package/@anthropic-ai/mcpb
    accessed: 2026-10-05
  - id: issue-217
    title: "mcpb#217 User Config Interpolation (closed); maintainer diagnosis of the platform_overrides env replacement"
    resource: https://github.com/modelcontextprotocol/mcpb/issues/217
    accessed: 2026-10-05
  - id: pr-221
    title: "mcpb#221 fix: merge platform_overrides env with base env instead of replacing (merged 2026-04-13, unreleased)"
    resource: https://github.com/modelcontextprotocol/mcpb/pull/221
    accessed: 2026-10-05
  - id: issue-250
    title: "mcpb#250 Claude Desktop: substitute user_config options with no value (open)"
    resource: https://github.com/modelcontextprotocol/mcpb/issues/250
    accessed: 2026-10-05
  - id: issue-251
    title: "mcpb#251 Claude Desktop: substitute ${HOME} in user_config defaults (open)"
    resource: https://github.com/modelcontextprotocol/mcpb/issues/251
    accessed: 2026-10-05
  - id: pr-252
    title: "mcpb#252 fix: resolve user config placeholders (open, unmerged)"
    resource: https://github.com/modelcontextprotocol/mcpb/pull/252
    accessed: 2026-10-05
---

# MCPB bundle variable substitution and user_config defaults

How an MCP Bundle host turns `server.mcp_config` and `user_config` into the
environment a server process receives: which `${...}` tokens it replaces,
where defaults come from, and what it leaves literal. Claude Desktop (macOS
and Windows) is closed source, but the MCPB README says it runs this
repository's loading code, so the library is the primary evidence and Claude
Desktop's own behaviour beyond it is marked where it is only reported. The
companion page for the other install channel is
[Claude Code plugin variable substitution](claude-code-plugin-variables.md).

## Scope

- Covers: the variable set in `mcp_config` and in `user_config` defaults, the
  default-versus-stored merge, unset optional fields, `platform_overrides`
  env merging, the CLI validator's variable rule, and whether a per-extension
  persistent directory exists.
- Does not cover: signing, packing layout, the `uv` runtime's environment
  creation, the `${arguments.*}` prompt templates, localization.
- Depended on by: `packaging/mcpb/manifest.json.in` (the `mcp_config.env`
  wiring and `user_config` defaults), `config-presentation.domain.yml` (the
  `${DOCUMENTS}/Vault` default), `packaging/mcpb/build.sh` (pins the CLI at
  2.1.2), `packaging/pre-release-checks.sh`.

Pins: no test in this repository asserts any claim below yet.

## Claims

### Which code Claude Desktop runs

- The MCPB repository provides "the code used by Claude for macOS and Windows
  to load and verify MCPB bundles" (pointing at `src/index.ts`), and Claude
  for macOS and Windows "uses the code in this repository". [source: readme]
- A maintainer's answer on mcpb#217 says a fix to `getMcpConfigForManifest`
  reaches users once "a new CLI version is published, Claude Desktop will pick
  it up in a subsequent update", and that there is no way to check from
  inside Claude Desktop which version it bundles. [source: issue-217]
- The latest published `@anthropic-ai/mcpb` is 2.1.2; `main` carries
  unreleased changes after the `v2.1.2` tag (the env-merge fix #221 and the
  variable validator f14863b among them). [source: npm-mcpb]
  [observed: `npm view @anthropic-ai/mcpb dist-tags versions`, `git log v2.1.2..main`]

### Variables in `mcp_config`

- The spec lists, for `mcp_config`: `${__dirname}` (the extension's
  directory), `${HOME}`, `${DESKTOP}`, `${DOCUMENTS}`, `${DOWNLOADS}`,
  `${pathSeparator}` or `${/}`, and `${user_config.KEY}`. [source: manifest-spec]
  § Server Configuration, "Variable Substitution"
- In the library, the substitution map is exactly `__dirname`,
  `pathSeparator`, `/`, the host-supplied `systemDirs` object spread in,
  then one `user_config.<key>` entry per merged option. `HOME`, `DESKTOP`,
  `DOCUMENTS` and `DOWNLOADS` are not named in the library; they exist only
  if the host passes them in `systemDirs`. [source: config-v212] lines 132-171
- Which keys Claude Desktop passes in `systemDirs` is not published; that it
  passes `HOME` is consistent with mcpb#216/#217 (`${HOME}` in a
  `platform_overrides.darwin.env.PATH` resolved on macOS) and mcpb#251
  (`${HOME}/AppData/...` in `mcp_config` "works correctly" on Windows), both
  reporter observations. [unverified] What would verify: a probe bundle whose
  `mcp_config.env` dumps each variable, installed in Claude Desktop on each OS.
- A `${NAME}` the map does not contain is left in the string verbatim, with
  no error or warning. [source: config-v212] `replaceVariables`
  [observed: node probe calling `getMcpConfigForManifest` from
  `@anthropic-ai/mcpb@2.1.2`: `${XDG_DATA_HOME}/x` came back unchanged]
- Substitution is one pass over the map in insertion order; a replacement's
  own `${...}` text is not re-scanned for variables that came earlier in the
  map. [source: config-v212] `replaceVariables` loop

### No per-extension data directory

- Neither the spec nor the library defines a variable for a per-extension
  persistent data directory; `${__dirname}` is the extension's install
  directory. [source: manifest-spec] [source: config-v212]
- Whether Claude Desktop keeps `${__dirname}` across an extension update or
  replaces it is not documented. [unverified] Writing state there is unsafe
  until it is.

### `user_config` defaults and the stored-value merge

- The spec says `default` "supports variable substitution" and lists, as
  "Available variables for default values", `${HOME}`, `${DESKTOP}` and
  `${DOCUMENTS}` only (not `${DOWNLOADS}`, `${__dirname}` or `${/}`).
  [source: manifest-spec] § User Configuration
- The library does not substitute variables inside a default. Defaults are
  copied into the merged config verbatim and become the `user_config.<key>`
  replacement, which is applied after the `systemDirs` entries, so a default
  of `${HOME}/x` reaches the server as the literal `${HOME}/x`.
  [source: config-v212] lines 142-177
  [observed: node probe on 2.1.2, `index_path` default `${HOME}/.cache/mvm`
  referenced as `${user_config.index_path}` in `env` gave `${HOME}/.cache/mvm`,
  while `${HOME}/.cache/mvm` written directly in `env` gave `/home/u/.cache/mvm`]
- Claude Desktop shows the same literal pass-through for an optional field
  left at its default, per mcpb#251 (open since 2026-05-29, no maintainer
  reply); PR #252, which would substitute system variables inside defaults,
  is open and unmerged. [source: issue-251] [source: pr-252] That Claude
  Desktop's settings UI never resolves a default before storing it is
  [unverified].
- For a field the stored settings lack, the library uses the manifest's
  current `default`: defaults are merged first and stored values override per
  key, so a field added in a new bundle version gets its default on an
  install that stored values only for the older fields. [source: config-v212]
  lines 142-154 [observed: node probe on 2.1.2, `userConfig: { source_dir: "/v" }`
  and a new `index_path` field with a default: `index_path` received the
  default]
- A `required` field is checked against the stored values only, before the
  merge: `hasRequiredConfigMissing` reads `userConfig`, not the merged
  config, so a required field's `default` never satisfies the check and the
  whole server config is skipped until the host stores a value.
  [source: config-v212] lines 124-130, 197-221. The `${DOCUMENTS}/Vault`
  default on this project's required `source_dir` therefore acts only as a
  pre-fill in the install dialog; whether Claude Desktop stores it resolved
  or literal is [unverified].
- An optional field with no default and no stored value has no map entry, so
  `${user_config.key}` reaches the server as that literal text, not as an
  empty string. [source: config-v212] [source: issue-250] (open)
- Booleans become `"true"`/`"false"`; other scalars go through `String()`.
  [source: config-v212] lines 161-171

### `platform_overrides`

- In 2.1.2 a platform override that sets `env` replaces the whole base `env`
  (`result.env = platformConfig.env || result.env`), dropping every base
  entry, `${user_config.*}` wiring included. [source: config-v212] line 120
  [source: issue-217] (maintainer diagnosis)
- `main` merges the override into the base env instead (#221, merged
  2026-04-13); no CLI release contains it, and which loader version Claude
  Desktop bundles is unknown. [source: pr-221] [source: config-main]
  [unverified] for Claude Desktop.

### CLI validation of variables

- The released 2.1.2 CLI does not check `${...}` names in `mcp_config`.
  [observed: `git show v2.1.2:src/node/validate.ts` has no
  `VALID_VARIABLE_PATTERN`]
- `main`'s `mcpb validate`, which `mcpb pack` runs first, accepts only
  `${__dirname}`, `${pathSeparator}`, `${/}` and `${user_config.<key>}` in
  `mcp_config` `command`, `args` and `env` (and their platform overrides);
  any other token, `${HOME}` and `${DOCUMENTS}` included, is an error that
  stops the pack. It does not inspect `user_config` defaults.
  [source: validate-main] `validateCommandVariables`. This contradicts the
  spec's own variable list; it ships with the next CLI release unless changed.
- Whether Claude Desktop runs this validator on install is not documented.
  [unverified]

### Platforms

- Claude Desktop runs on macOS and Windows; the README names no Linux host.
  [source: readme] On those two, `${HOME}` comes from the host's `systemDirs`
  rather than the process environment, so it does not depend on a `HOME`
  environment variable existing on Windows. [source: config-v212] That
  Claude Desktop supplies it on Windows is [unverified], with mcpb#251 as a
  reporter observation in favour.

## Where this project departs from the subject

None recorded. The project's `${DOCUMENTS}/Vault` default and the
pre-release check that the placeholder survives rendering
(`packaging/pre-release-checks.sh`) rely on the spec's default-variable list,
which the library does not implement for optional fields (see above).

## Not covered

- What Claude Desktop does with a default in its install dialog (resolve
  before storing, or store literal): needs a probe bundle installed in
  Claude Desktop.
- The exact `systemDirs` keys Claude Desktop passes, and whether any of them
  names an application-data directory: needs the same probe.
- Whether `${__dirname}` survives an extension update.
- The environment Claude Desktop gives the spawned process beyond the
  manifest's `env` (whether `HOME`, `APPDATA` and the like are inherited).
