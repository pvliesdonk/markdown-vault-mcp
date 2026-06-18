<!-- scripts/copier_update_prompts/job_a.md -->
# Job A: copier-update conflict resolution

You are an agent helping an operator review a `copier-update` PR for the
`markdown-vault-mcp` downstream consumer of the `pvliesdonk/fastmcp-server-template`.

## Context

The template was advanced from `${PREVIOUS_COMMIT}` to `${NEW_REF}`. `copier
update --conflict=inline` ran and left conflict markers in some files because
copier's 3-way merge could not auto-resolve them. Your job is to triage these
conflicts: auto-commit the trivial mechanical ones, and write analysis comments
for the rest.

You have access to:
- The downstream working tree at the current branch state (with conflict markers).
- A clone of the template repo at `/tmp/template`. Use `git -C /tmp/template
  checkout ${PREVIOUS_COMMIT}` and `git -C /tmp/template checkout ${NEW_REF}`
  to inspect the template's state at either ref.

## Conflict files

The following files contain `<<<<<<< before updating ... >>>>>>>` markers:

${CONFLICT_FILES_LIST}

## Per-conflict task

For each `<<<<<<<` / `>>>>>>>` region in each conflict file, decide one of:

### Auto-resolvable

A conflict is **auto-resolvable** if and only if you can articulate the precise
transformation that resolves it in **one sentence** with **no ambiguity** about
which downstream lines belong where. Examples of articulations that pass this
bar:

- "Moved 12 lines of `remote OIDC mode` customisation from a mid-file location
  into the new `DOMAIN-OIDC-EXTRA` block at end-of-file; template body accepted
  unchanged."
- "Renamed sentinel marker `DOMAIN-DOCKER-START` to `DOMAIN-DOCKER-EXTRA-START`
  to match the template's new naming convention; downstream content unchanged."

If you cannot articulate the transformation in one sentence — or if there is any
ambiguity about which downstream lines should go where — the conflict is NOT
auto-resolvable.

For auto-resolvable conflicts: write the resolved file to disk (replacing the
conflict markers and surrounding region with the final content). Do NOT commit
yet — accumulate auto-resolutions and commit once at the end.

### Needs review

For non-auto-resolvable conflicts: leave the markers in place. Write a
`recommended_resolution` (one paragraph, concrete) and `reasoning` (one
paragraph, explains why it can't be auto-resolved).

## Final commit

If you applied any auto-resolutions, run **once** at the end:

```bash
git add <files-you-modified>

# Verify ONLY the conflict files you intended to stage are staged.
# The list below MUST be a subset of CONFLICT_FILES_LIST. If it isn't,
# something else got staged — abort, do NOT commit, and report status
# error in the JSON. Anything else risks pushing unrelated edits to
# the PR branch under the auto-resolve commit.
git diff --cached --name-only

# Reset git identity so both author and committer are github-actions[bot].
# claude-code-action overwrites user.name/email to claude[bot]; --author alone
# would fix the author field but leave the committer as claude[bot].
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -m "auto-resolve N trivial conflicts (claude-code)"
```

Capture the commit SHA via `git rev-parse HEAD`.

## Output

Write the following JSON to `/tmp/agent-job-a.json` (NOT to stdout — write the file):

```json
{
  "status": "ok",
  "auto_resolved": [
    {
      "file": "docs/deployment/oidc.md",
      "region": "L42-L78",
      "articulation": "Moved 12 lines of remote OIDC mode customisation into the new DOMAIN-OIDC-EXTRA block; template body accepted unchanged.",
      "commit_sha": "abc1234"
    }
  ],
  "needs_review": [
    {
      "file": "docs/guides/authentication.md",
      "region": "L120-L145",
      "recommended_resolution": "Lift the renamed anchor `#known-limitations-oidc-session-lifetime` into the EXTRA block while accepting the template's anchor in the body.",
      "reasoning": "Conflict is between template's new MCP OAuth refresh section content and downstream's renamed cross-reference anchor. The rename affects three internal links so resolving requires choosing between (a) preserving the rename and updating template-body links to match, or (b) reverting to template anchor and dropping the rename. Operator preference."
    }
  ]
}
```

If you encounter an unrecoverable error (e.g. file unreadable, git commit
failed), write `{"status": "error", "message": "<one-line description>"}`
instead and exit. If you receive a 429 / rate-limit response from any tool,
write `{"status": "rate_limited", "message": "<details>"}`.

The aggregator that consumes this JSON validates schema strictly. Any
deviation from the shapes above will cause the section to render as
"Agent failed" in the PR body.

## Constraints

- Tool access: file read/write on the working tree, file read on `/tmp/template`,
  `git -C /tmp/template <subcommand>`, `git status`, `git diff`, `git add <path>`,
  `git config user.name`, `git config user.email`, `git commit -m '<msg>'`,
  `git rev-parse`. NO `git push`, NO branch operations, NO network calls.
- Do not modify files outside the conflict files listed above.
- Sentinel-block awareness: lines inside `<!-- DOMAIN-*-START -->` ... `<!-- DOMAIN-*-END -->`,
  `<!-- PROJECT-*-START -->` ... `<!-- PROJECT-*-END -->`, `# CONFIG-*-START` ...
  `# CONFIG-*-END`, `# PROJECT-*-START` ... `# PROJECT-*-END` are downstream-owned by
  contract. When resolving conflicts inside these blocks, take the downstream side
  unless there is a clear template-side correction (e.g. the marker name itself
  was renamed by the template).

If the auto-resolved list is empty (no trivial cases found), do NOT make any
git commit; the `auto_resolved` array in the JSON is `[]`.
