# MV replay triage (Step 4 Phase B)

Generated: 2026-04-20
Template: `/mnt/code/fastmcp-server-template` @ `v1.0.0`
Render target: `/tmp/mv-replay`
MV source: `/mnt/code/markdown-mcp` @ `main` (v1.25.0)
Raw diff: `/tmp/mv-replay-diff.txt` (8566 lines, 134 entries)

## Phase 1 outcome

- **Class A (domain content, no action):** 62
- **Class B (infra bug in MV, fix during retrofit):** 4
- **Class C (hybrid/sentinel + full-replacement hybrid):** 29
- **Class D (template bug — cut v1.0.x patch):** 0
- **Class E (acceptable divergence, document + move on):** 39

Total entries: 134 (10 `Only in /tmp/mv-replay:` + 89 `Only in /mnt/code/markdown-mcp:` + 35 `diff -r`).

`⚠️ NEEDS HUMAN CALL` items:
- `.gitleaks.toml` — template uses `regexes`-based allowlist; MV uses `paths`-based. Both work but they solve different scopes. Decide whether to unify or keep divergence documented.
- `pyproject.toml` ruff codes — MV has legacy `TCH001/TCH002` (still honored but deprecated); template uses `TC001/TC002/TC003`. Treat as Class E (acceptable, optional retrofit to modern codes).

---

## Class A — Domain content (no action)

MV has these files, template correctly does not produce them (vault/FTS5/embeddings/MCP-Apps/manager/scanner/git/artifacts/SPA-icons/prompts domain).

