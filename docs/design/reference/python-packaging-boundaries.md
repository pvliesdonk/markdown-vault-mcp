---
type: Reference
title: Python packaging boundaries — extras, default extras, entry points, typing markers
description: What extras, the draft default-extras proposal, console-script entry points, PEP 561 markers and installed-file records allow when one project is split into library, server and client installs.
subject_version: "Core metadata 2.6 (May 2026); PEP 771 Draft; PEP 561 Final; hatchling as pinned in pyproject.toml; uv 0.12.24; fastmcp 4.1.6"
valid_for: "Core metadata 2.x; re-check if PEP 771 changes status or Metadata-Version 2.7 is approved"
generated:
  by: process:researching-references
  at: 2026-11-03T16:55:31+00:00
verified:
  - by: process:researching-references
    at: 2026-11-03T16:55:31+00:00
stale_after: 2027-05-03T16:55:31+00:00
status: stable
sources:
  - id: core-metadata
    title: Core metadata specifications
    resource: https://packaging.python.org/en/latest/specifications/core-metadata/
    accessed: 2026-11-03
  - id: dep-specifiers
    title: Dependency specifiers
    resource: https://packaging.python.org/en/latest/specifications/dependency-specifiers/
    accessed: 2026-11-03
  - id: pyproject-spec
    title: pyproject.toml specification
    resource: https://packaging.python.org/en/latest/specifications/pyproject-toml/
    accessed: 2026-11-03
  - id: pep-771
    title: PEP 771 – Default Extras for Python Software Packages
    resource: https://peps.python.org/pep-0771/
    accessed: 2026-11-03
  - id: entry-points
    title: Entry points specification
    resource: https://packaging.python.org/en/latest/specifications/entry-points/
    accessed: 2026-11-03
  - id: pep-561
    title: PEP 561 – Distributing and Packaging Type Information
    resource: https://peps.python.org/pep-0561/
    accessed: 2026-11-03
  - id: recording-installed
    title: Recording installed projects
    resource: https://packaging.python.org/en/latest/specifications/recording-installed-packages/
    accessed: 2026-11-03
  - id: hatch-build
    title: Hatch build configuration and wheel builder
    resource: https://hatch.pypa.io/latest/config/build/
    accessed: 2026-11-03
  - id: pypi-fastmcp
    title: PyPI JSON metadata for fastmcp 4.1.6 and fastmcp-slim 4.1.6
    resource: https://pypi.org/pypi/fastmcp-slim/4.1.6/json
    accessed: 2026-11-03
---

# Python packaging boundaries

