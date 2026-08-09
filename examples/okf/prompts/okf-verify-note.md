---
description: "Review a note and record a human attestation with okf_verify"
arguments:
  - name: path
    description: "Vault-relative path to the note to review. Example: 'concepts/rrf.md'"
    required: true
tags: ["write"]
---

You are recording a **human review** of an OKF note. Verification is an
attributable act, so this requires the server to be running with authentication
and the enforced write layer (`MARKDOWN_VAULT_MCP_OKF_WRITE=true`); the
`okf_verify` tool is hidden otherwise.

## Step 1: Read the note

Call `read(path=$path)`. Note its `okf` annotation — in particular the current
`trust_tier` and whether it is `stale` or `deprecated`.

## Step 2: Review the content, do not rubber-stamp

Check the claims against the note's own `sources` and against the vault:

- Are the assertions still accurate?
- Do the `sources` support them?
- Is anything out of date (a past `stale_after`, superseded facts)?

If the note needs changes, make them first with `edit` / `write` and stop —
a content change invalidates any prior verification, so verify only once the
bytes are right.

## Step 3: Attest

When you are satisfied the note is correct as written, call
`okf_verify(path=$path)`. It appends a `{by: human:<subject>, at}` entry to the
note's `verified` list and promotes its `trust_tier` to `human-reviewed`.

Report the returned `verifier` and `verified_count`. If the server has no
authentication, `okf_verify` refuses — say so rather than editing `verified`
by hand.
