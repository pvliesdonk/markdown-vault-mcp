---
description: "Review a note and record a human attestation with okf_verify"
arguments:
  - name: path
    description: "Vault-relative path to the note to review. Example: 'concepts/rrf.md'"
    required: true
tags: ["write"]
---

You are recording a **human review** of an OKF note. This requires the enforced
write layer (`MARKDOWN_VAULT_MCP_OKF_WRITE=true`); the `okf_verify` tool is
hidden otherwise.

How the attestation is confirmed depends on `MARKDOWN_VAULT_MCP_OKF_VERIFY`. In
the default `elicit` mode the tool asks the **human** to confirm the review
through a client elicitation prompt and records nothing unless they answer yes —
you cannot answer it on their behalf. In `trust-auth` mode it attributes to the
authenticated caller with no prompt (and refuses when the server has no auth).

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
note's `verified` list and promotes its `trust_tier` to `human-reviewed`. In the
default `elicit` mode the human must confirm the client's prompt for the entry
to be written; relay that a confirmation is needed rather than trying to answer
it yourself.

Report the returned `verifier` and `verified_count`. If the tool refuses —
because the human declined or the client cannot show the elicitation, or (in
`trust-auth` mode) because the server has no authentication — say so rather than
editing `verified` by hand.
