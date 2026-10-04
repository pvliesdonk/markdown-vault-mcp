# Markdown Vault MCP

Generic markdown vault MCP with hybrid search

## Design
<!-- DOMAIN-START -->
The authoritative design specification lives at [`docs/design/design.md`](docs/design/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
The complete file-by-file map of `src/markdown_vault_mcp/` lives in
[`docs/design/module-map.md`](docs/design/module-map.md) — read it when locating
a module or adding one, rather than guessing from the package layout. It is kept
out of this always-loaded file because it is reference material consulted on
demand, not context every session needs.
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Conventions

- Write commit subjects and pull-request titles as conventional commits: one type from `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`, an optional scope (`feat(search): ...`), and `!` for a breaking change. The `PR Title` job fails any other title; retitle to clear it, no push needed. Accepted types: `scripts/check_pr_title.py`.
- Only `feat`, `fix`, and the `!` marker drive releases: `feat` cuts a minor, `fix` a patch, `!` a major. Every other type, `perf` included, cuts nothing and never reaches `CHANGELOG.md`. Ship a change that needs a release of its own as a `fix:` or through Release Prepare's `override_version` input.
- `Revert "..."` titles pass the title job. Neither revert form reaches `CHANGELOG.md`; narrate a revert on its `docs/releases/` page.
- Write a Google-style docstring on every public function and a type hint on every signature.
- Log through `logging.getLogger(__name__)`, never `print()`, with messages shaped `event_name key=%s` (the `logging-standard` skill); `tests/test_logging_standard.py` fails any other first-party call.
- Put shared test fixtures in `tests/fixtures/`.

## Skills

Skills live under `.agents/skills/`; Claude Code reads them through the `.claude/skills/` symlinks. They load only when invoked, so invoke the one that matches before you start:

- `releasing` — before any release, release-candidate, unstable-channel, plugin-channel, or release-notes work.
- `config-contract` — before adding a config field, env var, Dockerfile extension point, mcpb install-screen entry, or release-manifest stamp.
- `logging-standard` — before adding or changing a logging call.
- `tool-registration` — before adding, renaming, or documenting an MCP tool, `get_server_info`, icons, or the public import surface.
- `writing-model-facing-text` — before writing or changing a tool, parameter, resource or prompt description, or a server-instructions snippet.
- `writing-documentation` — before adding, moving or substantially changing a page in `docs/` or `README.md`: whether the knowledge is this project's or the template's, and where it goes.
- `designing-tool-outcomes` — before writing or changing a tool that can fail, refuse, find nothing or hit a conflict: what it returns, raises and logs in each case.
- `repository-protection` — before changing rulesets, required checks, or the bootstrap workflow.
- `authoring-issues-prs` — when filing an issue or opening a PR.
- `self-reviewing` — before opening a PR, marking one ready, or pushing further commits to a branch with an open PR: self-review the cumulative diff.
- `applying-template-updates` — when working through the weekly template update PR (`copier/update` branch) or after running `copier update`.
- `writing-release-notes` — when drafting a `docs/releases/` page.
- `roadmapping` — when charting, refining or revisiting epics and release packages; before planning work that spans PRs.
- `researching-references` — when a change depends on how something outside the repo behaves (a markdown dialect, git, a file format, a vendor API) and `docs/design/reference/` has no current page for it.

Project-owned skills follow the same shape: a directory under `.agents/skills/` plus a relative symlink in `.claude/skills/`. Project-specific issue and PR conventions go inside the `authoring-issues-prs` skill's `DOMAIN-AUTHORING` block.

## Breaking Changes and the `!` Marker

Mark a commit `!` (or add a `BREAKING CHANGE:` footer) only when it breaks one of two surfaces for a user of the last stable release:

- Operator surface: an environment variable, config file, CLI flag, deployment layout, or on-disk state format a human must change to upgrade.
- Public library interface: anything importable from `markdown_vault_mcp` that a downstream Python consumer uses.

A change to the MCP surface (the tools, resources and prompts a client discovers over the protocol: their names, parameters, schemas and payload shapes, and adding or removing any of them) is not breaking on its own, because the client re-discovers the surface on connect. It is breaking when a component keeps its shape but its previous behaviour is no longer reachable; an additive or dual-mode change is not.

Assess against the last stable release, not the previous commit: a change to a surface introduced in the same unreleased range earns no `!`. Before merging a commit carrying `!`, run `git tag --contains` on the commit that introduced the surface; an empty result means the surface never shipped and the `!` is spurious.

## Hard PR Acceptance Gates

Run every gate locally and open or push a PR only when all of them pass:

1. Tests: `uv run pytest -x -q`.
2. Lint: `uv run ruff check --fix .`, then `uv run ruff format .`, then `uv run ruff format --check .`, in that order.
3. Types: `uv run mypy src/ tests/`.
4. Patch coverage ≥ 80% on the lines the PR adds or changes: `uv run pytest --cov=src/markdown_vault_mcp --cov-report=term-missing`; add a test for every uncovered new branch before pushing. Pass `--cov` the path form; a dotted module target (`--cov=markdown_vault_mcp.config`) aborts the whole session at conftest load.
5. Docs: `README.md` and `docs/**` reflect every user-facing change in the same commit (Documentation Discipline below).
6. Manifest version lockstep: `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json` and `.claude-plugin/plugin/.mcp.json` carry the same version, the latest stable release. A stable release PR stamps all three; when you touch one by hand, update all three.
7. Structural quality (diff) passes: new or changed lines add no structural violation (complexity, too-many-*, security); pre-existing code is never blocked. Run `bash scripts/structural_gate.sh` before pushing. It runs `diff-quality --violations=ruff.check --options="--extend-select=C901,PLR0911,PLR0912,PLR0913,PLR0915,S" --fail-under=100` against the nearest of `origin/main`, `origin/release/*` and `origin/integration/*` (override with `STRUCTURAL_GATE_BASE`). For irreducible new code, add `# noqa: C901` (or the rule that fired) with a one-line justification.

## Pre-commit Hooks

- Install once per clone with `uv run pre-commit install`; run `uv run pre-commit run --all-files` before pushing.
- The `structural-diff-gate` hook runs `scripts/structural_gate.sh` at push time.
- **Template conformance runs at push time:** the `template-conformance` hook fails a push that adds content outside a sentinel block in a file `copier update` re-renders. Move the content into the block the file declares. Skip the hook only for drift a Decay issue tracks: `SKIP=template-conformance git push`.
- Fix a failing hook; never bypass it with `--no-verify`, because the same check fails in CI.
- Add domain hooks (shellcheck, yamllint, project linters) between the `DOMAIN-HOOKS` markers at the end of the config, never outside them; hooks inside that block on top of the shipped defaults survive `copier update`.

## Structural health

- Keep each function single-purpose; a section that needs its own comment is a function of its own.
- Nest at most three levels; extract or return early beyond that.
- Pass at most five parameters; past that, pass an object or split the function.
- Give a new responsibility its own collaborator instead of a longer class.
- Before substantial work in an unfamiliar area, or before touching a flagged module, run the advisory audit:

  ```bash
  uv run --with radon python -m radon cc -s -n C src/    # complexity hotspots (grade C+)
  uv run --with radon python -m radon mi -s src/         # maintainability index
  uv run --with vulture vulture src/                     # dead-code candidates
  ```

  `vulture` over-reports on imported, decorated and framework-registered code; confirm each candidate before deleting it.
- When you notice decay outside the current change's scope (a god class forming, a dead branch, a leaking abstraction, a name that no longer matches behaviour, an audit hotspot), open an issue with the Decay form (`.github/ISSUE_TEMPLATE/decay.yml`: What, Where, Why it compounds, Suggested direction) instead of fixing it inline or passing it over. File only decay that will compound; it does not block the current PR.

## PR Discipline

Every PR closes or references at least one issue: create the issue first when none exists, then put `Closes #N` (or `Refs #N`) in the PR body. One PR may close several issues (`Closes #A, closes #B`). Pure typo fixes and automated dependency bumps (Renovate) need no issue.

<!-- TEMPLATE-TRACKING-START -->
Request Claude on a pull request or issue with an explicit `@claude` mention.
Automatic agent review is disabled. Request Claude selectively with an `@claude` mention; deterministic CI remains the merge gate.
<!-- TEMPLATE-TRACKING-END -->

## GitHub Review Types

Before you call a review round complete, read and address both kinds of comment: inline review comments on the diff (the "Files changed" tab) and PR-level comments on the Conversation tab, where review summaries, bot analyses and blocking issues are posted.

End every issue, comment, PR description, review summary and inline reply with the `Agent-authored:` footer from `CONTRIBUTING.md`'s "Agent-authored posts" section, naming the agent product you are, and write as a proposer, because the post appears under the account holder's name and they decide in a reply. Before treating a post under the account holder's name as their decision, check it for that marker; it may be an earlier session's output, including yours.

## Documentation Discipline

Where a page belongs and who owns it is set by `docs/contribute/docs-structure.md`, which the `writing-documentation` skill applies. Before you close an issue or open a PR, update whichever of these the change touches:

- `docs/design/`: every new feature, changed behaviour and architectural decision.
- `docs/design/reference/`: dated, sourced references on how external things behave (the `researching-references` skill). Close a bug rooted in an external behaviour with a reference entry, not only a design-doc note; re-research a reference past its `stale_after` date before relying on it.
- `README.md` and the `docs/` site pages: new or changed tools, resources, prompts, CLI flags, installation methods and deployment options. A new env var or config field follows the `config-contract` skill: the generated configuration reference lists every field, and the README tables carry only the fields tagged `readme`.
- `CHANGELOG.md`: knope writes each release's version section below the `<!-- version list -->` flag line. Never hand-edit a version section or the flag line. If the project predates the flag, add the flag line once by hand; `tests/test_release_flow_contract.py` fails with the exact line until it is present.
- Docstrings: every new or changed public function gets an accurate Google-style docstring.

Code without matching docs is incomplete.

## Documentation Conventions

Vale lints `docs/` and `README.md` in pre-commit and CI; keep them clean. Internal docs are neither linted nor published; put them in one of these three subtrees and add no other exclusion:

- `docs/design/`: design specs and architecture notes, with `docs/design/reference/` for the external-behaviour references.
- `docs/decisions/`: architecture decision records.
- `docs/superpowers/`: agent scratch, gitignored; a feature's approved spec ships in its PR body.

## Roadmap

Read `docs/design/roadmap.md` before planning work, and update its argument when direction changes. GitHub owns status (epics are parent issues with native sub-issues; release packages are ordinal-named milestones); the index owns the argument.

<!-- TEMPLATE-TRACKING-START -->
## Shared Infrastructure

Shared infrastructure (auth providers, middleware, logging bootstrap, event store, CLI scaffolding, release pipeline, Docker entrypoint, nfpm and mcpb packaging) lives upstream in [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core), the Python library, and [`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template), the copier template this project was generated from. Fixes to shared code land there and propagate here via `copier update`, run by the weekly `.github/workflows/copier-update.yml` cron or by hand.

Content outside a sentinel block in a file `copier update` re-renders is drift, not a customisation: `uv run --script scripts/check_template_conformance.py` lists every such line against the pinned template version, and `docs/contribute/template-updates.md` names each file's blocks. Domain code (tools, resources, prompts, and the config fields inside the `CONFIG-*` sentinels) stays in this repo. A conflict marker in a copier-update PR often signals a template bug; check whether the template needs fixing before resolving locally.

## Contributing fixes upstream

`CONTRIBUTING.md` holds the three-tier routing (library to `fastmcp-pvl-core`, template to `fastmcp-server-template`, domain to this repo), the issue and PR discipline, and the uncertainty rule.
<!-- TEMPLATE-TRACKING-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Key Design Decisions
<!-- DOMAIN-START -->
- Document identity: relative path with `.md` extension
- Frontmatter: optional by default, `required_frontmatter` config to enforce
- Hybrid search: Reciprocal Rank Fusion (RRF)
- Tool semantics: mirror Claude Code Read/Write/Edit patterns
- Library is sync; MCP layer uses `asyncio.to_thread()`
- Indexing is hash-based, so an unchanged file is never re-parsed: any change to how a note's stored rows are derived from its bytes (link extraction, chunking, tag/alias/heading derivation) must be covered by an `INDEX_SEMANTICS_VERSION` bump in `fts_index.py`, or deployed vaults keep serving the rows the old code produced (#1124). The bump is **once per release** (#1365): if no stable or rc tag contains the commit that set the current value, the value already covers the next release — extend its history note with the new change instead of bumping again (`git tag --contains <that commit>` empty → no bump). The edge channel is not a release for this purpose. A redundant bump is harmless, not a defect
- Full decision log in `docs/design/design.md` appendix
- How the outside world behaves (Obsidian's dialect, CommonMark/GFM, git) is recorded in dated, sourced references under `docs/design/reference/` (an OKF v0.2 bundle; start at its `index.md`); read the relevant page before touching `scanner.py`, `fts_index.py` link resolution, or `git/`, and re-research rather than trust a page past its `stale_after`
<!-- DOMAIN-END -->
