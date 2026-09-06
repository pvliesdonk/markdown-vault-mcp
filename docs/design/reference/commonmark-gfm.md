---
type: Reference
title: CommonMark and GitHub Flavored Markdown
description: CommonMark block and inline rules (paragraph boundaries, links, code) and the GFM extensions, scoped to what the scanner depends on
subject_version: "0.31.2"
valid_for: "CommonMark 0.31.x; GFM as published at accessed date"
generated:
  by: process:researching-references
  at: 2026-09-06
verified:
  - by: process:researching-references-refute
    at: 2026-09-06
stale_after: 2027-09-06
status: stable
sources:
  - id: cm
    title: CommonMark Spec, version 0.31.2 (2024-01-28), section-numbered HTML
    resource: https://spec.commonmark.org/0.31.2/
    accessed: 2026-09-06
  - id: cm-json
    title: CommonMark 0.31.2 spec.json (numbered examples with section names)
    resource: https://spec.commonmark.org/0.31.2/spec.json
    accessed: 2026-09-06
  - id: gfm
    title: GitHub Flavored Markdown Spec, version 0.29-gfm (2019-04-06)
    resource: https://github.github.com/gfm/
    accessed: 2026-09-06
  - id: gh-footnotes
    title: GitHub Docs, "Basic writing and formatting syntax", section "Footnotes"
    resource: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax
    accessed: 2026-09-06
  - id: cm-changelog
    title: commonmark-spec changelog.txt (0.30, 0.31, 0.31.1, 0.31.2 entries)
    resource: https://raw.githubusercontent.com/commonmark/commonmark-spec/master/changelog.txt
    accessed: 2026-09-06
  - id: rfc3986
    title: RFC 3986, Uniform Resource Identifier (URI), section 3.1 Scheme
    resource: https://www.rfc-editor.org/rfc/rfc3986#section-3.1
    accessed: 2026-09-06
---

# CommonMark and GitHub Flavored Markdown

What CommonMark 0.31.2 (current at `spec.commonmark.org` on the accessed
date) and the GFM extensions say about the constructs `scanner.py` and
`utils/links.py` recognise with regexes: how a line and a blank line are
spelled, every way a paragraph ends, and the grammar of links, reference
definitions, code spans and fences. Written for issue #1334, whose two closed
attempts each found one more spelling of "where does a paragraph end" and
then dropped links written on a heading line. Obsidian's syntax is in
`obsidian-markdown.md`. "Ex. N" is the numbered example in `spec.json`
[source: cm-json]; observations ran markdown-it-py 3.0.0
(`MarkdownIt("commonmark")`, tables/strikethrough enabled where said) on
Python 3.13.

## Scope

- Covers: §2.1 lines and line endings; §4.1–4.9 leaf blocks and what
  interrupts a paragraph; §5.1–5.3 containers and laziness; §6.1 code spans;
  §6.3–6.5 links, images, autolinks; §2.4–2.5 escapes and entities;
  §6.6–6.8 raw HTML and line breaks; GFM tables, task lists, strikethrough,
  extended autolinks; GitHub footnotes.
- Does not cover: emphasis, rendering, Obsidian wikilinks/embeds/callouts.
- Depended on by: `src/markdown_vault_mcp/scanner.py` (`extract_links`,
  `_extract_inline_links`, `_extract_reference_links`, `_strip_code_spans`,
  `_scan_headings`, `extract_section`, `HeadingChunker._budget_split`),
  `src/markdown_vault_mcp/utils/links.py` (`apply_link_replacement`);
  `docs/design/design.md` § Link Extraction, § Chunking Strategy.

## Claims

### Lines, line endings, blank lines (§2.1–2.3, §3.1, §4.9)

- A line ending is LF, a CR not followed by LF, or CR LF; a line is the
  characters other than LF/CR up to a line ending or end of file. [source: cm]
- A blank line contains nothing, or only spaces (U+0020) and tabs (U+0009);
  form feed, NBSP and U+2028 do not make a line blank. [source: cm]
  [observed: markdown-it-py 3.0.0, `[a\n\x0c\nb](x.md)`, `[a\n\xa0\nb](x.md)`,
  `[a  b](x.md)` each render one link]
