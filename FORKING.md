# Forking: detaching from the template

This project was generated from the
[`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template)
copier template and, by default, **tracks** it: the weekly
`copier-update` workflow opens PRs that pull in template and `fastmcp-pvl-core`
improvements, and `AGENTS.md` routes fixes back upstream.

A **fork** is different. If you are taking sole ownership of this server, or
want an opinionated variant that no longer follows the fleet, you should
**detach**: stop tracking the template and remove the fleet-wide automation and
guidance that no longer applies. A fork is not a downstream — after detaching,
template and `fastmcp-pvl-core` changes are yours to port manually.

Detaching is mechanical. Run the steps below once, then commit.

## Step 1 — Stop tracking the template

```bash
rm -f .copier-answers.yml
```

This removes the link copier uses to reattach. The weekly cron that ran
`copier update` is deleted in Step 2.

## Step 2 — Prune template-origin CI and fleet review wiring

```bash
rm -f .github/workflows/copier-update.yml \
      .github/workflows/claude.yml \
      .github/workflows/claude-code-review.yml
rm -f scripts/copier_update_notes.py
rm -f scripts/migrate_agent_instructions.py
rm -f scripts/report_seeded_changes.py .copier-seeded-changes.md
rm -rf .agents/skills/applying-template-updates .claude/skills/applying-template-updates
rm -f docs/deployment/template-updates.md
# The page above is in the MkDocs nav; a dangling entry fails `mkdocs build --strict`.
sed -i.bak '/Template Updates: deployment\/template-updates.md/d' mkdocs.yml && rm -f mkdocs.yml.bak
```

What this removes and why:

- `copier-update.yml` — template-update automation; meaningless once detached.
- `claude.yml` — the explicit `@claude` mention responder.
- `claude-code-review.yml` — the optional automatic pull-request reviewer, if
  your project enabled it.
- `scripts/copier_update_notes.py` — the UPGRADING.md section picker that
  only `copier-update.yml` invoked; dead weight once that workflow is gone.
- `scripts/migrate_agent_instructions.py` — the CLAUDE.md → AGENTS.md
  migration that only `copier update`'s `_migrations` stage invoked; dead
  weight once you no longer run `copier update`.
- `scripts/report_seeded_changes.py` and its output `.copier-seeded-changes.md`
  — the seeded-file report a `copier update` writes; nothing writes it after
  detaching.
- the `applying-template-updates` skill and `docs/deployment/template-updates.md`
  — the procedure for the weekly template update pull request, which a
  detached fork never receives. The `sed` line removes the page's nav
  entry (`docs.yml` runs `mkdocs build --strict`, which fails on a
  dangling entry); Step 3 drops the release-process page's link to it.

**Keep** your own CI and release workflows: `ci.yml`, `codeql.yml`,
`coverage-status.yml`, `docs.yml`, `release-prepare.yml`, `release.yml`, and
`release-notes-publish.yml`. (The release pair still needs the `RELEASE_TOKEN`
secret; only its `copier-update` justification is gone.
`release-notes-publish.yml` is deterministic — no Claude dependency — and
still redeploys canonical `docs/releases/` pages on merge.) The remaining
template-owned skills under `.agents/skills/` (`authoring-issues-prs`,
`code-review`, `config-contract`, `logging-standard`, `releasing`,
`repository-protection`, `tool-registration`, `writing-release-notes`) and
their `.claude/skills/<name>`
symlinks are independent of Claude review wiring; retain or remove each
according to the detached fork's process — a fork that keeps using Claude
Code, for instance, has no reason to drop the symlinks even after detaching.

## Step 3 — Scrub template-tracking guidance from `AGENTS.md` and its skills

```bash
# -i.bak + rm keeps this portable across GNU sed (Linux) and BSD sed (macOS),
# which disagree on the in-place flag's syntax. Runs over AGENTS.md, the
# three skills that still carry copier-update wording after the task-shaped
# sections moved into portable skills under .agents/skills/ (#486), and the
# release-process page (its link to the template-update page): a detached
# fork owns those files too, so the same scrub applies to each. The rule on
# `applying-template-updates` drops the AGENTS.md Skills bullet for the
# skill Step 2 removed.
for f in AGENTS.md .agents/skills/releasing/SKILL.md .agents/skills/config-contract/SKILL.md .agents/skills/tool-registration/SKILL.md docs/deployment/release-process.md; do
  sed -i.bak \
    -e '/<!-- TEMPLATE-TRACKING-START -->/,/<!-- TEMPLATE-TRACKING-END -->/d' \
    -e '/<!-- ===== TEMPLATE-OWNED SECTIONS BELOW/d' \
    -e '/<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->/d' \
    -e '/^- `applying-template-updates` — /d' \
    -e 's/ Kept across copier update\.//' \
    -e 's/ on top of the shipped defaults survive `copier update`\./ on top of the shipped defaults are yours to maintain./' \
    -e 's/ are preserved across `copier update`\./ are domain-owned./' \
    -e 's/The block is preserved across `copier update`\./The block is domain-owned./' \
    -e 's| on every `copier copy`/`copier update` and re-verified by| whenever config fields change, and re-verified by|' \
    "$f" && rm -f "$f.bak"
done
```

This deletes the template-coupled sections — the bot-reviewer merge-gate
paragraph, **Shared Infrastructure**, **Contributing fixes upstream**, and the
releasing skill's **Release notes pages** section — and strips the
copier-update wording that the `releasing`, `config-contract`, and
`tool-registration` skills carry and that no longer describes a detached
fork: the `TEMPLATE-OWNED SECTIONS` banner fences (a fork owns every section,
so the template/domain split is moot), the "Kept across copier update" notes
on the DOMAIN blocks, the remaining "preserved/survive across copier update"
notes (the pre-commit defaults, the `Dockerfile` sentinels, the
`scripts/stamp_manifests.py` release-manifest sentinels, and the upstream
sentinel),
and the copier-specific trigger on the config-wizard spec generation note (the
generator still runs in a fork, just not on a copier lifecycle event).
The fork-neutral contributor guidance
(Conventions, the PR acceptance gates, the Logging Standard skill, the config
contract skill, GitHub Review Types) is kept. If your fork added its own
`.claude/CLAUDE.md`, apply the same scrub there; the same goes for a
project-owned addition to `AGENTS.md` outside the DOMAIN blocks, or an
extra project-owned skill under `.agents/skills/`, if either picked up
copier-update or template-tracking wording of its own.

## Step 4 — README cleanup (optional)

These leftover references are harmless but now misleading:

- The **Template** badge at the top of `README.md` (the
  `![Template](https://img.shields.io/badge/dynamic/yaml?...&label=template)`
  entry) points at the now-deleted `.copier-answers.yml`. Remove it.
- In the secrets table, the `RELEASE_TOKEN` row lists `copier-update.yml` as a
  consumer. Drop that workflow from the row.
- The `### \`uv.lock\` refresh after \`copier update\`` subsection no longer
  applies. Remove it.
- The **Contributing** section names the `applying-template-updates` skill
  and links `docs/deployment/template-updates.md`, both removed in Step 2.
  Drop those two references.

## You are now standalone

Remove this guide (it no longer applies once detached) and commit the result:

```bash
rm -f FORKING.md
git add -A
git commit -m "chore: detach from fastmcp-server-template"
```

Future template or `fastmcp-pvl-core` fixes are no longer delivered
automatically — pull in anything you want by hand.
