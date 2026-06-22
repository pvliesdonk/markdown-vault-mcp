# Forking: detaching from the template

This project was generated from the
[`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template)
copier template and, by default, **tracks** it: the weekly
`copier-update` workflow opens PRs that pull in template and `fastmcp-pvl-core`
improvements, and `CLAUDE.md` routes fixes back upstream.

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
rm -rf .gemini
rm -f scripts/copier_update_aggregator.py
rm -rf scripts/copier_update_prompts
```

What this removes and why:

- `copier-update.yml` — template-update automation; meaningless once detached.
- `claude.yml`, `claude-code-review.yml` — fleet review-bot wiring.
- `.gemini/` — gemini-code-assist fleet scope control.
- `scripts/copier_update_aggregator.py`, `scripts/copier_update_prompts/` —
  the orchestration that only `copier-update.yml` invoked; dead weight once
  that workflow is gone.

**Keep** your own CI and release workflows: `ci.yml`, `codeql.yml`,
`coverage-status.yml`, `docs.yml`, and `release.yml`. (`release.yml` still needs
the `RELEASE_TOKEN` secret; only its `copier-update` justification is gone.)

## Step 3 — Scrub template-tracking guidance from `CLAUDE.md`

```bash
# -i.bak + rm keeps this portable across GNU sed (Linux) and BSD sed (macOS),
# which disagree on the in-place flag's syntax.
sed -i.bak '/<!-- TEMPLATE-TRACKING-START -->/,/<!-- TEMPLATE-TRACKING-END -->/d' CLAUDE.md && rm -f CLAUDE.md.bak
```

This deletes the template-coupled sections — the bot-reviewer merge-gate
paragraph, **Shared Infrastructure**, and **Contributing fixes upstream** —
while keeping the fork-neutral contributor guidance (Conventions, the PR
acceptance gates, the Logging Standard, the config contract, GitHub Review
Types). If your fork added its own `.claude/CLAUDE.md`, apply the same scrub
there.

## Step 4 — README cleanup (optional)

These leftover references are harmless but now misleading:

- The **Template** badge at the top of `README.md` (the
  `![Template](https://img.shields.io/badge/dynamic/yaml?...&label=template)`
  entry) points at the now-deleted `.copier-answers.yml`. Remove it.
- In the secrets table, the `RELEASE_TOKEN` row lists `copier-update.yml` as a
  consumer. Drop that workflow from the row.
- The `### \`uv.lock\` refresh after \`copier update\`` subsection no longer
  applies. Remove it.

## You are now standalone

Remove this guide (it no longer applies once detached) and commit the result:

```bash
rm -f FORKING.md
git add -A
git commit -m "chore: detach from fastmcp-server-template"
```

Future template or `fastmcp-pvl-core` fixes are no longer delivered
automatically — pull in anything you want by hand.
