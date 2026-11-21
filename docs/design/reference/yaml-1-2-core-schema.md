---
type: Reference
title: YAML 1.2 core schema
description: How YAML 1.2.2's core schema resolves plain scalars, how it differs from the YAML 1.1 resolution PyYAML implements, and what that means for reading and writing note frontmatter
subject_version: "YAML 1.2.2 (2021-10-01 revision) and the YAML 1.1 type definitions (2005); observed on PyYAML 6.0.4 (C loader) and python-frontmatter 1.3.0, Python 3.11.17"
valid_for: "YAML 1.2.x; re-research on a YAML 1.3 or a PyYAML or python-frontmatter release that changes resolution, dumping or handler selection"
generated:
  by: process:researching-references
  at: 2026-11-21T18:52:01+01:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-21T19:30:00+01:00
stale_after: 2027-11-21T00:00:00Z
status: stable
sources:
  - id: yaml122
    title: YAML Ain't Markup Language (YAML) version 1.2.2, §3.2.1.1, §6.8.1, §10.2, §10.3
    resource: https://yaml.org/spec/1.2.2/
    accessed: 2026-11-21
  - id: yaml11-bool
    title: Boolean Language-Independent Type for YAML Version 1.1
    resource: https://yaml.org/type/bool.html
    accessed: 2026-11-21
  - id: yaml11-timestamp
    title: Timestamp Language-Independent Type for YAML Version 1.1
    resource: https://yaml.org/type/timestamp.html
    accessed: 2026-11-21
  - id: okf-spec
    title: Open Knowledge Format (OKF), Version 0.3, SPEC.md at commit 527e81aff5, §4 and §5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/SPEC.md
    accessed: 2026-11-21
---

# YAML 1.2 core schema

OKF 0.3 §4: "The frontmatter MUST parse as a YAML 1.2 mapping with the core
schema." [source: okf-spec] (§4) This page records what the core schema
resolves, how YAML 1.1, which PyYAML implements, differs, and the
consequences for a server that both reads and writes frontmatter. The
decision to read through PyYAML's C loader with the core schema swapped in
is #2415's (option B, account holder, 2026-11-21).

## Scope

- Covers: plain-scalar tag resolution under the core schema (§10.3.2) and
  under YAML 1.1; the `%YAML` directive (§6.8.1); key uniqueness
  (§3.2.1.1); quoting on output.
- Does not cover: the failsafe and JSON schemas beyond what the core schema
  extends; flow and block syntax; anchors and aliases.
- Depended on by: `src/markdown_vault_mcp/_yaml12.py` (`Core12Loader`,
  `InteropDumper`, `YAML12_HANDLER`), `scanner.parse_frontmatter` (the one
  read entry point), every `frontmatter.dumps` call, and
  `_okf_log_repair.py`.

## Claims

### Core-schema resolution (§10.3.2)

- A plain scalar is "matched with an extended list of regular expressions.
  However, in this case, if none of the regular expressions matches, the
  scalar is resolved to tag:yaml.org,2002:str". The core schema extends the
  JSON schema's list, where "first match wins, e.g. 0 is resolved as
  !!int", so order decides `012` (an int, base 10) and `0.` (a float).
  [source: yaml122] (§10.2.2, §10.3.2)
- A quoted scalar is never resolved by these expressions: "All nodes with
  the “!” non-specific tag are resolved, by the standard convention, to
  “tag:yaml.org,2002:seq”, “tag:yaml.org,2002:map” or
  “tag:yaml.org,2002:str”, according to their kind." Quoting is how a
  writer keeps a string a string. [source: yaml122] (§10.3.2)
- The list is null (`null | Null | NULL | ~` and the empty scalar); bool
  (`true | True | TRUE | false | False | FALSE`); int (`[-+]? [0-9]+`,
  `0o [0-7]+`, `0x [0-9a-fA-F]+`); and float (`[-+]? ( \. [0-9]+ | [0-9]+
  ( \. [0-9]* )? ) ( [eE] [-+]? [0-9]+ )?`, `[-+]? ( \.inf | \.Inf | \.INF
  )`, `\.nan | \.NaN | \.NAN`). [source: yaml122] (§10.3.2)
  [pins: tests/test_yaml12.py::test_example_10_9_core_tag_resolution]
- Example 10.9 resolves `0o7` to 7, `0x3A` to 58, `+12e03` to 12000 and
  `0.` to 0.0, and keeps `""` a string. [source: yaml122] (§10.3.2)
  [pins: tests/test_yaml12.py::test_example_10_9_core_tag_resolution]