- The three spellings behave identically: `[a\r\n\r\nb](x.md)`,
  `[a\r\rb](x.md)`, `[a\n\nb](x.md)` are two paragraphs and no link;
  `[a\rb](x.md)`, `[a\r\nb](x.md)` are one link over a soft break.
  [observed: markdown-it-py 3.0.0, those inputs]
- Multiple blank lines have no extra effect; blank lines at document start
  and end are ignored (Ex. 221, 227). [source: cm]
- Tabs count as spaces to a 4-column stop where indentation defines blocks
  (`#\tFoo` is a heading, §2.2); U+0000 becomes U+FFFD (§2.3). [source: cm]
- Between 0.29 (GFM's base) and 0.31.2 the changelog records four changes
  that touch these claims: 0.30 removed line tabulation and form feed from
  "whitespace" (so under 0.29/GFM a form-feed-only line counts as blank and
  under 0.31.2 it does not), renamed "newline" to "line ending", added
  `textarea` to the type-1 HTML block tags, and relaxed declarations
  (`<!X` need not be all capitals); 0.31 removed `source` from the type-6
  tag list and renamed "compact" reference links to "collapsed". 0.31.1 and
  0.31.2 are packaging fixes. Nothing else in 0.30–0.31.2 changes paragraphs,
  line endings, links, labels, definitions or fences. [source: cm-changelog]
- Block structure beats inline structure (`- \`one` / `- two\`` is two
  items, Ex. 42); inline parsing needs the reference definitions collected
  by the block pass (§3.1). [source: cm]

### What a paragraph is and what ends it (§4.8)

- A paragraph is a run of non-blank lines that cannot be interpreted as
  other blocks; raw content is the lines joined, trimmed of leading and
  trailing spaces/tabs (Ex. 219, 220). Lines after the first may be indented
  any amount (Ex. 223); the first line at most 3 spaces (Ex. 225). [source: cm]
- A paragraph ends at a blank line, at the end of the document or of its
  container, or at a line that starts a block which "can interrupt a
  paragraph" — the list below. [source: cm]
- ATX heading interrupts (§4.2; Ex. 70 shows a 4-space-indented `#` does
  not). [source: cm] [observed: markdown-it-py 3.0.0: `[a\n# h\nb](x.md)` and
  `[a\n   # h\nb](x.md)` no link; `[a\n    # h\nb](x.md)`, `[a\n#h\nb](x.md)`,
  `[a\n####### h\nb](x.md)` one link]
- Thematic break interrupts (§4.1, Ex. 58 `Foo`/`***`/`bar`); a `---` line
  after text is a setext underline instead (Ex. 59), which also ends the
  paragraph by making it an H2. [source: cm] [observed: markdown-it-py 3.0.0,
  `[a\n---\nb](x.md)` renders `<h2>[a</h2>`; `[a\n* * *\nb](x.md)` an `<hr />`]
- Fenced code (backtick or tilde, 3+) interrupts, no blank line needed
  (§4.5, Ex. 140). [source: cm] [observed: markdown-it-py 3.0.0,
  `[a\n```\nb](x.md)` and `[a\n~~~\nb](x.md)` no link; two backticks one link]
- Block quote interrupts (§5.1, Ex. 245 `foo`/`> bar`). [source: cm]
- A bullet item (`-`, `+`, `*` plus a space or tab; Ex. 261 `-one` is not
  one) interrupts (§5.3, Ex. 303); an ordered item interrupts only when its
  number is 1 with `.` or `)` (§5.2 rule 1 exception 1; Ex. 304 `14.` no,
  Ex. 305 `1.` yes). [source: cm] [observed: markdown-it-py 3.0.0,
  `[a\n2. b](x.md)` one link; `[a\n1) b](x.md)` no link; `[a\n-b](x.md)` one]
- HTML blocks of types 1–6 interrupt (§4.6: `<pre|script|style|textarea`,
  `<!--`, `<?`, `<!X`, `<![CDATA[`, and `<`/`</` plus a name from the fixed
  block-tag list — `div`, `p`, `table`, `h1`…); type 7, any other complete
  tag alone on a line, cannot (Ex. 185 vs 187). [source: cm]
  [observed: markdown-it-py 3.0.0, `[a\n<div>\nb](x.md)` and
  `[a\n<!-- c -->\nb](x.md)` no link; `[a\n<span>\nb](x.md)` one link]
- A GFM table (header row then delimiter row) interrupts: a table is a leaf
  block "broken at the first empty line, or beginning of another block-level
  structure". [source: gfm] [observed: markdown-it-py 3.0.0 with `table`,
  `para\n| a |\n|---|\n| b |` renders `<p>para</p><table>`]

### What cannot interrupt a paragraph

- Indented code: `aaa`/`    bbb` is one paragraph (§4.4, Ex. 223).
  [source: cm] [observed: markdown-it-py 3.0.0, `[a\n    b](x.md)` one link]
- A setext underline joins the preceding lines into a heading rather than
  interrupting (`Foo`/`Bar`/`---` is one H2, Ex. 95); as a lazy line it is
  text (Ex. 93). [source: cm]
- An ordered item not starting at 1, or an item whose first line is blank
  (`foo`/`*`, Ex. 285). [source: cm] [observed: markdown-it-py 3.0.0,
  `[a\n*\nb](x.md)`, `[a\n+\nb](x.md)`, `[a\n1.\nb](x.md)` one link each;
  `[a\n-\nb](x.md)` is `<h2>[a</h2>` — a lone `-` is a setext underline]
- A link reference definition (`Foo`/`[bar]: /baz`, Ex. 213); it may follow
  a heading or thematic break directly (Ex. 214). [source: cm]
  [observed: markdown-it-py 3.0.0, `[a\n[r]: x.md\nb](x.md)` one link]
- HTML block type 7; a fence shorter than 3 (Ex. 121). [source: cm]

### Containers: block quotes and list items (§5.1, §5.2)

- Marker: up to 3 spaces, `>`, optional space. A paragraph inside a quote
  ends at a marker-only line (`> foo`/`>`/`> bar`, Ex. 244), a blank line,
  or any interrupt above after the marker. [source: cm] [observed:
  markdown-it-py 3.0.0, `> [a\n>\n> b](x.md)`, `> [a\n> \n> b](x.md)` no link]
- Laziness: a marker-less line that would be paragraph continuation stays
  in the quote (Ex. 232, 233), so a blank line is needed after a quote
  (Ex. 247); a lazy line cannot start a list, code, fence or setext
  underline (Ex. 234–237, 92). [source: cm] [observed: markdown-it-py 3.0.0,
  `> [a\nb](x.md)` one link; `> a\n>\nb` ends the quote at `>`]
- A blank line always separates two quotes (Ex. 242); an unclosed fence
  inside a quote ends with the quote (Ex. 237). [source: cm]
- Item content is indented to the column after the marker's 1–4 spaces;
  ordered markers are 1–9 digits then `.`/`)` (Ex. 266); items may be
  preceded by ≤ 3 spaces (rule 4) and have lazy continuation lines (rule 5).
  A fence inside an item closes at the item's indentation. [source: cm]
  [observed: markdown-it-py 3.0.0, `- item\n  ```\n  [a](x.md)\n  ```\n- [b](y.md)`
  links only `b`]

### ATX headings, setext headings, thematic breaks (§4.1–4.3)

- ATX: 1–6 unescaped `#`, then space, tab or end of line; ≤ 3 spaces
  before; optional closing `#` run preceded by a space; content trimmed.
  `####### foo` (Ex. 63), `#5 bolt` (Ex. 64), 4-space-indented `#` (Ex. 69)
  are not headings; `#` alone is an empty heading (Ex. 79); `# foo ##` has
  content `foo` (Ex. 71). [source: cm]
  [pins: tests/test_scanner.py::TestExtractSection::test_fenced_hash_line_acts_as_boundary]
- Heading content is inline-parsed: `intro`/`# See [t](n.md)`/`body` links
  `t`; so does setext text `See [t](n.md)`/`===`. [observed: markdown-it-py
  3.0.0, those inputs]
- Setext: one or more paragraph-like lines (first ≤ 3 spaces) then a line
  of only `=` or only `-` (≤ 3 spaces, trailing whitespace ok, no internal
  spaces); never empty; H1 for `=`, H2 for `-`. [source: cm]
- Thematic break: ≤ 3 spaces, then 3+ of one of `-` `_` `*` with optional
  spaces/tabs between and after, nothing else; beats a list item (Ex. 60),
  loses to a setext underline (Ex. 59). [source: cm]

### Code spans and fenced code (§6.1, §4.5)

- A code span opens with a backtick string of length n and closes at the
  next backtick string of exactly n; line endings inside become spaces;
  backslashes are literal; an unmatched string is literal (Ex. 328, 329,
  347). [source: cm] [observed: markdown-it-py 3.0.0,
  `` `` a](x.md) `` and [b](y.md) `` links only `b`; `` ``` [a](x.md) ``` ``
  is a code span, not a fence]
- Code spans, HTML tags and autolinks bind tighter than link brackets
  (`` [not a `link](/foo`) ``, Ex. 342; 524–526). [source: cm]
  [pins: tests/test_links.py::TestCodeBlockExclusion::test_link_in_inline_code_excluded]
- A fence is 3+ backticks or 3+ tildes, not mixed, ≤ 3 spaces; the closer
  uses the same character, is at least as long (Ex. 122, 124), carries no
  info string (Ex. 147); unclosed, the block runs to the end of document or
  container (Ex. 127, 129). [source: cm] [observed: markdown-it-py 3.0.0,
  `` ```\n[a](x.md)\n `` no link; 4 backticks not closed by 3; backticks not
  closed by `~~~`]
  [pins: tests/test_links.py::TestCodeBlockExclusion::test_link_in_fenced_code_excluded, tests/test_links.py::TestCodeBlockExclusion::test_link_after_fenced_code_extracted]
- The info string (trimmed text after the opener; no backticks after a
  backtick fence, Ex. 145) is not inline content: `` ``` [a](x.md) `` on a
  fence line is not a link. [source: cm] [observed: markdown-it-py 3.0.0,
  renders `class="language-[a](x.md)"`]
- Indented code is 4+ spaces per line; it cannot interrupt a paragraph but a
  paragraph may follow it directly (§4.4). [source: cm]

### Inline links (§6.3, §6.4)

- Link text is zero or more inlines in `[`…`]`; inner brackets must be
  escaped or balanced (`[link [foo [bar]]](/uri)` links, Ex. 512;
  `[link] bar](/uri)` does not, Ex. 513; `[link \[bar](/uri)` does, Ex. 515).
  [source: cm] [observed: markdown-it-py 3.0.0, `[a [b] c](x.md)` one link
  with text `a [b] c`; `[a [b c](x.md)` links `b c`]
- Links may not contain links, innermost wins (Ex. 518); an image
  description may (Ex. 575). [source: cm] [observed: markdown-it-py 3.0.0,
  `[a [b](y.md) c](x.md)` links `b`; `![a [b](y.md)](i.png)` is one image]
- No whitespace between `]` and `(` (Ex. 511); inside the parentheses,
  spaces, tabs and up to one line ending may surround destination and title
  (Ex. 510). [source: cm]
- Destination, form 1: `<`…`>` with no line endings and no unescaped `<`/`>`;
  may hold spaces and `)` (Ex. 489, 492); `<foo\nbar>` fails (Ex. 491).
  Form 2: non-empty, not starting with `<`, no ASCII control characters and
  no spaces — hence no line endings (Ex. 488, 490); parentheses only escaped
  or balanced, ≥ 3 levels (Ex. 496–498). [source: cm] [observed: markdown-it-py
  3.0.0, `[a](x(1).md)` links `x(1).md`; `[a](x(1.md)` and `[a](x\n.md)` no link]
- Destination may be empty (Ex. 485); title is `"…"`, `'…'` or `(…)`,
  whitespace-separated, may span lines but not a blank line (Ex. 505, 510).
  [source: cm] [pins: tests/test_utils_links.py::TestApplyLinkReplacement::test_markdown_link_with_title]
- Backslash escapes and entities are decoded in destinations and titles
  (Ex. 22, 32); a backslash before non-punctuation is literal (Ex. 13);
  `\[` makes `\[not a link](/foo)` text (Ex. 14, 563). [source: cm]
  [observed: markdown-it-py 3.0.0, `[a](x\*.md)` → `x*.md`;
  `[a](x&#46;md)` → `x.md`]
- Images are `![`…`](`…`)` with the same grammar (§6.4). [source: cm]
  [pins: tests/test_links.py::TestExtractInlineLinks::test_image_link_excluded]
- A soft break (§6.8) or hard break (§6.7) inside link text keeps the link.
  [observed: markdown-it-py 3.0.0, `[a\nb](x.md)` and `[a\\\nb](x.md)`]

### Reference links and definitions (§4.7, §6.3)

- Definition: ≤ 3 spaces, link label, `:`, optional whitespace with up to
  one line ending, destination (same forms; empty only as `<>`, Ex. 199,
  200), optional whitespace with up to one line ending, optional title,
  nothing else on the line (Ex. 209 `[foo]: /url "title" ok` is a
  paragraph; Ex. 198 destination on the next line). Title may span lines
  but not a blank line (Ex. 196, 197). [source: cm]
- Not a definition when indented 4 spaces (Ex. 211) or inside code
  (Ex. 212); definitions may sit anywhere, before or after use, inside
  quotes or lists, and apply document-wide (Ex. 218). [source: cm]
  [observed: markdown-it-py 3.0.0, `> [r]: x.md\n\n[r]` links]
- A link label is `[`, ≥ 1 non-whitespace character, ≤ 999 characters, no
  unescaped brackets, `]` (Ex. 551, 552, 545). [source: cm]
  [observed: markdown-it-py 3.0.0, `[r[s]]: x.md` is a paragraph]
- Labels match after Unicode case fold, trim, and collapsing internal
  whitespace runs (spaces, tabs, line endings) to one space (`[ẞ]` matches
  `[SS]`, Ex. 540; `[Foo\n  bar]` matches `[Foo bar]`, Ex. 541); the first
  matching definition wins (Ex. 204). [source: cm]
  [pins: tests/test_links.py::TestExtractReferenceLinks::test_reference_link_case_insensitive_key]
- Full `[text][label]`, collapsed `[label][]`, shortcut `[label]`; no
  whitespace between the pairs (Ex. 542); full/collapsed beat shortcut
  (Ex. 569–571); inline beats reference (Ex. 567); `[foo][bar]` with `bar`
  undefined is not a shortcut to `foo` (Ex. 565). [source: cm]
  [pins: tests/test_links.py::TestExtractReferenceLinks::test_reference_link_basic, tests/test_links.py::TestExtractReferenceLinks::test_undefined_reference_skipped]
- Reference link text follows inline-link rules, so a blank line inside
  `[t\n\nu][r]` breaks it. [observed: markdown-it-py 3.0.0, that input]

### Autolinks and the URI scheme (§6.5; GFM §6.9)

- URI autolink: `<`, scheme, `:`, zero or more characters other than ASCII
  control, space, `<`, `>`, then `>`; scheme is 2–32 characters, an ASCII
  letter then letters, digits, `+`, `.`, `-`; registration irrelevant
  (Ex. 599). [source: cm] [observed: markdown-it-py 3.0.0, `<a:b>` is text,
  `<ab:c>` links, a 33-letter scheme is text]
- RFC 3986 §3.1: `scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )`, no
  length bounds; the 2-character floor is CommonMark's. [source: rfc3986]
  [pins: tests/test_links.py::TestExternalUriSchemes::test_windows_drive_letter_is_not_a_scheme, tests/test_links.py::TestExternalUriSchemes::test_schemed_inline_target_is_external]
- `<a@b.co>` becomes `mailto:`; bare `https://example.com` is not a link in
  CommonMark (Ex. 611); escapes do not apply inside autolinks (Ex. 20).
  [source: cm]
- GFM extended autolinks: `www.`, `http://`, `https://`, email, `mailto:`,
  `xmpp:` without brackets, only at line start, after whitespace, or after
  `*` `_` `~` `(`, with trailing-punctuation trimming (GFM §6.9,
  GFM Ex. 622–635). [source: gfm]

### Escapes, entities, raw HTML, line breaks (§2.4, §2.5, §6.6–6.8)

- Any ASCII punctuation may be backslash-escaped except in code blocks, code
  spans, autolinks and raw HTML (Ex. 12, 17–21). [source: cm]
- Named entities need `;` (Ex. 29); numeric are `&#` + 1–7 digits or `&#x`
  + 1–6 hex digits; never structural (Ex. 37); literal in code. [source: cm]
- An HTML block's lines are raw: `<div>`/`[a](x.md)`/`</div>` has no link;
  type 6 ends at the next blank line. [source: cm] [observed: markdown-it-py
  3.0.0, that input, and `<div>\n[a](x.md)\n\n[b](y.md)` links only `b`]
- Inline raw HTML: `<` name, attributes (each may hold one line ending),
  optional `/`, `>` (§6.6); it beats link brackets (Ex. 524). [source: cm]
- Hard line break: 2+ trailing spaces or a trailing backslash before a line
  ending, not at block end (Ex. 633, 634, 644, 646); any other line ending
  in a paragraph is a soft break (Ex. 648). [source: cm]

### GFM extensions and footnotes

- GFM is a strict superset of CommonMark 0.29 with its own section numbers
  (§6.1 escapes, §6.6 links). [source: gfm]
- Table: header row, delimiter row of `-` with optional `:`, data rows;
  cells split on `|`; a literal pipe is `\|` "including inside other inline
  spans"; header and delimiter counts must match; ends at the first empty
  line or another block (GFM §4.10, GFM Ex. 198–205). [source: gfm]
  [observed: markdown-it-py 3.0.0 with `table`, `| [t](n.md) | c \| d |`
  renders the link and cell `c | d`]
  [pins: tests/test_links.py::TestExtractWikilinks::test_wikilink_escaped_pipe_alias_in_table]
- Task item: list item whose first paragraph starts with `[ ]`, `[x]`, `[X]`
  plus whitespace (GFM §5.3) — bracket syntax the link regexes see. [source: gfm]
- Strikethrough: one or two `~` each side; three do not strike (GFM §6.5,
  GFM Ex. 491–493). [source: gfm]
- Footnotes are not in the GFM spec. GitHub documents `[^1]` references,
  `[^1]: text` definitions with continuation lines, that "the position of a
  footnote in your Markdown does not influence where the footnote will be
  rendered", and that wikis lack them. [source: gh-footnotes]
  [pins: tests/test_links.py::TestExtractReferenceLinks::test_footnote_definition_not_a_link, tests/test_links.py::TestExtractReferenceLinks::test_adjacent_footnote_references_are_not_one_link]
- Obsidian divergence met here: in a table cell Obsidian writes a wikilink
  alias pipe as `\|`, which GFM defines only as the cell-pipe escape (the
  scanner strips the trailing `\`, #731). [source: gfm]

## Where this project departs from the subject

Each inventoried assumption, by function, judged right / partial / wrong.
Behaviour lines are [observed: `extract_links`, `_strip_code_spans`,
`_HEADING_RE` on the working tree, 2026-09-06].

- `_RE_FENCED_CODE` in `_strip_code_spans` — **partial.** Right: backtick
  and tilde fences. Wrong: not line-anchored (`` x ``` y … z ``` w `` in
  prose is stripped); length ignored (a 4-backtick block "closes" at 3);
  character ignored (a backtick block "closes" at `~~~`); an unclosed fence
  is not stripped, so its links are extracted; `` `` `` on a line is
  treated as a fence. Indented code, info strings, fences under list/quote
  indentation are not modelled.
- `_RE_INLINE_CODE` — **partial.** Single backtick, single line only: a span
  with a line ending inside is not stripped; a double-backtick span is
  stripped by accident when it holds no inner backtick.
- `_RE_INLINE_LINK` in `_extract_inline_links` — **wrong** on #1334: text
  and destination match across line endings, blank lines and every
  interrupting block, since `[^\]]` and `[^)]` admit `\n` and `\r`.
  **Partial** elsewhere: no `<dest>` form (stored as `<x y.md>`); no title
  (stored as `x.md "t"`); no balanced parentheses (`x(1).md` stored as
  `x(1`); spaces in destinations accepted; no bracket balancing
  (`[a [b] c](x.md)` missed, `[a [b c](x.md)` links `b c`); `\[` ignored;
  escapes and entities kept raw; links inside HTML blocks and fence info
  strings extracted. Right: images by `!` lookbehind; `[a] (x.md)` rejected.
  Decided in `docs/design/design.md` § Link Extraction, pending #1334.
- `_RE_REF_USAGE` / `_RE_REF_DEF` in `_extract_reference_links` —
  **partial.** Right: full and collapsed forms, title stripping,
  document-wide definitions, footnotes excluded. Wrong: shortcut `[label]`
  not extracted; the *last* definition wins; labels are lower-cased, not
  case-folded (`[ẞ]`/`[ss]` miss) and whitespace is not collapsed; a
  4-space-indented definition accepted, one inside `> ` rejected; trailing
  garbage kept in the target; `<x y.md>` kept with brackets; label text may
  span a blank line; on a CR-only file `$` never matches, so no definition
  is found (`[t][r]\r[r]: x.md\r` → no links).
- `_RE_URI_SCHEME` in `_is_external_target` — **right** against
  CommonMark's scheme (2+ characters, letter then `[A-Za-z0-9+.-]`), minus
  the 32 cap, plus `//host`; narrower than RFC 3986 by design (#1335).
  Decided in `docs/design/design.md` § Link Extraction.
- `_HEADING_RE` in `_scan_headings` / `extract_section` — **partial.**
  Right: 1–6 `#`, whitespace required, `#h` and 7 `#` rejected, CR/CRLF
  fine via `splitlines`. Narrower: a heading indented 1–3 spaces missed; an
  empty `#` missed; a closing `##` run kept in the text; setext headings
  invisible; `# h` inside a fence counts (deliberate chunker parity, see the
  module comment). Decided in `docs/design/design.md` § Chunking Strategy.
- `HeadingChunker._budget_split` — **partial.** A paragraph is a run of
  lines with non-empty `strip()` over `splitlines()`; Python splits on
  `\x0c`, `\x1c`–`\x1e`, `\x85`, U+2028/2029 too and strips NBSP, so its
  lines and blanks are a superset of CommonMark's.
  [observed: `"a\r\nb\rc\nd\x0ce\x1cf\x85g h".splitlines()` gives 8 lines]
- `apply_link_replacement` — **partial.** `[text](old title?)` and
  `[label]: old title?` only; its title capture `(?:\s[^)]*)?` admits a line
  ending; runs on raw content including code spans (documented there).
  [pins: tests/test_utils_links.py::TestApplyLinkReplacement::test_markdown_image_not_affected, tests/test_utils_links.py::TestApplyLinkReplacement::test_reference_link_with_title]
- Ingestion: `parse_note` decodes `utf-8-sig`, then `frontmatter.loads`;
  python-frontmatter 1.3.0 replaces `\r\n` with `\n` in `frontmatter.util.u`
  before parsing, and leaves a lone `\r`. So on the `parse_note` path CRLF is
  already LF when `extract_links` runs; tests calling `extract_links`
  directly see raw CRLF. [observed: `frontmatter.loads("a\r\n\r\nb").content
  == "a\n\nb"`, `frontmatter.loads("a\r\rb").content == "a\r\rb"`] This
  corrects the "nothing normalises line endings" note on #1334.

## Not covered

- No test asserts a paragraph-boundary behaviour of `extract_links` (blank
  line in any spelling, bare `>`, heading, thematic break, list marker, HTML
  block) or a link *on* a boundary line; #1334's next attempt builds them
  from the table below.
- No test covers `<dest>` destinations, inline titles, balanced parentheses,
  nested brackets, `\[`, shortcut references, first-definition precedence,
  whitespace-collapsed labels, definitions in quotes, or CR-only files; the
  departures above are observed, not pinned.
- Whether `parse_note` should normalise a lone CR as python-frontmatter
  normalises CRLF is a design question, not a spec one.

## Paragraph boundaries the scanner should honour

Decision table for #1334, one row per way a paragraph ends. "Container-free"
means detectable from the line alone, without tracking open quotes, items
or fences. "Now" is whether `extract_links` on the working tree stops a
`[`…`](…)` match there (observed 2026-09-06). "Link on the line" is what the
spec does with a link written on the boundary line itself — what a
`re.split` that discards the separator loses (the #1348 regression).

| Boundary | Spec | Container-free | Now | Example line | Link on the line |
| --- | --- | --- | --- | --- | --- |
| End of document / container | §4.8 | yes | yes | — | — |
| Blank line, LF | §2.1, §4.8 | yes | no | empty line | none possible |
| Blank line, CRLF (`\r\n\r\n`) | §2.1 | yes | no | `\r\n` | none; already LF on the `parse_note` path |
| Blank line, lone CR (`\r\r`) | §2.1 | yes | no | `\r` | none |
| Whitespace-only line (spaces/tabs) | §2.1 | yes | no | `   ` | none |
| Bare quote marker line | §5.1 Ex. 244 | yes (`^ {0,3}>[ \t]*$`) | no | `>` | none |
| ATX heading | §4.2 | yes (`^ {0,3}#{1,6}([ \t]\|$)`) | no | `## See [t](n.md)` | **is a link** (inline content) |
| Thematic break | §4.1 | yes | no | `***` | none possible |
| Setext underline (`===`/`---`) | §4.3 | yes, but it turns the *preceding* lines into a heading | no | `---` after text | preceding lines' links stay links |
| Fence opener, 3+ backticks or tildes | §4.5 | opener yes; the closer needs length and character | only if closed | ```` ```py ```` | info string: **not a link** |
| Block quote line with text | §5.1 Ex. 245 | yes | no | `> see [t](n.md)` | **is a link** (paragraph in the quote) |
| Bullet item `-`/`+`/`*` + space | §5.3 Ex. 303 | yes | no | `- see [t](n.md)` | **is a link** |
| Ordered item `1.`/`1)` + space | §5.2 rule 1 | yes | no | `1. see [t](n.md)` | **is a link** |
| Ordered item not starting at 1 | §5.2 Ex. 304 | not a boundary | — | `2. text` | continuation text |
| Empty list item | §5.2 Ex. 285 | not a boundary (a lone `-` is a setext underline) | — | `*` | — |
| HTML block type 1–5 opener | §4.6 | yes (fixed prefixes) | no | `<!-- note -->` | raw: **not a link** |
| HTML block type 6 opener | §4.6 | yes (tag list) | no | `<div>` | raw until a blank line: **not a link** |
| HTML block type 7 | §4.6 Ex. 187 | not a boundary | — | `<span>` | continuation text |
| Indented code (4+ spaces) | §4.4 Ex. 223 | not a boundary | — | `    text` | continuation text |
| Link reference definition | §4.7 Ex. 213 | not a boundary | — | `[r]: x.md` | continuation text (a definition only after a blank line or block) |
| GFM table header + delimiter | GFM §4.10 | needs two lines | no | `\| a \|` / `\|---\|` | cells: **are links** |
| Soft break (single line ending) | §6.8 | — | not a boundary | `[a` / `b](x.md)` | one link across it |

Two consequences. Four boundary kinds carry inline content on the boundary
line (ATX heading, quote line, list opener, table row): a splitter must hand
that line to the extractors as its own region or it repeats #1348. The rows
marked "not a boundary" are the ones a line-shape regex is tempted to treat
as separators; each is spec-backed continuation text and deserves a test
that the link survives.
