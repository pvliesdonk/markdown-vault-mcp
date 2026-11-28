---
type: Reference
title: UTF-8 byte order mark before a frontmatter block
description: Whether a leading UTF-8 BOM (EF BB BF) is content or an encoding signature when a reader looks for the opening `---` of YAML frontmatter, per OKF 0.3, its own tooling, Unicode, RFC 3629, YAML 1.2.2, CommonMark and the common frontmatter consumers
subject_version: "OKF 0.3 (SPEC.md and tooling at open-knowledge-format/open-knowledge-format 527e81aff5); Unicode 18.0.0; RFC 3629; YAML 1.2.2; CommonMark 0.31.2 and spec master 8a5f7646e9; python-frontmatter 1.3.0; Jekyll 4.4.1; Hugo 0.170.1; gray-matter 4.0.3"
valid_for: "OKF 0.3; re-research on any later commit to SPEC.md, validate.py, scan.py or document.py, on a 0.4 draft, or on a CommonMark release after 0.31.2"
generated:
  by: process:researching-references
  at: 2026-11-28T08:13:20+01:00
stale_after: 2027-05-28T00:00:00Z
status: draft
sources:
  - id: okf-spec
    title: Open Knowledge Format (OKF), Version 0.3, SPEC.md at commit 527e81aff5 (2026-11-18)
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/SPEC.md
    accessed: 2026-11-28
  - id: okf-spec-v02
    title: OKF Version 0.2 as amended, SPEC.md at commit 0b87c52c6e (2026-08-21)
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/0b87c52c6e/SPEC.md
    accessed: 2026-11-28
  - id: okf-document
    title: OKF reference agent, src/reference_agent/bundle/document.py (OKFDocument.parse) at 527e81aff5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/src/reference_agent/bundle/document.py
    accessed: 2026-11-28
  - id: okf-document-test
    title: OKF reference agent, tests/test_document.py (test_parse_ignores_utf8_bom) at 527e81aff5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/tests/test_document.py
    accessed: 2026-11-28
  - id: okf-bom-commit
    title: OKF commit 56e30af256 "Parse timestamps per OKF v0.3 and keep their quoting on rewrite" (2026-10-23T22:46:24Z)
    resource: https://github.com/open-knowledge-format/open-knowledge-format/commit/56e30af256
    accessed: 2026-11-28
  - id: okf-v03-commit
    title: 'OKF commit 9506f310ed "Update to OKF v0.3: spec, example bundles, and tooling" (2026-10-23T22:46:31Z)'
    resource: https://github.com/open-knowledge-format/open-knowledge-format/commit/9506f310ed
    accessed: 2026-11-28
  - id: okf-validate
    title: OKF okf-generate skill, skills/okf-generate/scripts/validate.py at 527e81aff5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/skills/okf-generate/scripts/validate.py
    accessed: 2026-11-28
  - id: okf-scan
    title: OKF okf-consume skill, skills/okf-consume/scripts/scan.py at 527e81aff5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/skills/okf-consume/scripts/scan.py
    accessed: 2026-11-28
  - id: okf-index
    title: OKF reference agent, src/reference_agent/bundle/index.py (_root_frontmatter) at 527e81aff5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/src/reference_agent/bundle/index.py
    accessed: 2026-11-28
  - id: unicode-ch2
    title: The Unicode Standard, Version 18.0.0, Chapter 2, §2.6 Encoding Schemes
    resource: https://www.unicode.org/versions/Unicode18.0.0/core-spec/chapter-2/
    accessed: 2026-11-28
  - id: unicode-ch23
    title: The Unicode Standard, Version 18.0.0, Chapter 23, §23.8 Specials, "Byte Order Mark (BOM) - U+FEFF"
    resource: https://www.unicode.org/versions/Unicode18.0.0/core-spec/chapter-23/
    accessed: 2026-11-28
  - id: rfc3629
    title: RFC 3629, UTF-8, a transformation format of ISO 10646, §6 Byte order mark (BOM)
    resource: https://www.rfc-editor.org/rfc/rfc3629.txt
    accessed: 2026-11-28
  - id: yaml
    title: YAML Ain't Markup Language Version 1.2.2, §5.2 Character Encodings
    resource: https://yaml.org/spec/1.2.2/#52-character-encodings
    accessed: 2026-11-28
  - id: cm-spec
    title: CommonMark Spec 0.31.2, §2.1 Characters and lines
    resource: https://spec.commonmark.org/0.31.2/
    accessed: 2026-11-28
  - id: cm-spec-master
    title: commonmark-spec commit 8a5f7646e9 "Add note that a BOM at the beginning is skipped" (2026-10-10, unreleased after 0.31.2)
    resource: https://github.com/commonmark/commonmark-spec/commit/8a5f7646e9f67c3004f023d262b3e6b596afc2ed
    accessed: 2026-11-28
  - id: cm-issue-845
    title: commonmark-spec issue 845, "The spec doesn't say what to do with a leading byte order mark", with the maintainer's reply
    resource: https://github.com/commonmark/commonmark-spec/issues/845
    accessed: 2026-11-28
  - id: cmark
    title: cmark 0.31.2 (eec0eeba6d), src/blocks.c S_parser_feed and test/regression.txt "Issue #334 - UTF-8 BOM"
    resource: https://github.com/commonmark/cmark/blob/0.31.2/src/blocks.c
    accessed: 2026-11-28
  - id: cmjs
    title: commonmark.js 0.31.2 (cb2c2303d3), lib/*.js and bin/commonmark
    resource: https://github.com/commonmark/commonmark.js/tree/0.31.2/lib
    accessed: 2026-11-28
  - id: pyfm
    title: python-frontmatter 1.3.0, frontmatter/__init__.py and default_handlers.py (installed wheel)
    resource: https://github.com/eyeseast/python-frontmatter/tree/v1.3.0
    accessed: 2026-11-28
  - id: jekyll
    title: Jekyll v4.4.1 (commit 1f319fb273), lib/jekyll/utils.rb merged_file_read_opts and lib/jekyll/document.rb YAML_FRONT_MATTER_REGEXP
    resource: https://github.com/jekyll/jekyll/blob/v4.4.1/lib/jekyll/utils.rb
    accessed: 2026-11-28
  - id: hugo
    title: Hugo v0.170.1 (c8b03de956), common/hugio/readers.go trimBOM and hugolib/page_test.go TestChompBOM
    resource: https://github.com/gohugoio/hugo/blob/v0.170.1/common/hugio/readers.go
    accessed: 2026-11-28
  - id: gray-matter
    title: gray-matter 4.0.3 (master 310f934938), lib/utils.js toString and lib/to-file.js; strip-bom-string at c67b05e878
    resource: https://github.com/jonschlinkert/gray-matter/blob/310f9349381775d10a221cef903989eb5acc8843/lib/utils.js
    accessed: 2026-11-28
  - id: obsidian
    title: Obsidian help, Release notes/v1.1.13.md (released 2023-02-13), obsidian-help at 1a4e17ca01
    resource: https://github.com/obsidianmd/obsidian-help/blob/1a4e17ca01/Release%20notes/v1.1.13.md
    accessed: 2026-11-28
---

# UTF-8 byte order mark before a frontmatter block

Whether the three bytes `EF BB BF` (U+FEFF) at the start of a Markdown file
are content, which would stop the file from "starting with a line that is
exactly `---`" under OKF 0.3 §4, or an encoding signature that a reader
removes before that rule applies. Covers the OKF text, the OKF repository's
own parsers and validator, the encoding standards, and the frontmatter
consumers this server sits beside. The server's own reading contract
(`utf-8-sig` on every vault-markdown read, plain `utf-8` writes) is decided
in `docs/design/design.md` § "UTF-8 BOM normalization (#673)"; line-ending
handling on the same path is in [commonmark-gfm.md](commonmark-gfm.md)
("Ingestion"); this page researches the open "Not covered" entry on BOMs in
[okf-v0.3.md](okf-v0.3.md).

## Scope

- Covers: a single UTF-8 BOM at byte 0 of a `.md` file, before the opening
  `---`; how each source treats it.
- Does not cover: UTF-16/UTF-32 files (OKF requires UTF-8); U+FEFF anywhere
  other than the start; non-UTF-8 files (open OKF issue 21, a third-party
  report about `validate.py` crashing on them); the whitespace tolerance of
  the `---` line itself, which differs between OKF's tools (noted once below).
- Depended on by: `src/markdown_vault_mcp/utils/text.py`
  (`read_text_utf8`, `decode_utf8`), `scanner.py` (`parse_note`), the OKF
  detector and audit in `okf.py`, `_okf_log_repair.py`, `_transfer_sink.py`.

## Claims

### OKF 0.3 specification text

- §4 opens "Each concept is a UTF-8 Markdown file with two parts" and lists,
  under "Frontmatter syntax", "The file MUST start with a line that is
  exactly `---`." and "The block ends at the next line that is exactly
  `---`." [source: okf-spec] (§4)
- The spec contains no mention of a byte order mark, U+FEFF, or how the
  UTF-8 bytes are decoded; "UTF-8" appears once, in that §4 sentence. The
  same held at every earlier SPEC.md commit (25461dbcdd through f097ae8ef3).
  [observed: `grep -c -i "bom\|byte order\|feff\|utf-8"` over SPEC.md at
  each of the seven commits that touched it returned 1, the §4 sentence]
- The "exactly `---`" wording is new in 0.3. 0.2 said only "delimited by
  `---` on its own line at the start of the file". [source: okf-spec-v02]
  (§4) [source: okf-v03-commit]
- §3.2 rule 2: "A `.md` file that has no frontmatter, or no `type` in its
  frontmatter, is not a concept. Consumers skip it. It does not affect
  conformance. Validators SHOULD warn about it." So whichever reading a
  consumer takes, a BOM file is never non-conformant; the readings differ
  only on whether it is a concept. [source: okf-spec] (§3.2, §11.1)
- Interpretation, not spec text: the rule is stated over a "line", and §4
  calls the file "UTF-8 Markdown", a text format; whether the line is formed
  before or after the bytes are decoded to characters, and whether that
  decoding drops a signature, the text does not say. The spec therefore does
  not settle the question on its own. [unverified] An erratum or a reply
  from the OKF authors would settle it.

### OKF repository tooling (same authors, same commit)

- The reference agent's parser strips one leading BOM before looking for the
  delimiter. Its comment, split over two `#` lines, reads "Some editors
  write a UTF-8 BOM. Drop it so that the opening `---` is found.", followed by `text = text.removeprefix("\ufeff")`.
  [source: okf-document] (`OKFDocument.parse`)
- A unit test pins that a BOM file has frontmatter:
  `test_parse_ignores_utf8_bom` parses `"\ufeff---\ntype: X\n---\n\nBody.\n"`
  and asserts `doc.frontmatter == {"type": "X"}`. [source: okf-document-test]
- The strip arrived in commit 56e30af256 (message: "Strip a leading UTF-8
  BOM before looking for the frontmatter delimiter."), authored by the same
  account as 9506f310ed, with author dates seven seconds apart; 9506f310ed replaced SPEC.md
  with the 0.3 text that introduced "exactly `---`". The rule and the BOM
  strip were written together by the spec's author. [source: okf-bom-commit]
  [source: okf-v03-commit]
- The conformance validator `validate.py` reads each `.md` with
  `path.read_text(encoding="utf-8").lstrip(BOM)` (where `BOM = "\ufeff"`)
  before `split_frontmatter`, which requires `lines[0].rstrip("\r") ==
  "---"`. The same strip is applied to the root `index.md` before reading
  `okf_version`. A BOM file is therefore a concept to OKF's validator, while
  a line such as `--- ` is not. [source: okf-validate] (`run`,
  `read_okf_version`, `split_frontmatter`; the stripped `text` reaches
  `split_frontmatter` through `check_markdown` → `frontmatter` for concepts
  and `check_index` for index files)
- The consumer scanner `scan.py` does the same:
  `path.read_text(encoding="utf-8").lstrip(BOM).splitlines()` and then
  `lines[0].rstrip("\r") != "---"` means "no frontmatter". [source: okf-scan]
  (`read_frontmatter`)
- Two maintainers wrote the strip independently: the parser commit
  (56e30af256) and the 0.3 spec are by `amirhormati`; `validate.py` and
  `scan.py` arrived in c4df7bab12 ("Add okf-generate and okf-consume agent
  skills", 2026-10-27) by `firat-elbey`, the repository's other contributor.
  [observed: `gh api repos/open-knowledge-format/open-knowledge-format/commits/<sha>`
  author logins and `/contributors`, two accounts in total]
- Index regeneration keeps a BOM-prefixed root `index.md`'s frontmatter:
  `index_path.read_text(encoding="utf-8").removeprefix("\ufeff")`.
  [source: okf-index] (`_root_frontmatter`)
- Side note: the reference agent compares `lines[0].strip()`, so it also
  accepts surrounding whitespace on the delimiter line; the validator and
  scanner do not. Only the BOM strip is common to all four readers.
  [source: okf-document] [source: okf-validate] [source: okf-scan]
- `.lstrip(BOM)` removes any number of leading U+FEFF; `removeprefix`
  removes one. [observed: Python semantics of `str.lstrip(chars)` vs
  `str.removeprefix`; not exercised against the OKF code]
- None of the OKF writers in the repository writes a BOM: every write is
  `write_text(..., encoding="utf-8")`. [observed: grep for `write_text` over
  `src/reference_agent/tools/bundle_tools.py`, `bundle/index.py` and
  `viewer/generator.py` at 527e81aff5 finds three sites, all plain `utf-8`]

### Unicode and RFC 3629

- "Use of a BOM is not required for UTF-8, but may be encountered in
  contexts where UTF-8 data is converted from other encoding forms that use
  a BOM or where the BOM is used as a UTF-8 signature. ... The Unicode
  Standard does not recommend for or against use of a BOM in UTF-8 data."
  [source: unicode-ch2] (§2.6). The phrase "neither required nor
  recommended" does not appear in the 18.0.0 (or 16.0.0) text.
- "In UTF-8, the BOM corresponds to the byte sequence <EF BB BF>. Although
  there are never any questions of byte order with UTF-8 text, this sequence
  can serve as signature for UTF-8 encoded text where the character set is
  unmarked." (The source writes each byte with a subscript 16.) [source:
  unicode-ch23] (§23.8)
- "Systems that use the byte order mark must recognize when an initial
  U+FEFF signals the byte order. In those cases, it is not part of the
  textual content and should be removed before processing". [source:
  unicode-ch23] (§23.8)
- Against stripping: "where Unicode text has known byte order, initial
  U+FEFF characters are not required, but for backward compatibility are to
  be interpreted as zero width no-break spaces." Read strictly, a protocol
  that fixes the encoding (as OKF fixes UTF-8) could treat the initial
  U+FEFF as a character. This is the strongest primary-source support for
  reading the BOM as content. [source: unicode-ch23] (§23.8)
- RFC 3629: a protocol "SHOULD forbid use of U+FEFF as a signature for
  those textual protocol elements that the protocol mandates to be always
  UTF-8", and "When a protocol forbids use of U+FEFF as a signature ... any
  initial U+FEFF ... MUST be interpreted as a 'ZERO WIDTH NO-BREAK SPACE'.
  When a protocol does NOT forbid use of U+FEFF as a signature ...
  implementations SHOULD be prepared to handle a signature in that element
  and react appropriately: using the signature to identify the character
  encoding as necessary and stripping or ignoring the signature as
  appropriate." [source: rfc3629] (§6)
- RFC 3629's two SHOULDs pull in opposite directions for OKF. The first
  quoted bullet fits a format that "mandates" UTF-8. The other, "A protocol
  SHOULD NOT forbid use of U+FEFF as a signature for those textual protocol
  elements for which the protocol does not provide character encoding
  identification mechanisms, when a ban would be unenforceable", names
  entities that implementations "obtain ... from file systems", which is how
  OKF bundles are read. [source: rfc3629] (§6)
- Either way OKF has not forbidden the signature, so the branch that applies
  to the text as written is "does NOT forbid": be prepared to strip or ignore
  it. [source: okf-spec] [source: rfc3629] (interpretation)

### YAML 1.2.2

- "If a character stream begins with a byte order mark, the character
  encoding will be taken to be as indicated by the byte order mark." and
  "Byte order marks may appear at the start of any document"; "The
  character encoding is a presentation detail and must not be used to
  convey content information." [source: yaml] (§5.2)
- This governs a YAML stream, and OKF's BOM sits before the `---` that opens
  the frontmatter, outside the YAML the spec hands to a YAML parser. It is
  an analogy for how the format OKF embeds treats the mark, not a rule that
  reaches OKF's delimiter line. [unverified] (interpretation)
- PyYAML 6.0.4 follows §5.2: `yaml.safe_load("\ufefftype: X\n")` and
  `yaml.safe_load("\ufeff---\ntype: X\n")` both return `{'type': 'X'}`.
  [observed: repo `.venv` Python 3.14.8, `python -I -c`]

### CommonMark

- 0.31.2 never mentions a BOM and says "This spec does not specify an
  encoding; it thinks of lines as composed of characters rather than bytes.
  A conforming parser may be limited to a certain encoding." [source:
  cm-spec] (§2.1) [observed: case-insensitive grep for "byte order" and
  "FEFF" over the 0.31.2 HTML and spec.txt returned 0]
- Spec master, after 0.31.2 and not in any release yet, adds: "A byte order
  mark (`U+FEFF`) at the very start of the document is ignored. Anywhere
  else, `U+FEFF` is an ordinary character." [source: cm-spec-master]
- The maintainer's reasoning on that change: "stripping off the BOM is part
  of the process of turning a sequence of bytes into a sequence of Unicode
  code points (which is what the spec deals with)." [source: cm-issue-845]
  (jgm, 2026-10-10)
- cmark 0.31.2 skips it in the byte feed ("// Skip UTF-8 BOM if present; see
  #334") and pins a literal BOM followed by `# Hi` (shown here as
  `\ufeff# Hi`) → `<h1>Hi</h1>` in `test/regression.txt`.
  [source: cmark]
- commonmark.js 0.31.2 has no BOM handling in `lib/` or `bin/commonmark`, so
  it reads U+FEFF as a first-line character. [observed: grep for `feff`,
  `bom`, `65279` over the fetched `lib/*.js` and `bin/commonmark` at 0.31.2
  returned nothing] The issue 845 body (a third party) reports the same and
  lists markdown-it with it, and cmark-gfm, comrak, micromark, markdown-rs and
  pulldown-cmark as stripping; those are [unverified] here.

### Frontmatter consumers

- python-frontmatter 1.3.0 does not strip a BOM: its YAML handler detects
  with `re.compile(r"^-{3,}\s*$", re.MULTILINE).match(text)` after
  `text.strip()`, and `"\ufeff".isspace()` is `False`.
  `frontmatter.loads("\ufeff---\ntype: X\n---\n\nBody.\n").metadata == {}`
  and the content keeps the BOM; decoding the bytes with `utf-8-sig` first
  gives `{'type': 'X'}`. [source: pyfm] [observed: repo `.venv`,
  python-frontmatter 1.3.0, PyYAML 6.0.4, `python -I`]
- Jekyll 4.4.1 forces a BOM-aware read for any UTF encoding: "# always use
  BOM when reading UTF-encoded files" then `merged[:encoding] =
  "bom|#{merged[:encoding]}"`, before matching
  `YAML_FRONT_MATTER_REGEXP = %r!\A(---\s*\n.*?\n?)^((---|\.\.\.)\s*$\n?)!m`.
  [source: jekyll] (`Utils.merged_file_read_opts`; `document.rb` line 483
  `File.read(path, **Utils.merged_file_read_opts(site, opts))`)
- Hugo 0.170.1: `TestChompBOM` builds a page whose file starts with
  `"\xef\xbb\xbf"` and asserts it gets its front-matter title ("Simple").
  `common/hugio` has `ReadFile`/`ReadAll`, documented as "It also removes the
  UTF-8 BOM if present" (`trimBOM`); which of them the content path calls was
  not traced. [source: hugo]
- gray-matter 4.0.3 passes all input through `strip-bom-string`
  (`if (typeof str === 'string' && str.charAt(0) === '\ufeff') return
  str.slice(1)`) before parsing: "// strip BOM and ensure that
  "file.content" is a string". [source: gray-matter] (`lib/utils.js`
  `toString`, `lib/to-file.js`)
- Obsidian 1.1.13: "File BOM will now automatically be stripped when
  Obsidian reads a file." [source: obsidian] Whether Obsidian keeps or drops
  the BOM when it saves the file again is [unverified]; a round-trip through
  the desktop app would settle it.

### This server

- Every vault-markdown read decodes with `utf-8-sig`; writes are plain
  `utf-8`, so a BOM file parses, is indexed and audited on its frontmatter,
  and loses its BOM on its next rewrite; fetched and uploaded bodies are
  decoded the same way. [pins:
  tests/test_utils_text.py::TestUtf8BomHelpers::test_read_text_utf8_strips_leading_bom,
  tests/test_utils_text.py::TestReadTextUtf8::test_strips_a_leading_bom,
  tests/test_scanner.py::test_parse_note_strips_bom_so_frontmatter_parses,
  tests/test_okf.py::TestOkfDetector::test_declaration_behind_a_bom_is_detected,
  tests/test_okf.py::TestOkfAudit::test_note_behind_a_bom_is_audited_on_its_frontmatter,
  tests/test_transfer_sink.py::test_write_note_strips_bom]

## Where this project departs from the subject

- None found. The server's `utf-8-sig` reading matches every OKF tool in the
  OKF repository at 527e81aff5. The spec text is silent, so this is the
  server's reading, chosen by the account holder on 28 November 2026 (#2452:
  "I'd say 3. At least as a safe read."), and recorded as agreement with
  OKF's tooling, not with a spec sentence. The same issue made the rest of
  §4 exact in every vault: a block opens only on a first line that is
  exactly `---` and closes on the next such line. [pins:
  tests/test_scanner.py::TestStripFrontmatterBlock::test_a_bom_and_crlf_line_endings_still_open_a_block,
  tests/test_scanner.py::TestStripFrontmatterBlock::test_a_block_not_opened_by_an_exact_first_line_is_body]

## Not covered

- An authoritative reading from the OKF authors. Neither the spec nor any
  issue in open-knowledge-format/open-knowledge-format addresses it (an
  issue search for "BOM" finds only issue 21, which is about non-UTF-8
  files).
- Whether more than one leading U+FEFF is stripped: OKF's validator and
  scanner strip all of them, its parser and this server strip one. No
  source addresses a doubled BOM in UTF-8.
- Obsidian's write-back behaviour and the CommonMark implementations named
  only in issue 845.
