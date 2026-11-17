---
description: "The Vault class, the library's entry point, and how to construct and configure it."
kind: reference
---

# Vault

The `Vault` class is the primary public API for the library. Most MCP tools, CLI commands, and direct integrations go through this class; portable file references also have a standalone reader below. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](facets.md)).

## Quick Start

Construction uses `VaultSettings`: pass `source_dir` plus a `VaultSettings` carrying the configuration knobs. Collaborator objects (`embedding_provider`, `summarizer`, `git_strategy`, `on_write`, and `chunk_strategy`) stay explicit keywords.

```python { .run data-expect="results" }
from pathlib import Path
from markdown_vault_mcp.vault import Vault, VaultSettings

# Basic read-only vault (all settings at their defaults)
vault = Vault(source_dir=Path("/path/to/vault"))
stats = vault.index.build_index()
print(f"Indexed {stats.documents_indexed} documents")
vault.close()

# Configured vault: knobs travel on VaultSettings
vault = Vault(
    source_dir=Path("/path/to/vault"),
    settings=VaultSettings(
        read_only=False,
        index_path=Path("/path/to/index.db"),
        exclude_patterns=[".obsidian/**"],
    ),
)

# Build (or refresh) the index before the first search: construction never builds it
vault.index.build_index()

# Search (reader facet)
results = vault.reader.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document (reader facet)
note = vault.reader.read("Journal/note.md")
print(note.content)
vault.close()
```

## Ownership

`close()` releases what the vault opened: its index, its writer thread and the write-callback queue. The collaborators you pass in (`embedding_provider`, `summarizer`, `git_strategy`, `on_write`) stay yours. Close them after the vault, so a git push flush follows the drained commits.

Pass `vector_store=` to use a vector store you opened (the vault never closes or rebuilds it) or `vector_backend=` to choose the backend the vault opens and closes its store with.

## Migrating from 4.x

The 31 configuration keywords on `Vault` have been removed. Move each value to the same-named field on `VaultSettings`. Replace `Vault(source_dir=root, read_only=False, index_path=index)` with:

```python { .fragment }
vault = Vault(
    source_dir=root,
    settings=VaultSettings(read_only=False, index_path=index),
)
```

Old keywords now raise `TypeError`, including when their values match the defaults or when `settings` is also supplied. `source_dir` and the five collaborator keywords remain on `Vault`.

Omitting `settings` (or passing `None`) uses `VaultSettings()`. Library defaults are unchanged: read-only, no chunk overlap, and no overwrite protection once writes are enabled. Server configuration continues to use its own defaults through [configuration assembly](config.md).

## Build completion

Synchronous, asynchronous, and legacy background builds use one build lifecycle. The Future from `vault.index.build_index_async()` completes after the scan, completion-marker write, and readiness update. Marker-write failures raise through this Future. Pending builds can be cancelled; running builds finish normally. The legacy `start_background_build_index()` method schedules work once on the same writer.

## Index freshness after writes

File writes complete before returning; index updates run on the background writer. For a following library search or graph read, use `vault.index.wait_for_drain(timeout=60)` and check whether it returns `True`. A disk `read` does not need this wait.

Link conversion, index generation, rename with `update_links=True`, and folder move perform their own queued refresh before reading index data. They fail before mutation if that refresh fails or exceeds 60 seconds. A timeout raised by the refresh job propagates as that job error; the generic refresh-timeout message is reserved for expiration of the wait budget. The wait covers prior writes; it does not isolate concurrent edits. The same 60-second budget covers the preceding build and its readiness update, including synchronous and background builds. A failed or cancelled build blocks these mutations; an index with no scheduled build still supports rename and folder move. OKF generators still reject an index that was never built. Healthy notes continue to receive vector updates when another note in their refresh batch fails. Retrying that batch reuses matching stored vectors, avoiding repeated provider requests for unchanged notes. Direct `DocumentManager` integrations can supply the `sync_index` callback to provide the same boundary.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config_sections.vault_settings.VaultSettings

::: markdown_vault_mcp.vault.Vault
<!-- vale on -->


## Portable file references

