---
description: Migrate from automatic OKF provenance stamping to explicit authoring.
kind: how-to
---

# Migrate OKF provenance writes

This breaking change affects deployments using `MARKDOWN_VAULT_MCP_OKF_WRITE=true`. Ordinary writes no longer overwrite `generated` or delete `verified`, and credentials no longer supply an author. Existing records stay in the note. A retained review is a claim whose applicability to current content is unknown; it is not a certification of a later edit.

The deprecated alias now enables `OKF_AUTHOR`, review, index maintenance and write logging. Explicit individual settings override it. Set `MARKDOWN_VAULT_MCP_OKF_AUTHOR=false` to hide explicit authoring while keeping the other alias defaults, or configure each capability independently.

To record a producer, call [`okf_author`](../reference/tools/writer.md#okf_author) with the note's current etag and an explicit producer. Review is a separate operation. For background on evidence and review, see [Open Knowledge Format](okf.md).

Python integrations must remove uses of the retired stamping helpers, `Principal.okf_actor`, the `DocumentManager` enrichment hook, `OkfCapabilities.legacy_stamping`, and `OkfWriteIntent.actor`. `Vault.okf_write_state` and the corresponding write-state fields in stats and `config://vault` are also removed. Configure and inspect individual capabilities instead.