- `src/markdown_vault_mcp/artifacts.py` — one-time download artifact store (domain)
- `src/markdown_vault_mcp/collection.py` — Collection facade (domain)
- `src/markdown_vault_mcp/exceptions.py` — domain exceptions
- `src/markdown_vault_mcp/fts_index.py` — SQLite FTS5 (domain)
- `src/markdown_vault_mcp/git.py` — GitWriteStrategy (domain)
- `src/markdown_vault_mcp/hashing.py` — hash helpers (domain)
- `src/markdown_vault_mcp/_icons.py` — Lucide icon map (domain)
- `src/markdown_vault_mcp/managers/` (4 files: document.py, index.py, link.py, search.py) — domain manager layer
- `src/markdown_vault_mcp/mcp_server.py` — MV-specific FastMCP wiring (domain)
- `src/markdown_vault_mcp/providers.py` — embedding provider ABC + impls (domain)
- `src/markdown_vault_mcp/scanner.py` — file discovery + chunking (domain)
- `src/markdown_vault_mcp/_server_prompts.py` — prompt registration (domain module; template ships a tiny `prompts.py` starter instead)
- `src/markdown_vault_mcp/_server_resources.py` — resource registration (domain)
- `src/markdown_vault_mcp/_server_tools.py` — tool registration (domain)
- `src/markdown_vault_mcp/static/app.src.html` — SPA source template (MCP Apps domain)
- `src/markdown_vault_mcp/static/icons/` — Lucide SVG icons (MCP Apps domain)
- `src/markdown_vault_mcp/static/prompts/` — built-in prompt templates (domain)
- `src/markdown_vault_mcp/tracker.py` — hash-based change tracker (domain)
- `src/markdown_vault_mcp/types.py` — public API dataclasses (domain)
- `src/markdown_vault_mcp/utils/` — text/links helpers (domain)
- `src/markdown_vault_mcp/vector_index.py` — numpy embeddings (domain)
- `tests/fixtures/` — test vault fixtures (domain)
- `tests/__init__.py` — test package marker (domain)
- `tests/test_api_validation.py` — domain API tests
- `tests/test_artifacts.py` — artifacts tests
- `tests/test_cli.py` — CLI tests
- `tests/test_collection.py` — Collection tests
- `tests/test_config.py` — config tests
- `tests/test_docstrings.py` — docstring discipline tests
- `tests/test_event_store.py` — event store tests
- `tests/test_fts_index.py` — FTS index tests
- `tests/test_git.py` — git strategy tests
- `tests/test_graph.py` — link graph tests
- `tests/test_links.py` — link parsing tests
- `tests/test_managers_document.py` — DocumentManager tests
- `tests/test_managers_index.py` — IndexManager tests
- `tests/test_managers_link.py` — LinkManager tests
- `tests/test_managers_search.py` — SearchManager tests
- `tests/test_mcp_apps_browser.py` — MCP Apps Browser view tests
- `tests/test_mcp_apps_context.py` — MCP Apps Context Card tests
- `tests/test_mcp_apps_foundation.py` — MCP Apps foundation tests
- `tests/test_mcp_apps_graph.py` — MCP Apps Graph Explorer tests
- `tests/test_mcp_apps_navigation.py` — MCP Apps cross-nav tests
- `tests/test_mcp_server.py` — MCP server tests
- `tests/test_packaging_mcpb.py` — mcpb packaging tests
- `tests/test_prompts.py` — prompts tests
- `tests/test_providers.py` — embedding provider tests
- `tests/test_scanner.py` — scanner tests
- `tests/test_tracker.py` — tracker tests
- `tests/test_utils_links.py` — utils/links tests
- `tests/test_utils_text.py` — utils/text tests
- `tests/test_vector_index.py` — vector index tests
- `docs/api/` — Python API reference pages (domain MkDocs)
- `docs/deployment/claude-desktop.md` — domain guide
- `docs/deployment/index.md` — domain deployment overview
- `docs/deployment/systemd.md` — domain systemd guide
- `docs/deployment.md` — domain landing page
- `docs/guides/claude-code-plugin.md` — domain
- `docs/guides/claude-desktop.md` — domain
- `docs/guides/docker.md` — domain
- `docs/guides/embeddings.md` — domain
- `docs/guides/git-integration.md` — domain
- `docs/guides/index.md` — domain guides index
- `docs/guides/mcp-apps.md` — domain
- `docs/guides/obsidian-everywhere.md` — domain
- `docs/guides/oidc-providers.md` — domain
- `docs/guides/para.md` — domain
- `docs/guides/research-workflows.md` — domain
- `docs/guides/zettelkasten.md` — domain
- `docs/hooks.py` — MkDocs hooks (domain)
- `docs/prompts.md` — domain MCP prompts catalog
- `docs/resources.md` — domain MCP resources catalog
- `docs/superpowers/` — domain plans/specs (not shipped)
- `examples/ifcraftcorpus.env` — domain example
- `examples/obsidian-oidc.env` — domain example
- `examples/obsidian-readonly.env` — domain example
- `examples/obsidian-readwrite.env` — domain example
- `examples/para/` — domain PARA templates
- `examples/zettelkasten/` — domain Zettelkasten templates
- `packaging/mcpb/` — MCP Bundle packaging (domain)
- `packaging/env.example` — packaging-time env var sample (domain; systemd deployment)
- `site/` — generated MkDocs output (build artifact; should already be in .gitignore)
- `SYNC.md` — cross-repo sync tracking (domain/project convention; template intentionally omits)
- `.claude-plugin/` — Claude Code plugin manifest (domain distribution channel)
- `.dockerignore` — domain `.dockerignore` (template deliberately omits per design)
- `.worktrees/` — local git worktree dir (transient; not committed)
- `.mcpregistry_github_token` / `.mcpregistry_registry_token` — local creds (must stay ignored; also seen as Class E)

**Template-only skeletons that would be skipped on real retrofit (Class A for replay purposes):**
These are in the template's `_skip_if_exists` list — they only appeared in the replay because `/tmp/mv-replay` was empty. During the real MV retrofit (Phase C), copier will see the existing MV tree and skip them entirely, so no action is needed.

- `src/markdown_vault_mcp/tools.py` — starter tool module (MV uses `_server_tools.py` instead)
- `src/markdown_vault_mcp/resources.py` — starter resource module (MV uses `_server_resources.py`)
- `src/markdown_vault_mcp/prompts.py` — starter prompt module (MV uses `_server_prompts.py`)
- `src/markdown_vault_mcp/domain.py` — `Service` dataclass starter (MV uses `Collection`)
- `src/markdown_vault_mcp/server.py` — minimal `make_server` starter (MV uses `mcp_server.py`)
- `tests/test_smoke.py` — smoke test starter
- `tests/test_tools.py` — tools test starter

---

## Class B — Infra bug in MV (fix MV during retrofit)

Template produces it correctly; MV is missing it or has it wrong.

