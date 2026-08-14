---
description: Summarize a vault document with structured coverage of main topics and key points.
arguments:
  - name: path
    description: Path to the document to summarize.
    required: true
icons: read
---
Call the `read` tool with path='$path'. The result contains a `content` field (the full note including frontmatter) and a `frontmatter` field (the parsed metadata). Write a concise summary covering the document's main topics and key points. If `read` returns an error, report it and stop. This prompt covers a single document; for a folder or several notes, use the `summarize-subtree` prompt instead of repeating this per note.
