---
type: Reference
title: Bounding a regular expression with a child process
description: Why Python's re cannot bound its own run time, what subprocess.run does to a child at its timeout, what python -I keeps off sys.path, and how a replacement template is expanded
subject_version: "Python 3 documentation as published on 2026-11-28; template check observed on CPython 3.14 and 3.15"
valid_for: "CPython 3.11 to 3.15; research again when the supported range gains a release, or if re gains a timeout"
generated:
  by: process:researching-references
  at: 2026-11-28T20:00:00+01:00
stale_after: 2027-05-28T00:00:00+00:00
status: draft
sources:
  - id: py-re
    title: "re — Regular expression operations (Python 3 documentation)"
    resource: https://docs.python.org/3/library/re.html
    accessed: 2026-11-28
  - id: py-subprocess
    title: "subprocess — Subprocess management (Python 3 documentation)"
    resource: https://docs.python.org/3/library/subprocess.html
    accessed: 2026-11-28
  - id: py-cmdline
    title: "Command line and environment (Python 3 documentation)"
    resource: https://docs.python.org/3/using/cmdline.html
    accessed: 2026-11-28
---

# Bounding a regular expression with a child process

The text replacement across notes (#2031) runs a caller's regular expression
over many notes. This page records the standard-library behaviour its design
relies on. The design came from the contributed #2187.

## Scope

- Covers: whether `re` can bound its own run time, what `subprocess.run`
  does at its timeout, what isolated mode (`-I`) removes from `sys.path`, and
  how a replacement template is expanded and checked.
- Does not cover: the regular-expression syntax itself, or how long a given
  pattern takes.
- Depended on by: `src/markdown_vault_mcp/managers/text_replace.py`
  (`_regex_spans`), `src/markdown_vault_mcp/managers/_replace_worker.py`,
  and `docs/design/design.md` § Text Replacement Across Notes.

## Claims

- `re` takes no timeout: the documented signatures are
  `re.compile(pattern, flags=0)`, `Pattern.finditer(string[, pos[, endpos]])`,
  `Pattern.sub(repl, string, count=0)` and `Match.expand(template)`, and the
  page never mentions a timeout. So a match can only be bounded from outside
  the process running it. [source: py-re] (Module contents, Regular
  expression objects, Match objects)
  [pins: tests/test_text_replace.py::TestPreview::test_catastrophic_backtracking_is_killed]
- `subprocess.run` with a timeout kills the child and waits for it before it
  raises: "If the timeout expires, the child process will be killed and
  waited for. The `TimeoutExpired` exception will be re-raised after the
  child process has terminated." [source: py-subprocess] (Using the
  subprocess module, `run()`)
  [pins: tests/test_text_replace.py::TestPreview::test_catastrophic_backtracking_is_killed]
- The timeout does not cover process creation: "you are not guaranteed to
  see a timeout exception until at least after however long process creation
  takes." [source: py-subprocess] (Notes, Timeout Behavior)
- `Popen.communicate` with a timeout does not kill the child: "The child
  process is not killed if the timeout expires". Code that calls it directly
  must kill the child itself, which `run` does. [source: py-subprocess]
  (Popen objects)
- `-I` runs Python "in isolated mode", implies `-E`, `-P` and `-s`, does not
  prepend `sys._path0` to `sys.path`, and ignores every `PYTHON*`
  environment variable. For a script, `sys.path` then "contains neither the
  script's directory nor the user's site-packages directory". So the worker
  script can import only the standard library and installed packages, and
  nothing beside it. [source: py-cmdline] (`-I`, `<script>`)
  [pins: tests/test_text_replace.py::TestPreview::test_regex_with_groups]
- `-P` was added in 3.11 and `-I` in 3.4, so both exist on every supported
  interpreter. [source: py-cmdline]
- `Match.expand` returns "the string obtained by doing backslash
  substitution on the template string template, as done by the `sub()`
  method": `\1` and `\g<name>` insert groups, `\g<0>` the whole match, and
  since 3.5 an unmatched group becomes an empty string. [source: py-re]
  (Match objects) [pins: tests/test_text_replace.py::TestSpanEngines::test_regex_expands_groups]
- In a string replacement, an unknown escape of an ASCII letter is an error
  (since 3.7), and other unknown escapes such as `\&` are left alone.
  [source: py-re] (`re.sub`)
- The page does not say what an invalid group reference in a template
  raises. Observed: `re.compile("a").sub(r"\9", "")` raises
  `re.PatternError` ("invalid group reference 9 at position 1") even when
  the subject is empty, so a worker can check a template before matching
  anything. `re.error` is the pre-3.13 name of `PatternError` and stays an
  alias. [observed: uv run python on CPython 3.14.8 and 3.15.2; the alias: source
  py-re, Exceptions]
  [pins: tests/test_text_replace.py::TestSpanEngines::test_run_reports_errors_and_limits]
