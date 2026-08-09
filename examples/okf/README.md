# OKF example pack

Templates and prompts for authoring an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog)
(OKF) bundle with markdown-vault-mcp. Copy the pieces you want into your vault
and prompt folders; nothing here is loaded automatically.

See the [OKF guide](../../docs/guides/okf.md) for the full picture (detection,
the trust model, the migration ratchet, the enforced write layer, and export).

## What OKF is, in one paragraph

OKF is a lightweight convention over markdown-with-frontmatter: every note
carries a `type`, an optional lifecycle `status`, provenance (`sources`,
`generated`), and human attestations (`verified`). markdown-vault-mcp reads
those fields to annotate and rank results, and — when you opt in — to enforce
provenance on writes. OKF describes *what each note is*; it does not tell you
how to organize your vault, so it composes with methods like
[PARA](../para/README.md) and [Zettelkasten](../zettelkasten/README.md).

## Setup

1. Declare the bundle. Put `templates/index.md` at your vault root and keep its
   `okf_version` line. Under the default `MARKDOWN_VAULT_MCP_OKF_MODE=auto`,
   that declaration is what switches OKF read semantics on. (Use `on` to force
   them without a declaration, or `off` to disable them entirely.)
2. Author notes from `templates/`. Every template already carries a non-empty
   `type`, so a vault built from them passes `okf_validate` from the first note.
3. Optional: enable the enforced write layer with
   `MARKDOWN_VAULT_MCP_OKF_WRITE=true` so the server stamps `generated`
   provenance, invalidates `verified` on content changes, exposes the
   `okf_verify` tool, and keeps each folder's `log.md` / `index.md` current.

## Templates

| File | `type` | Use |
|------|--------|-----|
| `templates/index.md` | — | Bundle-root declaration (`okf_version`). Regenerate its listing with `okf_generate_index`. |
| `templates/concept.md` | `Concept` | A typed, sourced note — the workhorse. |
| `templates/capture.md` | `Capture` | A quick capture that is conformant from note one; triage assigns the real type later. |

## Prompts

Copy into your `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER`:

| File | Does |
|------|------|
| `prompts/okf-author-concept.md` | Draft a new conformant note with `type`, `description`, and cited `sources`. |
| `prompts/okf-verify-note.md` | Review a note and record a human attestation via `okf_verify`. |
| `prompts/okf-triage-stale.md` | Surface `deprecated` / stale notes and propose refresh or retirement. |
| `prompts/okf-migrate-vault.md` | Walk an existing vault through the migration ratchet: audit → declare → enrich → mechanical transforms. |
