---
description: Migrate from automatic OKF provenance stamping to explicit authoring.
kind: how-to
---

# Migrate OKF provenance writes

This breaking change affects deployments using `MARKDOWN_VAULT_MCP_OKF_WRITE=true`. Ordinary writes no longer overwrite `generated` or delete `verified`, and credentials no longer supply an author. Existing records stay in the note. A retained review is a claim whose applicability to current content is unknown; it is not a certification of a later edit. Ordinary writes also store a supplied `verified` entry as given, so any writer can supply a `human:` review claim. `OKF_VERIFY=elicit` guards only the explicit `okf_verify` operation.

Line endings and a BOM follow the existing rules. An edit, append or link rewrite stores the note's existing text with `\n` line endings, as `read` shows it, and keeps an unchanged frontmatter block byte for byte. Text you supply is stored as given, except that a note upload drops a leading BOM.

The deprecated alias now enables `OKF_AUTHOR`, review, index maintenance and write logging. Explicit individual settings override it. Set `MARKDOWN_VAULT_MCP_OKF_AUTHOR=false` to hide explicit authoring while keeping the other alias defaults, or configure each capability independently.

To record a producer, call [`okf_author`](../reference/tools/writer.md#okf_author) with the note's current etag and an explicit producer. Review is a separate operation. For background on evidence and review, see [Open Knowledge Format](okf.md).

Python integrations must remove uses of the retired stamping helpers, `Principal.okf_actor`, the `DocumentManager` enrichment hook, `OkfCapabilities.legacy_stamping`, and `OkfWriteIntent.actor`. The write-state fields in stats and `config://vault` still report whether the alias is configured and effective; an effective alias stamps nothing.

Service credentials can no longer record human reviews, even with elicitation. Use a human principal for human confirmation, or enable `MARKDOWN_VAULT_MCP_OKF_MACHINE_REVIEW=true` for named service checks while review is enabled.
