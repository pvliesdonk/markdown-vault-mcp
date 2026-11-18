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

### Selected bundle inspection

`inspect_bundle` reads one explicitly selected directory without building an
index. It reads the index for search state when ready. Untyped and malformed Markdown remain visible even when
index admission requires frontmatter fields. See the [security model](../../security-model.md)
for the filesystem boundary.

```python { .fragment }
from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest

observation = vault.reader.inspect_bundle(
    BundleInspectionRequest(root="knowledge", directory="concepts", entry_limit=100)
)
# Check state, enumeration and unavailable entries before treating the observation as complete.
```

`root` is vault-relative and required; `root=""` explicitly selects the vault.
`directory` is relative to that root. Absolute paths, parent components, NUL bytes,
backslashes and empty or dot components are rejected with `ValueError`.

The frozen `BundleInspection` contains the request, sorted `BundleEntry` values,
`state`, `enumeration`, `interpretation="caller_selected"`, and
`search_state="not_evaluated"`. This legacy scope-level field is unchanged;
each entry carries its own search state. Scope states distinguish `inspected`, `missing`
and `unavailable`. A missing configured source is `unavailable`; only a missing
directory within an accessible source is `missing`.

Each entry contains its vault-relative `path`, filename-based `role`, `state`,
`reason`, `parse_state`, `type_state`, `visibility`, `hidden_path` and `search_state`. Exact
`index.md` and `log.md` names mean `navigation` and `history`; the configured
conventions filename has role `support` unless it is `index.md` or `log.md`;
other `.md` files are `concept_candidate`.
Directories and allowed regular attachments have `directory` and `supporting`
roles. Roles describe observations and grant no conformance or write authority.

Complete Markdown reads distinguish `valid`, `invalid_encoding` and
`malformed_metadata`. A valid parse reports `type_state` as `absent`, `invalid`
or `present`; present means a nonempty string, without validating its vocabulary.
Content that was not inspected retains `not_inspected`. No body, metadata values or symlink
targets are returned.

Enumeration is `complete`, `entry_limit`, `unavailable` or `not_inspected`.
`complete` covers only immediate policy-visible enumeration, even if individual
entries remain unavailable or not inspected. Paths matching index exclusions
are labelled `index_excluded`, and dot paths have `hidden_path=true`; neither
label prevents inspection. Non-directory, non-symlink filenames outside the attachment
allowlist, `.git`, server-owned state directories and files, and
permission-denied children are omitted. The change-tracker file is omitted by exact path and the keyword index
by its own lock and journal files, so neighboring notes and other same-named
siblings stay visible. Alternate spellings of internal paths follow filesystem identity rules. A denied child makes enumeration unavailable without hiding readable
siblings, including when the note-read budget has been exhausted; a refused stat or open
consumes none of that budget. A directory
entry's visibility describes its own name, not the admission of its subtree. Visible symlinks have reason `symlink`; special files remain unavailable and are
never followed or read. A link with a name outside the allowlist is an unavailable `hidden_symlink` row. Its target is not returned and it is never followed. Check unavailable rows even when enumeration is complete. When a stat failure prevents determining whether an
omitted name is a visible directory, enumeration is `unavailable`.

The entry limit defaults to 100 (maximum 1000). Every visited entry, including
omitted entries, consumes the limit; one additional entry detects truncation.
No omitted-entry counts are returned. Markdown reads use at most 64 KiB per
file, or the smaller configured nonzero note-read limit, plus one additional
byte to detect oversized content. The aggregate cap is 1 MiB including these
additional bytes. Capped files remain
`not_inspected` with `note_limit` or `byte_budget` reasons. YAML metadata with
aliases, more than 4096 parsing events, or collection nesting deeper than 32
remains `not_inspected` with reason `metadata_complexity`, before metadata is
constructed. This limit does not classify such content as malformed. The check
uses only YAML frontmatter, leaving body text and other supported formats alone.
Numeric overflow or recursion limits during parsing also retain this outcome.
Other unexpected parser failures produce `state="unavailable"` with reason
`unexpected_parser_error`; sibling observations remain available. Exception
details are omitted.

`inspect_bundle` is a live observation with no stable truncated subset guarantee.
It does not descend into child directories, detect global
OKF mode, serve navigation bodies or create navigation files. Unsupported
no-follow filesystem primitives produce `unavailable`.

When the index is queryable, safely observed regular Markdown entries report
`searchable`,
`non_admitted` (missing a locally required field), `unparseable`,
`index_excluded`, or `not_indexed_yet` from current exclusion policy and the
last index observations, even when a read limit prevented parsing. A live row
or recorded skip takes precedence over the current exclusion label; exclusion
is used when no index row exists. Each state comes from one index read. Unknown
index error categories remain `not_evaluated`. Any SQLite error from the index read, including after the vault closes,
leaves all entries `not_evaluated`. Each entry is read separately, so one page
is not a snapshot. Entries also remain unevaluated when they
cannot be safely inspected, when the backend lacks row-state support, and before
the index is ready. Search state reflects the last index observations and can
lag disk changes until indexing runs. Format conformance is a separate
assessment. Inspection does not refresh or write the index or tracker. Direct `BundleInspector` use has no
index knowledge and reports `not_evaluated`.

For a subtree, page each selection and queue any depth-limit directories for a
fresh selection. Keep unavailable outcomes from every page, even if a later
page is complete:

```python { .fragment }
from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

pending = [""]
unavailable = False
while pending:
    directory = pending.pop()
    cursor = None
    while True:
        page = vault.reader.inspect_bundle_tree(
            BundleTreeRequest(root="knowledge", directory=directory, entry_limit=50, cursor=cursor)
        )
        unavailable |= page.state != "inspected" or page.enumeration == "unavailable"
        for entry in page.entries:
            # Consume each entry here. The selected directory itself is not returned again.
            unavailable |= entry.state == "unavailable"
            if entry.reason == "depth_limit":
                pending.append(entry.path.removeprefix("knowledge/"))
        cursor = page.next_cursor
        if cursor is None:
            break
# Investigate unavailable outcomes before treating the observation as complete.
```

Tree pages are ordered by path components, with directories before their
children. The limit counts returned rows. The cursor is the last returned path,
strictly below the selected directory. A full final page requires one empty
call to establish completion. The walk is live: additions after the cursor can
appear, while additions at or before it require a new walk. Enumeration marked
`unavailable` is incomplete and must be retained across pages. Each selection
walks 32 levels below its selected directory. A directory at the limit has
`state="unavailable"` and reason `depth_limit`; its contents are not read.
Select it using its path relative to `root` and start a new walk to reach
deeper levels. Repeat for each boundary row, and do not treat unresolved rows
as complete. The page also stops before exceeding its 1 MiB read budget;
symlinks are unavailable rows; their targets are not returned. The limit counts
rows, not work: every visited directory lists and sorts all of its names on each
page, even when most names are omitted. Resume skips policy checks for names
before the cursor. A current non-directory at the cursor is treated as a leaf;
an inaccessible cursor ancestor makes enumeration incomplete.

<!-- vale off -->
::: markdown_vault_mcp.facets.reader.ReaderFacet
<!-- vale on -->

## WriterFacet

Create, edit, append, delete, rename, and attachment writes.

<!-- vale off -->
::: markdown_vault_mcp.facets.writer.WriterFacet
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