1. `Only in /tmp/mv-replay: .github/dependabot.yml` — Template ships dependabot config; MV has `.github/codeql/` and `.github/workflows/` but no `dependabot.yml`. **Action (retrofit):** adopt the template's dependabot.yml.
2. `Only in /tmp/mv-replay: .env.example` — Template ships `.env.example` at repo root; MV has `examples/*.env` files but no top-level `.env.example`. **Action (retrofit):** adopt the template's `.env.example` or add a symlink/doc reference.
3. `CHANGELOG.md` — Template's CHANGELOG includes the "Format follows Keep a Changelog" header + description; MV's CHANGELOG is managed entirely by semantic-release and has only the version entries without the preamble. **Action (retrofit):** add the Keep-a-Changelog header block on top (small, harmless).
4. `Only in /tmp/mv-replay: src/markdown_vault_mcp/_server_apps.py` content — Template ships an *inert* `_server_apps.py` scaffold that logs "scaffold present but not wired" when `APP_DOMAIN`/`BASE_URL` is set. MV's `_server_apps.py` is a full implementation (Class C hybrid overall — see below). No Class B action here; the presence of the inert scaffold is template-only for greenfield projects.

(Item 4 is a judgment call; treating the *file diff* as Class C hybrid, not Class B. Listed here for visibility.)

---

## Class C — Hybrid file, domain content preserved via sentinels or full replacement

These diffs are expected: template owns the skeleton, MV owns the richer domain content. On `copier update` the sentinel blocks and `_skip_if_exists` files protect MV's content.

### Sentinel-based hybrids

1. `CLAUDE.md` — DOMAIN-START/END sentinels; MV has rich project-memory content inside them. Template-owned prose (acceptance gates, documentation discipline, cross-repo sync) is updated on template bumps.
2. `pyproject.toml` — `PROJECT-DEPS-START/END` + `PROJECT-EXTRAS-START/END` sentinels; MV has `python-frontmatter`, `requests`, `mcp`/`embeddings-api`/`embeddings`/`all`/`docs` extras. Also includes MV-specific `[tool.uv]` pygments pin, hatch exclude for `app.src.html`, ruff per-file overrides for domain modules, mypy overrides for FTS/vector/providers modules, PSR `build_command` + plugin-manifest `version_toml` entries.
3. `src/markdown_vault_mcp/config.py` — `CONFIG-FIELDS-START/END` + `CONFIG-FROM-ENV-START/END` sentinels; MV has full `CollectionConfig` dataclass with 20+ domain fields plus auth builder wrappers.

### Full-replacement hybrids (MV's richer version wins)

