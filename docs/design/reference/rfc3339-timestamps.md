---
type: Reference
title: RFC 3339 timestamps
description: The RFC 3339 date-time and full-date grammar OKF timestamps follow, its offset semantics as updated by RFC 9557, the open erratum on the space separator, and how PyYAML and Python's fromisoformat depart from it
subject_version: "RFC 3339 (July 2002) as updated by RFC 9557 (April 2024); errata as of 2026-11-21; YAML 1.1 timestamp type (2005-01-18); observed on Python 3.11.17 and PyYAML 6.0.4"
valid_for: "RFC 3339 and RFC 9557 as published; re-research on a new RFC updating 3339, a ruling on erratum 5783, or a PyYAML or Python minor release that changes timestamp parsing"
generated:
  by: process:researching-references
  at: 2026-11-21T17:56:35+01:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-21T18:20:00+01:00
stale_after: 2027-11-21T00:00:00Z
status: stable
sources:
  - id: rfc3339
    title: "RFC 3339, Date and Time on the Internet: Timestamps (July 2002)"
    resource: https://www.rfc-editor.org/rfc/rfc3339.txt
    accessed: 2026-11-21
  - id: rfc9557
    title: "RFC 9557, Date and Time on the Internet: Timestamps with Additional Information (April 2024), §2"
    resource: https://www.rfc-editor.org/rfc/rfc9557.txt
    accessed: 2026-11-21
  - id: errata
    title: RFC Editor errata search for RFC 3339 (4 verified; 3 held for document update, 5783, 5624 and 6533)
    resource: https://www.rfc-editor.org/errata_search.php?rfc=3339
    accessed: 2026-11-21
  - id: erratum-5783
    title: RFC 3339 erratum 5783 (technical, §5.6, held for document update)
    resource: https://www.rfc-editor.org/errata/eid5783
    accessed: 2026-11-21
  - id: yaml-timestamp
    title: Timestamp Language-Independent Type for YAML Version 1.1 (working draft 2005-01-18)
    resource: https://yaml.org/type/timestamp.html
    accessed: 2026-11-21
  - id: py-fromisoformat
    title: Python 3.11 documentation, datetime.fromisoformat
    resource: https://docs.python.org/3.11/library/datetime.html#datetime.datetime.fromisoformat
    accessed: 2026-11-21
  - id: okf-spec
    title: Open Knowledge Format (OKF), Version 0.3, SPEC.md at commit 527e81aff5, §5
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/SPEC.md
    accessed: 2026-11-21
---

# RFC 3339 timestamps

OKF 0.3 §5 makes every timestamp value an RFC 3339 `date-time` or
`full-date` and says "No other form is valid" [source: okf-spec] (§5). This
page records what those two grammar rules accept, what the offsets mean,
where the RFC itself is ambiguous, and how the two parsers that hand the
server its values, PyYAML and Python's `datetime.fromisoformat`, differ from
it. The OKF side of the question is in [Open Knowledge Format
v0.3](okf-v0.3.md), "Timestamps".

## Scope

- Covers: the §5.6 grammar, the §5.7 restrictions, the §4.3 unknown-offset
  convention as updated by RFC 9557, the errata on §5.6, the YAML 1.1
  timestamp type PyYAML implements, and `fromisoformat` in Python 3.11.
- Does not cover: RFC 9557's IXDTF suffix (`[Europe/Paris]`, §3–4), which
  OKF does not admit; ISO 8601 itself, which is not freely available;
  leap-second tables.
