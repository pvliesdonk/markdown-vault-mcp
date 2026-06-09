---
description: Find related notes and suggest cross-references. Read-only — does not modify any documents.
arguments:
  - name: path
    description: Path to the document to find related notes for.
    required: true
icons: search
---
Step 1: Call `read` with path='$path'. Extract the main topics and key terms.
Step 2: Call `get_context` with path='$path' — this returns a dict containing backlinks, outlinks, and similar notes in one call (index freshness is reported out-of-band in `_meta.index_stale`). Also call `search` using the main topic terms to surface additional documents not captured by direct links or overall document similarity.
Step 3: Present a list of the most relevant related documents. For each, include: the document title, its path, and one sentence explaining the connection.
Format suggested cross-references as: [title](relative/path.md)
Do not edit any documents — this prompt is read-only.