4. `README.md` — MV has `mcp-name` header, installation + quickstart, feature list, MkDocs link.
5. `server.json` — MV lists 36 PyPI + 38 OCI env vars with schema metadata; template has minimal server.json.
6. `src/markdown_vault_mcp/__init__.py` — MV exports full public API (Collection, CollectionConfig, load_config, 20+ dataclasses, 7 exceptions, GitWriteStrategy); template has minimal `"""…"""` + `__version__`.
7. `src/markdown_vault_mcp/cli.py` — MV has `serve`/`index`/`search`/`reindex` subcommands; template has `make_serve_parser`-based `serve`-only skeleton.
8. `src/markdown_vault_mcp/_server_apps.py` — template ships inert scaffold (≈40 lines); MV has full MCP Apps SPA registration, tool visibility=app, CSP/APP_DOMAIN wiring.
9. `src/markdown_vault_mcp/_server_deps.py` — template ships `Service` lifespan scaffold (≈50 lines); MV has full Collection lifespan with git sync, embeddings build, event store, etc.
10. `scripts/vendor_spa.py` — template ships no-op placeholder; MV has full vendor pipeline (SHA-256 integrity, inline script/module, source hash marker for offline `--check`).
11. `src/markdown_vault_mcp/static/app.html` — template ships a short rendered stub; MV has the full vendored-inline SPA (≈8000 lines). Excluded by intent via `static/app.html` in excludes list (didn't actually filter due to diff basename matching — left as Class C noise).
12. `docs/design.md` — template is a short spec skeleton; MV has the full authoritative design doc (Phase 1/2/3 notes, architecture, shared infrastructure).
13. `docs/index.md` — template is a short landing skeleton; MV is a rich features/architecture overview.
14. `docs/configuration.md` — template has a stub pointing at core's README; MV has the full env-var reference table for all domain vars.
15. `docs/installation.md` — template stub; MV has PyPI/uv/source/Docker install recipes.
16. `docs/tools/index.md` — template stub; MV documents 28 tools + 6 app-only tools.
17. `docs/deployment/docker.md` — template has quick-start stub; MV has full Compose/volumes/Traefik/UID-GID deployment guide.
18. `docs/deployment/oidc.md` — template stub; MV has full Authelia + token-lifetime + multi-auth guide.
19. `docs/guides/authentication.md` — template stub; MV has full bearer/OIDC/multi-auth chapter.
20. `mkdocs.yml` — MV has rich nav (Python API section, deployment sub-pages, guides), `exclude_docs` for superpowers/design, `docs/hooks.py` hook, `llmstxt` plugin config.
21. `compose.yml` — MV has vault bind-mount, `MARKDOWN_VAULT_MCP_SOURCE_DIR`/INDEX_PATH/EMBEDDINGS_PATH/FASTEMBED_CACHE_DIR envs; template uses generic `service-data:/data/service` volume.
22. `Dockerfile` — MV uses `--extra all`, `COPY . .` (instead of selective copy), vault path `/data/vault`. Small differences but domain-owned.
23. `docker-entrypoint.sh` — MV creates extra embeddings/fastembed dirs under `/data/state`.
24. `tests/conftest.py` — MV has `MockEmbeddingProvider`, `vault_path`/`fixtures_path`/`mock_provider` fixtures; template has minimal `_clear_env` + FastMCP `client` fixture only.
25. `.github/workflows/ci.yml` — MV adds `vendor_spa.py --check` step and ignores two specific CVEs in pip-audit. Domain-specific (vendored SPA + CVE exceptions).
26. `.github/workflows/release.yml` — one-liner diff: image description wording ("vault" vs "collection"). MV version.
27. `.pre-commit-config.yaml` — MV adds `vendor-spa-check` hook for `app.src.html`.
28. `packaging/nfpm.yaml` — MV has real version + "Peter van Liesdonk" maintainer + longer description.
29. `packaging/scripts/postinstall.sh` — MV installs `markdown-vault-mcp[all]`; template installs bare package. MV is right because of the `[all]` extra that pulls in embeddings + mcp deps.

### Hybrid near-equivalents (cosmetic only)

30. `packaging/markdown-vault-mcp.service` — comment differences only ("vault" vs "data" wording). Keeps MV's copy.
31. `examples/bearer-auth.env` — MV-vs-template header wording + env var list; MV's example is more complete.
32. `LICENSE` — Copyright line: "Peter van Liesdonk" (MV) vs "pvliesdonk" (template). MV's is canonical.

---

## Class D — Infra bug in template (cut v1.0.x patch)

**None found.**

No entry in the diff warrants a template patch. Every template-only infra file that MV lacks is actually a correct template improvement MV should adopt (Class B), not a template bug. Template skeletons that are smaller than MV's equivalents are intentional hybrids (Class C).

---

## Class E — Acceptable divergence (document + move on)

Files/dirs that are divergent-by-design and won't be harmonised.

1. `Only in /mnt/code/markdown-mcp: .claude-plugin` — Claude Code plugin manifest; template omits by design (distribution channel, not infrastructure).
2. `Only in /mnt/code/markdown-mcp: .dockerignore` — template deliberately does NOT ship one (see `docker-entrypoint.sh` design notes — adding `.dockerignore` can break the entrypoint COPY).
3. `Only in /mnt/code/markdown-mcp: .mcpregistry_github_token` — local credential, must stay gitignored (MV gitignore handles it).
4. `Only in /mnt/code/markdown-mcp: .mcpregistry_registry_token` — same as above.
5. `Only in /mnt/code/markdown-mcp: .worktrees` — local git worktree dir; transient.
6. `Only in /mnt/code/markdown-mcp: site` — generated MkDocs output; gitignored.
7. `Only in /mnt/code/markdown-mcp: SYNC.md` — cross-repo tracking file; intentionally MV-only.
8. `Only in /mnt/code/markdown-mcp/docs: api` — Python API reference pages, MV-specific.
9. `Only in /mnt/code/markdown-mcp/docs: deployment.md` — MV landing page for deployment/.
10. `Only in /mnt/code/markdown-mcp/docs: hooks.py` — MkDocs hook, MV-specific.
11. `Only in /mnt/code/markdown-mcp/docs: prompts.md` — MV prompt catalog.
12. `Only in /mnt/code/markdown-mcp/docs: resources.md` — MV resource catalog.
13. `Only in /mnt/code/markdown-mcp/docs: superpowers` — MV plans/specs tree; not published.
14. `Only in /mnt/code/markdown-mcp/docs/deployment: claude-desktop.md` — MV-specific deployment target.
15. `Only in /mnt/code/markdown-mcp/docs/deployment: index.md` — MV deployment landing.
16. `Only in /mnt/code/markdown-mcp/docs/deployment: systemd.md` — MV systemd guide.
17. `Only in /mnt/code/markdown-mcp/docs/guides: claude-code-plugin.md` — MV guide.
18. `Only in /mnt/code/markdown-mcp/docs/guides: claude-desktop.md` — MV guide.
19. `Only in /mnt/code/markdown-mcp/docs/guides: docker.md` — MV guide.
20. `Only in /mnt/code/markdown-mcp/docs/guides: embeddings.md` — MV guide.
21. `Only in /mnt/code/markdown-mcp/docs/guides: git-integration.md` — MV guide.
22. `Only in /mnt/code/markdown-mcp/docs/guides: index.md` — MV guides index.
23. `Only in /mnt/code/markdown-mcp/docs/guides: mcp-apps.md` — MV guide.
24. `Only in /mnt/code/markdown-mcp/docs/guides: obsidian-everywhere.md` — MV guide.
25. `Only in /mnt/code/markdown-mcp/docs/guides: oidc-providers.md` — MV guide.
26. `Only in /mnt/code/markdown-mcp/docs/guides: para.md` — MV guide.
27. `Only in /mnt/code/markdown-mcp/docs/guides: research-workflows.md` — MV guide.
28. `Only in /mnt/code/markdown-mcp/docs/guides: zettelkasten.md` — MV guide.
29. `Only in /mnt/code/markdown-mcp/examples: ifcraftcorpus.env` — MV-specific domain example.
30. `Only in /mnt/code/markdown-mcp/examples: obsidian-oidc.env` — MV-specific domain example.
31. `Only in /mnt/code/markdown-mcp/examples: obsidian-readonly.env` — MV-specific domain example.
32. `Only in /mnt/code/markdown-mcp/examples: obsidian-readwrite.env` — MV-specific domain example.
33. `Only in /mnt/code/markdown-mcp/examples: para` — PARA template vault.
34. `Only in /mnt/code/markdown-mcp/examples: zettelkasten` — Zettelkasten template vault.
35. `Only in /tmp/mv-replay/examples: oidc.env` — **Reclassified Class B during retrofit:** template ships generic OIDC starter; MV had only `obsidian-oidc.env`. Adopted in retrofit so users get a vendor-neutral example alongside the Obsidian-flavored one.
36. `Only in /mnt/code/markdown-mcp/packaging: env.example` — Systemd-deployment env-var sample; MV-specific.
37. `Only in /mnt/code/markdown-mcp/packaging: mcpb` — MCP Bundle packaging artifacts; MV distribution channel.
38. `Only in /mnt/code/markdown-mcp/scripts: bump_manifests.py` — Custom PSR build script that bumps `.claude-plugin/**` manifests in the release commit (MV-specific manifest set). Referenced by MV's pyproject `build_command`.
39. `.gitignore` — MV ignores `.mcpregistry_*` and `.worktrees/`; template ignores `.claude/`. Small divergence, both correct.

**⚠️ NEEDS HUMAN CALL (also listed at top):**
- `.gitleaks.toml` — template uses `regexes` (test token patterns); MV uses `paths` (doc/example directories). Both are valid allowlist strategies — is there a canonical one the template should adopt?
- `pyproject.toml` ruff codes — MV uses legacy `TCH001/TCH002`; template uses modern `TC001/TC002/TC003`. Not breaking MV but deserves an optional retrofit.