- Depended on by: `src/markdown_vault_mcp/okf.py` (`_DATE_TIME_RE`,
  `_rfc3339_date_time`, `_parse_timestamp`, and `okf_timestamp`, which
  writes the server's own stamps), `scripts/check_references.py`
  (`_as_instant`), `docs/design/okf.md` § 1 ("Lifecycle").

## Claims

### The grammar (§5.6)

- `date-time = full-date "T" full-time`, `full-date = date-fullyear "-"
  date-month "-" date-mday`, `full-time = partial-time time-offset`.
  [source: rfc3339] (§5.6)
  [pins: tests/test_okf_rfc3339.py::test_rfc3339_forms_parse]
- Seconds are required: `partial-time = time-hour ":" time-minute ":"
  time-second [time-secfrac]`. A fraction is `"." 1*DIGIT`, with no limit on
  its length, so a comma or a bare `.` is outside the rule.
  [source: rfc3339] (§5.6)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- `time-offset = "Z" / time-numoffset` and `time-numoffset = ("+" / "-")
  time-hour ":" time-minute`, so a numeric offset always carries its colon
  and both its fields, and reuses their ranges: `+24:00` and `+05:60` are
  outside the rule, and an offset need not be whole hours (§5.8's
  `1937-01-01T12:00:27.87+00:20`). [source: rfc3339] (§5.6, §5.8)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- "Per [ABNF] and ISO8601, the "T" and "Z" characters in this syntax may
  alternatively be lower case "t" or "z" respectively." Where case matters
  ("e.g. XML"), "Specifications that use this format in such environments
  MAY further limit the date/time syntax so that the letters 'T' and 'Z'
  used in the date/time syntax must always be upper case", and
  "Applications that generate this format SHOULD use upper case letters."
  OKF §5 states no case restriction. [source: rfc3339] (§5.6)
  [source: okf-spec] (§5)
  [pins: tests/test_okf_rfc3339.py::test_rfc3339_forms_parse]
- The server's own stamps are upper case, at seconds precision, in UTC
  with `Z` (`okf_timestamp`), which meets the SHOULD.
  [pins: tests/test_okf_write.py::TestApplyOkfWriteStamp::test_the_stamp_is_a_quoted_string_not_a_yaml_timestamp]

### Ranges (§5.6, §5.7)

- `time-hour` is 00–23: "Although ISO 8601 permits the hour to be "24",
  this profile of ISO 8601 only allows values between "00" and "23"".
  [source: rfc3339] (§5.6, §5.7)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- `time-minute` is 00–59, and `time-second` 00–59 outside a leap second,
  so `14:60` and `:61` are outside the rule. [source: rfc3339] (§5.6)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- `date-mday` is bounded by month and leap year (the §5.7 table).
  [source: rfc3339] (§5.7)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- `time-second` "may have the value "60" at the end of months in which a
  leap second occurs", and the leap point shifts with the zone offset "so
  it happens at the same instant around the globe". §5.8 gives
  `1990-12-31T23:59:60Z` and `1990-12-31T15:59:60-08:00` as the same
  instant. The server accepts 60 for any time and reads it as the second
  after `:59`; it does not check the leap-second table. §5.7 also says
  "Applications should not generate timestamps involving inserted leap
  seconds until after the leap seconds are announced"; the server's own
  stamps never carry `:60`. [source: rfc3339] (§5.7, §5.8)
  [pins: tests/test_okf_rfc3339.py::test_rfc3339_forms_parse]

### The space separator: an open erratum

- After the grammar, a note says "ISO 8601 defines date and time separated
  by "T". Applications using this syntax may choose, for the sake of
  readability, to specify a full-date and full-time separated by (say) a
  space character." [source: rfc3339] (§5.6)
- Erratum 5783 (technical, held for document update) reports the
  conflict: "the ABNF allows only "T"/"t" as the separator, but the
  "NOTE:" paragraph describes acceptability of other separators", and does
  not resolve it. [source: erratum-5783] [source: errata]
- OKF names the `date-time` rule, not the note, and admits "No other
  form", so the server reads a quoted string with a space as not an OKF
  timestamp (#2428). This is a reading of an ambiguous source, recorded as
  such. [source: okf-spec] (§5)
  [pins: tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]
- No verified erratum touches §5.6: the four verified ones are on
  Appendix A, §5.1 and §6. Of the three held for document update, 5783 and
  5624 are on §5.6 and 6533 fixes a comma in the table of contents.
  Erratum 5624 (editorial) proposes a paragraph saying the productions
  may be used on their own: "Other productions (e.g. 'full-date',
  'full-time', 'partial-time' may be referenced by applications that have
  different requirements"; its first sentence names `'full-time'` where it
  apparently means `date-time`. It supports OKF's use of `full-date` on
  its own. [source: errata]

### Offsets and their meaning (§4.3, RFC 9557)

- RFC 3339 §4.3 gave `-00:00` the meaning "the time in UTC is known, but
  the offset to local time is unknown", and `Z` or `+00:00` the meaning
  that UTC "is the preferred reference point". [source: rfc3339] (§4.3)
- RFC 9557 updates §4.3: `Z` now means "the time in UTC is known, but the
  offset to local time is unknown", because ISO 8601:2000 "and later
  versions do not actually allow -00:00"; `+00:00` keeps the old meaning,
  and `-00:00` is not formally deprecated, though "the local offset Z
  should now be used in its place". [source: rfc9557] (§2.1–2.3)
- All three name the same instant; RFC 9557 says outright that "-00:00
  may be used instead of Z because they have the same meaning according to
  Section 2". [source: rfc9557] (§3.4) OKF compares instants only, so the
  server reads `Z`, `+00:00` and `-00:00` alike and keeps no local-offset
  semantics. [source: okf-spec] (§5, §5.3, §5.5)
  [pins: tests/test_okf_rfc3339.py::test_rfc3339_forms_parse]

### PyYAML: what arrives as a native value

- The YAML 1.1 timestamp type is looser than RFC 3339 in most respects.
  Its regexp allows one-digit month, day and hour, `[Tt]` or whitespace
  between date and time, a bare `.` fraction, whitespace before `Z`, an
  optional zone, and offsets like `-5` with optional minutes. "If the time
  zone is omitted, the timestamp is assumed to be specified in UTC." It is
  also stricter in two: seconds are required, and only an upper-case `Z`
  is a zone, so an RFC-valid lower-case `z` is not a YAML 1.1 timestamp.
  [source: yaml-timestamp]
- PyYAML 6.0.4 builds an unquoted `2026-06-30 14:00:00Z` as an aware
  `datetime`, but an unquoted `2026-06-30 14:00:00` as a *naive* one, not
  as UTC. It leaves `2026-06-30T14:00Z` and lower-case
  `2026-06-30t14:00:00z` as strings, because the YAML 1.1 regexp it
  implements needs seconds and an upper-case `Z`. [observed: `yaml.safe_load` on each spelling, Python
  3.11.17, PyYAML 6.0.4, 2026-11-21]
- An unquoted `2016-12-31T23:59:60Z`, a valid RFC 3339 leap second, makes
  PyYAML raise `ValueError: second must be in 0..59` while loading. The
  server then reports the whole note's frontmatter as unparseable.
  [observed: `parse_frontmatter` raises `MalformedFrontmatterError`, and
  `audit_bundle` lists the note under `unparseable_frontmatter`]
  Under the YAML 1.2 core schema OKF §4 requires, the value is a string,
  and since #2415 the server reads it that way, so the note parses and its
  `stale_after` is read by the RFC 3339 grammar.
  [pins: tests/test_yaml12.py::test_a_leap_second_note_is_readable_and_its_stale_after_parses]
- OKF §5: "Consumers MUST accept a quoted string and a YAML-native
  timestamp." The server accepts a native value with an offset as that
  instant and a native date as 00:00 UTC. It reads a naive native value as
  invalid, following §13.2's "A date-time without an offset stays invalid",
  although YAML 1.1 would read it as UTC. The account holder chose OKF's
  rule on 21 November 2026: "We follow okf here". [source: okf-spec] (§5, §13.2)
  [source: yaml-timestamp] OKF §4 also requires the YAML 1.2 core schema,
  which has no timestamp tag, so read strictly the "YAML-native timestamp"
  rule has force only under a YAML 1.1 loader. Since #2415 the server reads
  frontmatter by the core schema, so a timestamp arrives as the string
  written unless it carries an explicit `!!timestamp` tag, and the native
  path above serves only such tagged values ([YAML 1.2 core
  schema](yaml-1-2-core-schema.md)). [source: okf-spec] (§4, §5)
  [pins: tests/test_okf_rfc3339.py::test_yaml_native_timestamps_are_still_accepted]

### `datetime.fromisoformat`: why it is not the gate

- Python 3.11 documents `fromisoformat` as accepting "any valid ISO 8601
  format", with exceptions including "The T separator may be replaced by
  any single unicode character". Its examples parse `20111104`,
  `2011-W01-2T00:05:23.283` and `2011-11-04 00:05:23.283`.
  [source: py-fromisoformat]
- On Python 3.11.17 it also accepts `2026-06-30T14:00Z` (no seconds),
  `2026-06-30T14:00:00,5Z` (comma fraction) and `20260630T140000Z`, and it
  rejects the RFC-valid `2016-12-31T23:59:60Z` and
  `2026-06-30T14:00:00z` (the lower-case `z`; a lower-case `t` is
  accepted). So it accepts forms RFC 3339 excludes and
  rejects forms RFC 3339 allows; v6.1.4 parsed `stale_after` with it.
  [observed: `datetime.fromisoformat` on each spelling]
- The server and the checker therefore test the RFC grammar with their own
  pattern first and build the instant from its fields (#2428).
  [pins: tests/test_okf_rfc3339.py::test_rfc3339_forms_parse, tests/test_okf_rfc3339.py::test_non_rfc3339_forms_are_rejected]

## Where this project departs from the subject

- A fraction with more than six digits keeps only the first six, the
  microsecond precision a Python `datetime` holds. RFC 3339 sets no limit
  on its length.
- A `:60` second is accepted at any time of day, not only at a scheduled
  leap second, and with any offset: §5.7 shifts the leap point by the zone
  offset, so `2016-12-31T23:59:60+05:00` is not a real leap second, yet it
  reads as `2017-01-01T00:00:00+05:00`.
- `Z`, `+00:00` and `-00:00` are not told apart, as above.

## Not covered

- A year `0000`, which `4DIGIT` allows and a Python `date` cannot hold, so
  it reads as invalid. Not observed in a vault.
- Negative leap seconds (a maximum `time-second` of 58), which the
  server does not model.