- The core schema has no timestamp entry, so an untagged `2026-06-30` or
  `2026-06-30T14:00:00Z` is a string; an explicit `!!timestamp` tag still
  applies ("None of the above recommended schemas preclude the use of
  arbitrary explicit tags"). [source: yaml122] (§10.3.2, §10.4)
- The core schema defines no merge key, so `<<: {a: 1}` is a literal key
  named `<<`, where YAML 1.1 merged it. [observed: `load_yaml` on that
  block]
  [pins: tests/test_yaml12.py::test_plain_scalars_resolve_by_the_core_schema, tests/test_yaml12.py::test_an_explicit_tag_still_applies]

### YAML 1.1, as PyYAML resolves it

- YAML 1.1's bool type matches `y|Y|yes|Yes|YES|n|N|no|No|NO`,
  `true|True|TRUE|false|False|FALSE` and `on|On|ON|off|Off|OFF`.
  [source: yaml11-bool]
- Its timestamp type resolves a date, or a date and time, as a timestamp,
  with "If the time zone is omitted, the timestamp is assumed to be
  specified in UTC". [source: yaml11-timestamp]
- PyYAML 6.0.4 resolves `yes` and `on` to `True`, `1_000` to 1000, `012` to
  10 (octal), `1:30` to 90 (base 60), and a date to a `date`. It leaves
  `0o17` a string, and it fails to load `2016-12-31T23:59:60Z`
  (`ValueError: second must be in 0..59`). It keeps the single letters
  `y`, `Y`, `n` and `N` strings, although YAML 1.1's bool type lists them.
  [observed: `yaml.safe_load` on each spelling, PyYAML 6.0.4, 2026-11-21]
  The core schema reads each of these as a string, except `012`, which is
  12, and `0o17`, which is 15.
  [pins: tests/test_yaml12.py::test_plain_scalars_resolve_by_the_core_schema]
- python-frontmatter 1.3.0 parses YAML blocks with `yaml.CSafeLoader` and
  writes them with `yaml.CSafeDumper`, and a parsed `Post` keeps its
  handler for `frontmatter.dumps`. [observed: `inspect.getsource` of
  `YAMLHandler.load` and `export`, and of `frontmatter.loads`]
  [pins: tests/test_yaml12.py::test_a_parsed_post_writes_back_with_the_same_handler]

### Writing: quoting has to match the reader

- A dumper decides whether a string needs quotes by asking its own
  resolver how the plain spelling would read. PyYAML's 1.1 dumper writes
  the strings `0o17`, `1e3` and `+12e03` plain, and a core-schema reader
  then loads them as 15, 1000.0 and 12000.0. A core-schema-only dumper
  would write `yes` plain, which every 1.1 reader loads as `True`.
  [observed: `yaml.safe_dump` then a core-schema load, PyYAML 6.0.4]
- The server therefore writes with both resolvers and quotes a string
  whenever either version would read its plain spelling as something else.
  [pins: tests/test_yaml12.py::test_a_written_string_reads_back_as_the_same_string]

### Directives and keys

- "A version 1.2 YAML processor must accept documents with an explicit
  “%YAML 1.2” directive, as well as documents lacking a “YAML” directive",
  and "must also accept documents with an explicit “%YAML 1.1” directive".
  A higher minor version "should be processed with an appropriate
  warning"; a higher major version "should be rejected with an
  appropriate error message", which PyYAML does. "Hence a version 1.2
  processor should process version 1.1 documents as if they were version
  1.2", and the loader does: a `%YAML 1.1` block resolves by the core
  schema too. [source: yaml122] (§6.8.1)
  [pins: tests/test_yaml12.py::test_a_yaml_1_2_directive_is_accepted]
- Mapping keys are unique: "an unordered set of key/value node pairs, with
  the restriction that each of the keys is unique". OKF: "Consumers MAY
  reject frontmatter that has duplicate keys." [source: yaml122] (§3.2.1.1)
  [source: okf-spec] (§4)

### OKF's rules for a producer (§4)

- "Producers MUST NOT write duplicate keys." A dump of a Python mapping
  cannot repeat a key. [source: okf-spec] (§4)
- "Producers SHOULD quote values that look like numbers or booleans, for
  example `okf_version: "0.3"`." The interop dumper quotes every string a
  YAML 1.1 or 1.2 reader would read as anything else, a superset of this.
  [source: okf-spec] (§4)
  [pins: tests/test_yaml12.py::test_a_written_string_reads_back_as_the_same_string]
- "Producers SHOULD NOT use custom YAML tags." The dumper writes none; a
  date is written as its ISO text. [source: okf-spec] (§4)
  [pins: tests/test_yaml12.py::test_a_tagged_timestamp_round_trips_as_the_string_it_reads_back_as]
- "A producer that modifies an existing file SHOULD NOT re-serialize values
  that it did not change." `frontmatter.dumps` re-serializes the whole
  block; that is a departure, tracked as #2419. [source: okf-spec] (§4)

## Where this project departs from the subject

- A document declaring `%YAML 1.3` is refused by PyYAML's parser ("found
  incompatible YAML document"), where §6.8.1 asks for processing with a
  warning. [observed: `yaml.load` with `Core12Loader`, PyYAML 6.0.4]
- A duplicate key keeps its last value instead of being rejected; OKF
  allows either.
- The loader is the C safe loader with the resolvers replaced and the int
  and bool constructors swapped. It refuses every `!!python/*` tag; explicit
  standard tags (`!!timestamp`, `!!set`, `!!binary`, `!!omap`, `!!pairs`)
  still build their types. An explicit `!!int` or `!!bool` on a spelling
  the core schema does not list (`!!bool yes`, `!!int 1_000`) is a YAML
  error.
  [pins: tests/test_yaml12.py::test_the_loader_constructs_no_python_objects, tests/test_yaml12.py::test_an_explicit_tag_on_a_non_core_spelling_is_a_yaml_error]
- A native date, which only an explicit `!!timestamp` produces now, is
  written back as its ISO text, so a load-write-load cycle turns it into
  the string a core-schema reader reads anyway.
  [pins: tests/test_yaml12.py::test_a_tagged_timestamp_round_trips_as_the_string_it_reads_back_as]

## Not covered

- What Obsidian's own frontmatter parser does with `yes`, `1_000` or a
  leap second. [unverified] Open the same note in Obsidian and read its
  Properties view.