This page records what Python packaging standards and tools allow when one
project is offered to different consumers: a library caller, an MCP server
operator, and a remote command-line client. It covers extras, the draft
default-extras proposal, console-script entry points, PEP 561 typing markers,
and what happens when two distributions ship the same files. It does not cover
how to publish to PyPI, how to rename a project there, or namespace-package
layouts beyond the one PEP 561 rule. Research
[#1473](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1473) depends
on it, along with `pyproject.toml`'s `[project.optional-dependencies]` and
`[project.scripts]`.

## Scope

- Covers: extras semantics, PEP 771 status and opt-out, entry points and
  extras, `py.typed`, overlapping files and uninstall, hatchling's wheel
  contents, and FastMCP's slim distribution.
- Does not cover: PyPI project renames or redirects, sdist contents, and
  namespace packages beyond PEP 561's rule.
- Depended on by: `pyproject.toml` (dependencies, extras, scripts), research
  #1473, and the 020 first-class-library refinement (#1472).

## Claims

### Extras

- An extra only adds requirements. Each `Requires-Dist` entry guarded by
  `extra == 'name'` applies when that extra is requested, and requesting
  several extras installs the union of their dependencies.
  [source: core-metadata] § Provides-Extra; [source: dep-specifiers] § Extras;
  [source: pyproject-spec] § optional-dependencies
- No standard mechanism makes a requirement apply only when an extra is *not*
  requested. `extra != "name"` "SHOULD NOT be used anymore and MAY be rejected
  or evaluated to false". [source: dep-specifiers] § Environment Markers
- Consequence: moving a base requirement into an extra means a bare
  `pip install <project>` or `uvx <project>` no longer installs it. This
  follows from the two claims above; nothing in the standards softens it today.
- Extra names must match `^[a-z0-9]+(-[a-z0-9]+)*$`, and tools writing
  metadata must refuse two `Provides-Extra` entries that clash after
  normalisation (PEP 685). [source: core-metadata] § Provides-Extra
- The current core-metadata version is 2.6, approved in May 2026.
  [source: core-metadata] § Metadata-Version
- Requesting an extra that a distribution does not declare installs the
  distribution and prints a warning in both pip 26.3 and uv 0.12.24; neither
  fails. Removing an extra therefore does not break an install that still
  names it. [observed: `python-frontmatter[nosuchextra]`, dry runs, 2026-11-03]

### Default extras (PEP 771)

- PEP 771 is **Draft**: Standards Track, created 13 January 2025, with no
  Resolution. [source: pep-771] header
- It proposes a `Default-Extra` core-metadata field. Each default extra is
  installed when the project is installed without naming any extras, and
  `default-optional-dependencies` would be the matching `pyproject.toml` key.
  It would need Metadata-Version 2.7. [source: pep-771] § Specification
- Under it, naming any extra replaces the defaults, and `package[]` installs
  without them. The PEP says this "changes the assumption that extras are
  exclusively additive". [source: pep-771] § Overriding default extras,
  § Packaging-related tools
- A project cannot rely on default extras to keep a bare install complete
  while it is Draft. [unverified] This is inferred from the status. Check
  installer support (pip, uv) if the PEP is accepted.

### Entry points

- An entry point may still declare extras (`name = mod:func [extra]`), but
  "using extras for an entry point is no longer recommended", and consumers
  "may then ignore them". [source: entry-points] § Data model, § File format
- Installers are expected to create wrappers for every `console_scripts`
  entry. The spec does not make scripts conditional on extras.
  [source: entry-points] § Use for scripts
- Consequence: a second console script in the same distribution narrows the
  command surface a user sees, but installs exactly what the first one does.

### Typing markers (PEP 561)

- A package that supports type checking "MUST add a marker file named
  `py.typed`" to the package, and the marker applies to every sub-package.
  PEP 561 is Final; the living text is the typing specification's
  "Distributing type information". [source: pep-561] § Packaging Type Information
- With hatchling's default selection, a `py.typed` file inside
  `src/markdown_vault_mcp/` ships in the wheel.
  [observed: `touch src/markdown_vault_mcp/py.typed && uv build --wheel`, then
  `unzip -l` listed `markdown_vault_mcp/py.typed`, 2026-11-03. Hatch's own pages
  do not say this explicitly; [source: hatch-build] § Default file selection
  only describes package discovery.] The built 6.1.0 wheel likewise already
  carries the non-Python files `static/app.html` and `static/icons/*.svg`.

### Two distributions, one set of files

- The installed-project specification has no rule for two distributions that
  install the same path. Uninstall removes "all files listed in `RECORD`", so
  uninstalling one of them removes files the other still needs.
  [source: recording-installed] § The RECORD file
- Core metadata's `Import-Name` field says tools "SHOULD raise an error when
  two projects that are about to be installed list names that overlap".
  [source: core-metadata] § Import-Name
- Consequence: splitting one import package (`markdown_vault_mcp`) across two
  distributions is unsafe without namespace packaging. A split that keeps
  existing imports working needs either a new import package with
  re-exporting shims, or a namespace layout.

### FastMCP's slim distribution

- `fastmcp` 4.1.6 is a thin wrapper that requires `fastmcp-slim[client,server]==4.1.6`.
  `fastmcp-slim`'s own base requirements carry no server or client transport
  stack. [source: pypi-fastmcp]
- `from fastmcp import Client` fails on bare `fastmcp-slim` with "FastMCP
  client support is not installed. Install `fastmcp` or `fastmcp-slim[client]`",
  and succeeds with `fastmcp-slim[client]`.
  [observed: fresh venvs, uv 0.12.24, Python 3.14, 2026-11-03]

### Install footprints measured for #1473

Measured with `uv pip install` into fresh venvs, Python 3.14, uv 0.12.24, on
2026-11-03. An empty venv is 72 KB.
[observed: `uv pip install` of each row into its own fresh `uv venv`, then
`uv pip list | wc -l` and `du -sh` on the venv]

| Install | Packages | Size |
| --- | --- | --- |
| `markdown-vault-mcp` 6.1.0 wheel, base dependencies | 81 | 80 MB |
| `fastmcp-pvl-core>=10.1,<11` alone | 76 | 74 MB |
| `fastmcp>=4,<5`, `typer`, `httpx` | 67 | 65 MB |
| `fastmcp-slim[client]==4.1.6`, `typer` | 49 | 55 MB |
| `python-frontmatter`, `numpy` | 3 | 59 MB |

## Where this project departs from the subject

None. This page records constraints; #1473 records the choice.

## Not covered

- PyPI renames and redirects: needed only if a split introduces a new
  distribution name.
- Namespace-package layouts for a shared import package.
