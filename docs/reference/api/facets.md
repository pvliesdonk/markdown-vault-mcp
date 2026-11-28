---
description: "The reader, writer, graph and index facets that carry the library's operations."
kind: reference
---

# Facets

The read / write / graph / index operations live on four cohesive facets,
reached through the `reader` / `writer` / `graph` / `index` accessors on the
[`Vault`](vault.md) composition root.

```python { .run data-expect="results" }
from pathlib import Path
from markdown_vault_mcp.vault import Vault, VaultSettings

# The library is read-only by default; writes need read_only=False.
vault = Vault(source_dir=Path("/path/to/vault"), settings=VaultSettings(read_only=False))
vault.index.build_index()

# Reader facet — search / read / list / metadata
results = vault.reader.search("query text", limit=10)
note = vault.reader.read("Journal/note.md")

# Writer facet — write / edit / delete / rename / attachments
vault.writer.write("Journal/new.md", "# New note")

# Graph facet — backlinks / outlinks / orphans / paths
backlinks = vault.graph.get_backlinks("Journal/note.md")

# Index facet — build / reindex / embeddings / readiness
vault.index.reindex()
```

## ReaderFacet

Search, exact queries, read, listing, table-of-contents, similarity, context,
history/diff, revision reads, and attachment reads. Use `read_attachment_bytes`
for raw bytes; `read_attachment` returns base64 content and metadata. Exact
query methods check library index readiness and raise `IndexUnavailableError`
if unavailable; an unavailable index is never an exact zero. They accept the
[query types](types.md#exact-query-types) and return the same projections as the
[keyword backend](storage.md#exact-keyword-queries).

```python { .fragment }
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery

request = NoteQuery(query="release", scope="body", limit=20)
page = vault.reader.query_documents(request)
count = vault.reader.count_documents_matching(request)
grouped = vault.reader.aggregate_documents(request, NoteGrouping(by="folder"))
```

Path discovery uses the same indexed metadata and readiness gate. It reports
approximate coverage separately from output truncation:

```python { .fragment }
from markdown_vault_mcp.query_types import PathQuery

paths = vault.reader.search_paths(PathQuery(query="releaze", fuzzy=True))
# Inspect paths.coverage and paths.reasons before treating an empty list as absence.
```

Exact paths rank first. Literal prefix matches rank ahead of matches within a path;
both tiers use `str.casefold()` before comparing. Typo matches follow those tiers.
Approximation is ASCII-only, bounded to 10,000 candidates and 200 ms; output
defaults to 100 items (maximum 1000). Unicode retains direct matching with `str.casefold()`.

Invalid indexed fields raise `InvalidRequestError`. Existing ranked search
continues to support computed OKF filters.

<!-- vale off -->
::: markdown_vault_mcp.facets.reader.ReaderFacet
<!-- vale on -->

## WriterFacet

Create, edit, append, delete, rename, and attachment writes, and one text
replacement across many notes.

<!-- vale off -->
::: markdown_vault_mcp.facets.writer.WriterFacet
<!-- vale on -->

### Replacing text across notes

`preview_replace` computes one replacement across the notes a request covers
and writes nothing. `apply_replace` takes the plan from that preview,
recomputes it, and writes each planned note when the notes still give that
plan. Matches cover each note's body and never its frontmatter. Each write
behaves like `edit`, OKF stamping and upkeep included.

```python { .fragment }
from markdown_vault_mcp.managers.text_replace import ReplaceRequest

request = ReplaceRequest(
    pattern=r"(\d{2})/(\d{2})/(\d{4})",
    replacement=r"\3-\2-\1",
    folders=("Journal",),
    regex=True,
)
preview = vault.writer.preview_replace(request)
# preview.notes: each note it would change, with a count and, for the first
# ten, a diff; preview.refused: notes it would leave alone, with the reason.
result = vault.writer.apply_replace(request, preview.plan)
# Each of result.notes is applied, skipped or refused.
```

`apply_replace` raises `StalePlanError` and writes nothing when a covered
note changed after the preview, or the request differs from the previewed
one; preview again and apply the new plan. A note that changes between that
check and its own write is reported `skipped` and left as it is.

A request covers at most 1,000 notes, reads at most 256 KiB per note and
8 MiB in total, changes at most 200 notes and makes at most 10,000
replacements. Past a bound, the request is refused with `InvalidRequestError`
rather than cut short. A regular expression gets 3 seconds across the whole
selection. A preview shows diffs for the first 10 changed notes, each cut at
3,000 characters; narrow `paths` or `folders` to see the rest.

<!-- vale off -->
::: markdown_vault_mcp.managers.text_replace.ReplaceRequest

::: markdown_vault_mcp.managers.text_replace.ReplacePreview

::: markdown_vault_mcp.managers.text_replace.PlannedNote

::: markdown_vault_mcp.managers.text_replace.NoteRefusal

::: markdown_vault_mcp.managers.text_replace.ReplaceResult

::: markdown_vault_mcp.managers.text_replace.NoteOutcome

::: markdown_vault_mcp.managers.text_replace.StalePlanError
<!-- vale on -->

## GraphFacet

Backlinks, outlinks, broken links, orphans, most-linked notes, and connection
paths.

<!-- vale off -->
::: markdown_vault_mcp.facets.graph.GraphFacet
<!-- vale on -->

## IndexFacet

Index build / reindex / embeddings (sync + async), readiness, and writer
status.

<!-- vale off -->
::: markdown_vault_mcp.facets.index.IndexFacet
<!-- vale on -->
