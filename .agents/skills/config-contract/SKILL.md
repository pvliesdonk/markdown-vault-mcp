---
name: config-contract
description: >-
  Use when adding a domain configuration field, env var, Dockerfile extension, mcpb install-screen entry, or release-manifest stamp: the sentinel-based config and customization contract.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Config Contract

## Config & Customization Contract

Domain configuration composes `fastmcp_pvl_core.ServerConfig` inside your domain config class (see `src/markdown_vault_mcp/config.py`).  Add domain fields between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels, populate them in `from_env` between the `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels, and enforce their invariants in `__post_init__` between the `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels.  Validation belongs in `__post_init__` rather than `from_env` because it then also covers a direct `ProjectConfig(field=...)`; `env_float` / `env_int` bounds check only the env-sourced value, are inclusive-only, and cannot express cross-field rules.  The dataclass is frozen — read fields, don't assign (use `object.__setattr__` if a field must be normalised).  Never inherit from `ServerConfig`; always compose.

Env var prefix is `MARKDOWN_VAULT_MCP_` — all env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent.

- **Domain CLI subcommands** go in the `# DOMAIN-COMMANDS-START` / `-END` block in `cli.py` (like `CONFIG-FIELDS` in `config.py`). Register them as `@app.command()` and use function-local imports for domain modules. The block is preserved across `copier update`.

### Config wizard

`docs/javascripts/config-wizard/wizard-spec.json` drives the guided-setup page. It is **generated**, produced by `scripts/gen_config_surface.py` on every `copier copy`/`copier update` and re-verified by `scripts/gen_config_surface.py --check` in CI — never hand-edit it. The runtime (`wizard.js`, `generators.js`, `wizard-spec-schema.json`, the generic tests) is template-owned and re-rendered the same way it always was.

To change what the wizard asks:

- **A domain setting your project reads** — give its `ProjectConfig` field (between the `CONFIG-FIELDS-START`/`-END` sentinels in `config.py`) a `metadata={"help": ..., "tags": (...)}`. The generator's AST scan discovers it from there; no wizard-spec edits needed.
- **A var the scan cannot see** (a deprecated alias no longer read inside `ProjectConfig.from_env`, or something read outside it entirely) — declare it in `config-presentation.domain.yml` instead.
- **Coverage is enforced, not automatic** — the generator fails loudly (`SystemExit`, naming the var and its tags) if a collected `Var`'s tags match no env-file section, rather than silently dropping it from every generated file. Giving a domain field a tag no section lists is a config-presentation bug, and this catches it at generation time instead of shipping an undocumented var. There is, however, **no orphan check**: a stale or mistaken entry in `config-presentation.domain.yml` that nothing actually reads will generate into the wizard and env files anyway. Keep that file's contents matched to real reads yourself.

### mcpb install screen

`packaging/mcpb/manifest.json.in`'s `user_config` and `server.mcp_config.env` objects are **generated** the same way (`kind: mcpb-user-config`): both derive from one curated `fields:` map, so a screen field and its env wiring cannot drift apart, and hand edits to those two objects are overwritten on the next generation — the rest of the manifest stays yours. The template baseline shows a deliberately minimal screen (server name, log level). Curate this project's screen in `config-presentation.domain.yml` under `files:` → `packaging/mcpb/manifest.json.in` → `fields:`: map an env var name to `{id: ..., title: ..., type: string|boolean|number|directory|file, required: ..., default: ..., sensitive: ...}` (everything but `id` falls back to the var's own metadata), or to `null` to drop a baseline field. A `files:` entry for a path the template does not declare is taken wholesale — that is how a project drives its own additional install-channel manifest from the same source of truth.

### Dockerfile extension points

These sentinel blocks in `Dockerfile` are preserved across `copier update`. Add domain-specific apt packages, uv extras, state subdirs, and volume mounts inside them:

- `# DOCKERFILE-APT-DEPS-START` / `-END` — extra apt packages installed into the runtime image
- `# DOCKERFILE-UV-EXTRAS-START` / `-END` — `--extra <name>` flags added to both `uv sync` invocations (deps cache layer + project install — adding only to one breaks the cache layer)
- `# DOCKERFILE-STATE-DIRS-START` / `-END` — state subdirectories created under `/data` (chowned to the runtime user)
- `# DOCKERFILE-VOLUMES-START` / `-END` — `VOLUME` declarations on the final image

### Dependency pins

A pin in `[tool.uv] override-dependencies` / `constraint-dependencies` is a workaround, never a policy: it exists because something upstream is broken, and it must go the moment that is fixed. Every entry therefore carries `# until: <GitHub issue or PR URL>` — on its own line, on a comment line directly above it, or on the array's opening line to cover every entry — naming the upstream bug, or a project issue tracking it. `scripts/check_pins.py` (run by `ci.yml`'s lint job) fails the build when that issue is **closed**, so the reminder to lift the pin arrives on the next push rather than never. Keep one bound per package: a package bounded under `[project]` *and* in a `[tool.uv]` table fails the offline check, because Renovate cannot see the `[tool.uv]` tables and a raise on the `[project]` side would never move the lock. `tests/test_dependency_pins.py` runs the offline half locally.

### Release manifest extension points

`scripts/stamp_manifests.py` runs inside every release PR (knope's `prepare-release` workflow invokes it with the computed version; `pyproject.toml` is knope's own `versioned_files` entry and the script never touches it). It rewrites `uv.lock`'s self-version entry on every release, rc included, in the PEP 440 canonical spelling (`-rc.N` → `rcN`) — canonical because uv rewrites the entry to that spelling on any re-lock, and stamping it that way from the start means a mid-cycle re-lock changes nothing. The install-channel manifests — `server.json` and the Claude plugin pair — it stamps on *stable* versions only, so a stable tag never points at a manifest whose version lags it; on rc versions they stay at the last published stable — those manifests are the entries the MCP registry and the marketplace publish, and both surfaces are stable-only, so stamping an rc into them would advertise a candidate to everyone browsing the catalog (`tests/test_release_flow_contract.py` asserts all of this). That an rc's wheel is now on PyPI does not change this: reachability was never the whole reason, and the registry and marketplace entries are rolling pointers, not per-version ones. These sentinel blocks in that script are preserved across `copier update`. Add stamps for this project's own version-coupled manifests (another lockstep JSON/TOML) inside them:

- `# DOMAIN-MANIFESTS-HELPERS-START` / `-END` — module-level helpers (use `_load` / `_dump` for JSON so the byte format matches what `scripts/gen_config_surface.py` asserts; raise `StampError` for a pin that cannot be stamped — never warn and continue)
- `# DOMAIN-MANIFESTS-START` / `-END` — the calls, inside `main()`, where `version` is in scope; extend `stamped` with every path you rewrite so it is staged into the release commit

The script must actually be invoked by knope's stamp Command, or a stale manifest ships silently; `tests/test_release_flow_contract.py` asserts that invocation coupling (the successor of the PSR-era `assets` pairing rule), so a stamp declared in one half only fails the gate rather than shipping a release commit with a stale file in it.
