---
description: Research a topic and consolidate findings as a new note.
arguments:
  - name: topic
    description: The topic to research.
    required: true
tags:
  - write
icons: write
---
You are building a research note about: '$topic'

1. Call `search` with that query and mode='hybrid'. If the call fails (semantic search not configured), retry with mode='keyword'. Examine the top results; call `read` on the 3-5 highest-scoring paths. If the top results seem off-topic, tell the user what was found and ask whether to proceed.
2. Write a structured markdown summary of what you found. Link each source as [document title](its/relative/path.md).
3. Choose a new path like Research/${topic_slug}.md. If that path already exists, pick an unused suffix for the new note. Call `write` with that path, your content, and frontmatter={'title': ..., 'tags': ['research']}. If another writer creates the file first, choose a new path; do not overwrite or delete the existing note.
If no results are found, tell the user and stop — do not write an empty note.
