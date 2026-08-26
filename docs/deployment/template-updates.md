# Working Through a Template Update

This project is generated from
[fastmcp-server-template](https://github.com/pvliesdonk/fastmcp-server-template)
and tracks it. Once a week the `copier-update` workflow runs `copier update
--trust` and opens a pull request on the `copier/update` branch. Automated
runs advance one template major at a time, so a project several majors
behind sees one pull request per major, each with its own upgrade notes; a
manual dispatch with an explicit `vcs_ref` jumps straight to a chosen
release. This page is the procedure for that pull request,
whether you work through it yourself or hand it to a coding agent (the
`applying-template-updates` skill under `.agents/skills/` is the same
procedure in agent-invocable form).

## What the pull request carries

- The version delta (`vA.B.C` → `vX.Y.Z`) and a compare link on the
  template repository.
- The template's release notes for the target version, collapsed.
- The `UPGRADING.md` sections that apply to the jump, collapsed, with a
  link to the full file at the target ref. This is the template's record
  of the work `copier update` cannot do.
- The list of files that carry conflict markers, if any.
- The resolution policy: the template side wins everywhere outside the
  regions this project owns.

## The order of work

1. **Read the upgrade notes first.** Every `UPGRADING.md` section in the
   body names something a person must do: rename a variable, add a
   secret, rewrite a seeded test, add a `.gitignore` line. Do those steps
   on the `copier/update` branch before touching anything else; a note
   that is skipped now is a failure later, on a run nobody connects to
   this update.
2. **Resolve conflict markers.** `copier update` runs a three-way merge and
   leaves diff3-style markers where a template change and a local change
   collide: `<<<<<<< before updating` (the local side), `||||||| last
   update` (the common base), `=======`, and `>>>>>>> after updating` (the
   template side). Resolving a hunk means keeping one side and deleting
   all four marker lines and the base block between `|||||||` and
   `=======`. Outside the `DOMAIN-*`, `CONFIG-*` and `PROJECT-*` sentinel
   blocks the template side is correct by policy: a local edit to a
   template-owned region is drift, and the fix is to move that content
   into a sentinel block or upstream it. Inside a sentinel block the local
   side is yours and stays.
3. **Check the seeded-once files.** Copier's `_skip_if_exists` list names
   files that are written on the first `copier copy` and never touched
   again by `copier update`. They are absent from the diff even when the
   template changed them. The update writes `.copier-seeded-changes.md` at
   the repository root (the pull request body embeds it): a diff of every
   seeded path between the previous and target template versions, or an
   explicit statement that no seeded file changed. Work through that file
   and apply what applies by hand, keeping your local content. When the
   report says it could not be computed, fall back to the compare link and
   the "Before every upgrade" recipe in the template's `UPGRADING.md`. The
   seeded paths, for reference:

   `.claude-plugin/**`, `src/markdown_vault_mcp/tools.py`,
   `src/markdown_vault_mcp/resources.py`, `src/markdown_vault_mcp/prompts.py`,
   `src/markdown_vault_mcp/domain.py`, `tests/conftest.py`,
   `tests/test_smoke.py`, `tests/test_cli.py`,
   `tests/public_import_surface.txt`, `CHANGELOG.md`, `docs/releases/**`,
   `LICENSE`, `packaging/mcpb/manifest.json.in`,
   `packaging/mcpb/pyproject.toml.in`, `packaging/mcpb/src/server.py`,
   `packaging/mcpb/build.sh`, `.gitignore`, `.vale.ini`,
   `.vale/styles/config/vocabularies/Base/accept.txt`,
   `config-presentation.domain.yml`, `tests/test_config_wizard_domain.py`,
   `src/markdown_vault_mcp/static/app.src.html`.

   Four more skip-listed paths are generated, not seeded: `.env.example`,
   `packaging/env.example`, `examples/*.env` and
   `docs/javascripts/config-wizard/wizard-spec.json`. The update
   regenerates them from the config surface at its end, so never edit
   them by hand; `scripts/gen_config_surface.py --check` in CI catches a
   stale copy.

   The list above is a snapshot; the authoritative one is
   `_skip_if_exists` in the template's `copier.yml` at the target ref.
   Treat a template change to a seeded file as a change to apply, never as
   noise to ignore: the template ships these files as complete starting
   points, and corrections to them reach this project only this way.
4. **Refresh the lock file.** `uv lock` after any floor change (the
   upgrade notes say when), then `uv sync --all-extras --all-groups
   --locked`.
5. **Run the update gate.** This is wider than the everyday gate, because
   an update can leave drift that only these checks see:

   ```bash
   git diff --check
   git grep -nE '^(<<<<<<<|=======|>>>>>>>)'
   uv lock --check
   uv sync --all-extras --all-groups --locked
   uv run python scripts/gen_config_surface.py --check
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src/ tests/
   uv run pytest -x -q
   uv run mkdocs build --strict
   uv run pre-commit run --all-files
   ```

   An MCP Apps project also runs `uv run python scripts/vendor_spa.py
   --check`. CI runs the same checks on the pull request.
6. **Merge.** The branch is recreated on the next weekly run, so leave
   nothing uncommitted on it.

## Working with a coding agent

Give the agent the pull request number and ask it to invoke the
`applying-template-updates` skill. The skill walks the six steps above and
stops where a decision is yours: a conflict inside a sentinel block, an
upgrade note it cannot perform (a secret to add, a repository setting),
and any seeded-file change it is not certain applies to this project. It
reports what it applied, what it skipped and why. Review that report
against the pull request body before merging.

## When the update is not wanted

A template change can be wrong for this project. Do not resolve that by
editing a template-owned region; the next update reverts it. Either move
the divergence into a sentinel block the template preserves, or open an
issue on the template repository so the change gains a switch or a
sentinel. `FORKING.md` covers the last resort of detaching from the
template altogether.
