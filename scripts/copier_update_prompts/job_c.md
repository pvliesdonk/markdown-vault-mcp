<!-- scripts/copier_update_prompts/job_c.md -->
# Job C: copier-update excluded-file evolution

You are an agent helping an operator review a `copier-update` PR for the
`markdown-vault-mcp` downstream consumer of `pvliesdonk/fastmcp-server-template`.

## Context

Some files in the template are listed under `_exclude` in `copier.yml` — they
exist as `.jinja` source in the template repo for maintainer reference but are
NEVER rendered to downstream. Examples: `tests/test_tools.py`, `docs/design/index.md`.

Downstream often has its own version of these files (created at scaffold time,
or hand-written later). Because the template doesn't render these to
downstream, the upstream `.jinja` source can evolve without any changes
flowing through. This job triages whether downstream's local copy of any such
file would benefit from porting the upstream evolution.

You have read-only access to:
- A clone of the template repo at `/tmp/template`. Use `git -C /tmp/template
  diff ${PREVIOUS_COMMIT}..${NEW_REF} -- <path>` to see what changed.
- The downstream working tree at the current state (read-only — to inspect
  the local copy).

## Per-file task

Read `/tmp/template/copier.yml` at `${NEW_REF}` to get the `_exclude` list.
For each entry that is a `.jinja`-suffixed FILE (skip directory globs like
`docs/superpowers`, stock excludes like `.git` / `*.pyc` / `~*`, and
non-`.jinja` files like `uv.lock`):

1. Run: `git -C /tmp/template diff ${PREVIOUS_COMMIT}..${NEW_REF} -- <path>`
2. If the diff is empty → skip (not in output).
3. If non-empty, classify the diff:
   - **`recommend-port`** — the change adds operator-relevant value (new
     test case, doc improvement, bug fix in a script). Downstream's local
     copy of this file would benefit from porting the change manually.
   - **`skip`** — template-maintainer concern only. Downstream doesn't need
     to do anything (e.g. internal refactor, comment cleanup, change to a
     test that downstream doesn't have).
   - **`informational`** — small change with mixed value; downstream might
     find it interesting but no clear action recommended.

For each non-skipped file, also write `summary` (one sentence on what
changed) and `diff_summary` (terse — line counts, function names changed,
etc.; max ~80 chars).

## Output

Write to `/tmp/agent-job-c.json`:

```json
{
  "status": "ok",
  "files": [
    {
      "file": "tests/test_tools.py",
      "classification": "recommend-port",
      "summary": "Template added a smoke test for the new register_server_info_tool helper that downstream's local test_tools.py doesn't have.",
      "diff_summary": "+15 lines test fixture for get_server_info"
    }
  ]
}
```

If no excluded files evolved between the two refs, write
`{"status": "ok", "files": []}`.

If unrecoverable error: `{"status": "error", "message": "..."}`.
If rate-limited: `{"status": "rate_limited", "message": "..."}`.

## Constraints

- Tool access: file read, `git -C /tmp/template log` / `diff` / `show` /
  `checkout`. NO file writes (other than the output JSON), NO git commits,
  NO network calls.
- Skip directory globs in `_exclude`. Only process individual file paths
  ending in `.jinja`.
- The downstream working tree may not contain the same paths the template's
  `_exclude` references (e.g. downstream may have deleted their `tests/test_tools.py`).
  When that's the case, still report the upstream evolution but note in the
  summary that downstream doesn't currently have a local copy.
