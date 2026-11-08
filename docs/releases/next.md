# Next release

<!-- notes-range-end: 25db173cd9085b7241bd9d59bebadc77369671fe -->

<!-- RELEASE-SUMMARY NEXT START -->
This release fixes how the server finds the headings in a note. The outline
that `get_toc` returns now lists every heading the note has, a `#` line inside
fenced code is no longer read as a heading, and a note that opens with
indented code keeps it as code instead of taking its first line as the title.
Nothing needs to change on upgrade. The first start rebuilds the text index
once; the release's Upgrading notes say what that keeps.
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

No step is required.

**Clients:** nothing beyond reconnecting. No tool, parameter or payload shape
changes. An outline can list more headings than before, a heading that occurs
twice in a note is listed twice, and a note whose title came from a `#` line
in code gets its real title.

**State:** kept. The first start after upgrading rebuilds the text index once,
automatically, because this release changes how headings are derived from the
notes. The embeddings are kept: only a note whose sections came out different
is embedded again, and only its changed sections. Those are notes with a `#`
line inside fenced code and notes whose first line is indented. A reader that
opens an index read-only, such as the terminal client on a local index, keeps
the old outline for an index an older version built until the server has
rebuilt it, and logs `toc_heading_table_missing` once.

**Security posture:** none.

## The outline lists every heading

[`get_toc`](../reference/tools/reader.md#get_toc) and the
`toc://vault/{path}` resource return the heading outline of a note, or of
every note under a folder. Until this release the outline was read from the
index's search chunks, and a chunk carries at most one heading. The report in
[#2033](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2033) was that
it "omits headings that `read` returns and that `read(path, section=...)`
finds". The outline missed:

- every heading in a note of 30 lines or fewer;
- headings below H2 inside a section short enough to stay one chunk;
- a heading with no text under it;
- in a long section, a heading followed directly by a deeper one.

The index now stores every heading of a note in document order, found by the
same scan that `read(path, section=...)` uses, so each heading the outline
lists is one a section read accepts, and the other way round
([#2035](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2035)). Search
chunks are unchanged.

## Code is not a heading

A `#` line inside fenced code is a comment or a prompt, not a heading. As
[#2034](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2034) put it,
`read(path, section="Setup")` "stops at a `# comment` line inside a fenced
code block". The same line showed in the outline, split a search chunk inside
the code, and could become the note's title. Lines between an opening and a
closing fence of three or more backticks or tildes are now never headings,
for section reads, the outline, chunking and the title alike
([#2037](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2037)).

A note that opens with indented code had the same problem one step earlier:
the body was read with its first line's indentation stripped, so, in the
words of [#2036](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2036),
"the code line loses its indentation, and a `#` line in it becomes the note's
title and an H1 in the outline". The first line now keeps its indentation;
every other note reads exactly as before
([#2038](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2038)).
