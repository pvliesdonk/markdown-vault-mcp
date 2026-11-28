---
type: Reference
title: Typer parameter sources
description: How Typer distinguishes explicitly supplied local options from defaults.
subject_version: "Typer 0.29.1; Click 8.5.1"
valid_for: "Typer 0.29.x; recheck when changing the CLI dependency"
generated:
  by: process:researching-references
  at: 2026-11-14T09:15:00+00:00
stale_after: 2027-05-14T09:15:00+00:00
verified:
  - by: process:source-refute
    at: 2026-11-14T09:15:00+00:00
status: stable
sources:
  - id: typer-core
    title: Typer 0.29.1 vendored Click core
    resource: https://github.com/fastapi/typer/blob/0.29.1/typer/_click/core.py
    accessed: 2026-11-14
  - id: click-core
    title: Click 8.5.1 parameter source enumeration
    resource: https://github.com/pallets/click/blob/8.5.1/src/click/core.py
    accessed: 2026-11-14
---

# Typer parameter sources

## Scope

- Covers explicit command-line values, including a Boolean equal to its default.
- Depended on by `_cli_admin.py` and the [hosted CLI design](../design.md#hosted-cli-1831-2060).
- Observations use the installed wheel sources for the versions above in a synthetic remote sandbox, not a developer's configuration.

## Claims

- Typer's vendored `Context.get_parameter_source(name)` returns its recorded enum member, or `None`; its docstring distinguishes an explicit value equal to the default from a value actually taken from the default. [source: typer-core] [observed: installed `typer/_click/core.py`, lines 498–507; synthetic `index --no-force reindex` invocation] [pins: tests/test_cli_structure.py::test_index_parent_local_options_rejected_with_hosted_child]
- Typer's vendored `ParameterSource` is an `enum.Enum` with a member named `COMMANDLINE`; Click's separately installed `ParameterSource` is an `enum.IntEnum`. Their command-line members compare unequal in these versions. [source: typer-core] [source: click-core] [observed: installed source inspected with `inspect.getsource`, then `typer._click.core.ParameterSource.COMMANDLINE == click.core.ParameterSource.COMMANDLINE` returned `False`] [pins: tests/test_cli_structure.py::test_index_parent_local_options_rejected_with_hosted_child]
- A supplied `--no-force` is recorded as `COMMANDLINE` although its resulting value is `False`. [observed: synthetic Typer `index --no-force reindex` callback printed `force`, `ParameterSource.COMMANDLINE`, and `False` for equality with Click's member] [pins: tests/test_cli_structure.py::test_index_parent_local_options_rejected_with_hosted_child]

## Where this project departs from the subject

No semantic departure. The compatibility guard checks the returned member's name, avoiding both a truthiness check and an import from Typer's private vendored package. The omitted-option case is pinned by `tests/test_cli_write.py::test_native_wire_mapping` for hosted reindex.

## Not covered

Environment, prompt and default-map precedence; other Typer releases. No claim of compatibility with a future enum rename.