Use `markdown_vault_mcp.references.ReferenceReader` to cite files without
transferring their bodies. It works without a built index or Git repository.
The MCP equivalents are [`get_file_reference` and `resolve_file_reference`](../tools/reader.md#get_file_reference).
Set `MARKDOWN_VAULT_MCP_REFERENCE_NAMESPACE` to enable those calls. Library
callers pass the namespace directly. Choose a unique, stable name for each
vault, keep it across moves and Docker deployments, and never reuse it for
an independent vault. Namespace equality checks configuration; it does not
authenticate a server.

```python { .fragment }
from dataclasses import asdict
from markdown_vault_mcp.references import FileReference, ReferenceReader

refs = ReferenceReader(vault.source_dir, "urn:vault:research")
ref = refs.get_file_reference("notes/report.md", include_sha256=True)
saved = asdict(ref)  # Store this small JSON-compatible value in any tracker.
result = refs.resolve_file_reference(FileReference(**saved), include_content=True)
if result.status == "verified":
    import base64
    raw_bytes = base64.b64decode(result.content_base64)
```

Pass `attachment_extensions=` with the same allowlist as your vault when it
uses a custom policy. The reader supports notes and allowed binary attachments
under the [existing path rules](../../security-model.md). It hashes complete
raw bytes, including frontmatter and original line endings, without decoding
or parsing the file. The explicitly named `sha256` is a lowercase SHA-256
hex digest, equal to the `etag` returned by `read` for the same raw file bytes;
`size_bytes` counts the same bytes. `include_sha256=False` saves
only a locator and byte count. Optional `provenance` is a caller-supplied label
of at most 1024 characters; the reader infers no provenance or Git revision.

The reference has `version=1`, `namespace`, `path`, `size_bytes`, `sha256`
(null when omitted) and `provenance` (null when omitted). Save it unchanged.
The `version` field is required when restoring a saved reference; missing or
unsupported versions and malformed digests are refused. Paths are relative,
with slash separators and no empty, `.` or `..` components. Use the path that
the vault's `read` accepts.

Resolution returns the original `reference`, `status`, `detail` and optional
`content_base64`:

| Status | Meaning |
| --- | --- |
| `verified` | The complete current bytes match the saved digest and byte count. |
| `unverified` | The path exists with the saved byte count, but no digest was saved. |
| `changed` | Byte count or digest differs; no body is returned. |
| `missing` | The saved path is missing, including after a rename; no body is returned. |
| `wrong_vault` | The configured namespace differs; the file is not opened. |

A rename does not update a reference. Without a per-file ID, rename and
deletion cannot be distinguished. A path reused for other bytes reports
`changed` when a saved digest proves the difference. Path-only references
cannot establish content identity: a size change reports `changed`, but a
same-size edit remains `unverified`. A digest proves matching bytes, not file
lineage or retention.
A saved path that becomes a directory, or a file type no longer admitted by
the attachment allowlist, raises `InvalidRequestError` instead of a status.

`include_content=True` requires a saved digest and returns the exact verified
raw bytes as base64 for both notes and attachments. The MCP tools apply the
configured note or attachment body-read cap; library callers can set
`max_content_bytes` (default 262144, `0` for unlimited). Hash-only operations
stream files without a body-read cap. When requesting content, a changed
file reports `changed` even if its current body exceeds the cap; an unchanged
file over the cap returns a limit error. A concurrent change refuses the call;
retry it rather than saving a partial result.

No historical copy is retained. Without another retained copy, old bytes may
be unavailable. A standalone reader defaults to reporting history unavailable
in its context; pass `history_available=True` only when the caller has Git
backing to inspect. The MCP adapter uses the vault's configured Git strategy
for this context. This capability does not guarantee that saved bytes are in
history. For a Git-backed vault, `get_history` may help find an earlier
commit; `read(path, revision=...)` can retrieve historical notes using its
existing rename-aware rules. Historical binary reads are not provided by that
tool. Any candidate recovered through Git or another archive must be checked
against the saved raw-file SHA-256 and byte count before it is accepted.
Git line-ending normalization can prevent a historical candidate from matching
the saved raw digest.
The reference tools never silently return a different revision or current
content in place of the saved bytes.
