<!-- scripts/copier_update_prompts/job_b.md -->
# Job B: copier-update changelog triage

You are an agent helping an operator review a `copier-update` PR for the
`markdown-vault-mcp` downstream consumer of `pvliesdonk/fastmcp-server-template`.

## Context

The template was advanced from `${PREVIOUS_COMMIT}` to `${NEW_REF}`. The
template's `CHANGELOG.md` between these two refs has new entries the operator
hasn't seen before. Your job is to triage each entry: classify it by
operator-relevance and write a one-paragraph summary.

You have read-only access to:
- A clone of the template repo at `/tmp/template`. Read
  `/tmp/template/CHANGELOG.md` at the new ref.
- The downstream working tree (read-only — do not modify).

## Per-entry task

For each entry in the CHANGELOG between `${PREVIOUS_COMMIT}` (exclusive) and
`${NEW_REF}` (inclusive), classify as one of three:

- **`ships-automatically`** — the change is in a template-owned file (NOT in
  `_skip_if_exists`). Downstream gets it via this `copier update` with no
  further action. Examples: new env var documented in `docs/configuration.md`
  (template-owned), workflow change in `release.yml.jinja`.

- **`needs-opt-in`** — downstream must wire something to benefit. Examples:
  template wires a new helper in a `_skip_if_exists` file (`_server_deps.py`,
  `tools.py`, etc.); template adds a new copier variable that defaults to a
  conservative value but downstream may want to override; template adds a new
  scaffold file that downstream may want to populate.

- **`informational`** — internal template-side change with no downstream-facing
  effect. Examples: changes to `template-ci.yml`, release-pipeline plumbing,
  repo-internal docs (`docs/superpowers/`), CHANGELOG-only edits.

## Reading copier.yml to disambiguate

To decide between `ships-automatically` and `needs-opt-in`, read
`/tmp/template/copier.yml` at `${NEW_REF}` and check `_skip_if_exists` and
`_exclude`. Files listed in `_skip_if_exists` are downstream-owned starter
files — changes in those files do NOT flow to downstream automatically.

**Split-ownership entries are the dangerous case.** If one CHANGELOG entry
touches BOTH a template-owned file and a `_skip_if_exists` file, only half of
it arrives, and the half that does arrive can assert something the missing
half was supposed to make true. Classify these `needs-opt-in` even when the
template-owned half looks self-sufficient, and make the summary name the
skip-listed file plus the exact edit downstream has to make by hand. The
shape to look for: a re-rendered config file declaring behaviour a
skip-listed script implements — template v3.1.0 (#298) added `uv.lock` to
`pyproject.toml`'s `[tool.semantic_release] assets` and the matching
`_bump_lockfile()` to the then-skip-listed `scripts/bump_manifests.py`, so
instances with an extended copy of that script started committing lockfiles
whose version lagged the tag (#325).

## Output

Write the following JSON to `/tmp/agent-job-b.json`:

```json
{
  "status": "ok",
  "entries": [
    {
      "pr_number": 89,
      "title": "wire register_server_info_tool",
      "classification": "needs-opt-in",
      "summary": "The template now wires `get_server_info` (via `register_server_info_tool`) by default in `make_server()`. This change ships through automatically since `server.py.jinja` is template-owned. To wire upstream version reporting, populate the DOMAIN-UPSTREAM sentinel inside the call. See template's CLAUDE.md `## Server Info Tool` section."
    }
  ]
}
```

If the CHANGELOG between the two refs is empty (template advanced without
publishing CHANGELOG entries — unusual), write `{"status": "ok", "entries": []}`.

If you encounter an unrecoverable error, write
`{"status": "error", "message": "<details>"}`. If rate-limited, write
`{"status": "rate_limited", "message": "<details>"}`.

## Constraints

- Tool access: file read on `/tmp/template`, `git -C /tmp/template log` and
  `git -C /tmp/template show`, `git -C /tmp/template checkout <ref>`. NO file
  writes (other than the output JSON), NO git commits, NO network calls.
- Use the CHANGELOG.md content directly. Do not infer entries from `git log`
  alone — if a PR isn't in the CHANGELOG, it's not in scope (the CHANGELOG is
  managed by python-semantic-release and is the canonical operator-facing
  feed).
- Per-entry summaries should be operator-actionable: tell the operator what
  they got, why it matters, and what (if anything) they should do.
