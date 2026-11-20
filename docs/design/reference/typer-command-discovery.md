---
type: Reference
title: Typer metadata for offline command discovery
description: "Native command contexts, runnable groups and usage pieces used by command discovery."
kind: reference
subject_version: "Typer 0.29.1"
valid_for: "Typer 0.29.1; recheck on Typer upgrades"
generated:
  by: process:researching-references
  at: 2026-11-14T11:59:00+00:00
stale_after: 2027-05-14T11:59:00+00:00
verified:
  - by: process:researching-references
    at: 2026-11-14T11:59:00+00:00
status: stable
sources:
  - id: typer-core
    title: Typer 0.29.1 native command and argument implementation
    resource: https://github.com/fastapi/typer/blob/0.29.1/typer/core.py
    accessed: 2026-11-14
  - id: vendored-core
    title: Typer 0.29.1 vendored command and context implementation
    resource: https://github.com/fastapi/typer/blob/0.29.1/typer/_click/core.py
    accessed: 2026-11-14
---

# Typer metadata for offline command discovery

## Scope

This reference covers the compiled command metadata consumed by
`_cli_search.py` and the [command-discovery design](../design.md#offline-command-discovery).
It does not cover parsing arbitrary command arguments or running callbacks.

## Claims

- `TyperGroup` inherits the vendored `Command` directly and holds a `commands`
  mapping; testing against the separately installed Click `Group` misses this
  tree. [source: typer-core]
  [pins: tests/test_cli_command_search.py::test_live_registration_and_native_usage]
- Constructing `command.context_class(command, info_name=name, parent=parent)`
  initializes an unparsed context, without evaluating parameter defaults or
  invoking callbacks. `make_context` also parses arguments and is not used for
  discovery. [source: vendored-core]
  [pins: tests/test_cli_command_search.py::test_live_registration_and_native_usage]
- `collect_usage_pieces` returns the command's options metavar and parameter
  usage pieces; the native argument display in 0.29.1 is `{record}` for a
  required argument, `[suffix]` for an optional argument and `[labels]...` for
  an optional variadic argument. [source: typer-core] [source: vendored-core]
  [observed: synthetic command_tree read leaf, locked 0.29.1 runtime]
  [pins: tests/test_cli_command_search.py::test_live_registration_and_native_usage]
- Groups expose `invoke_without_command` and `no_args_is_help` independently
  of their callback. A callback alone does not make an invocation without a
  child runnable. [source: typer-core]
  [pins: tests/test_cli_command_search.py::test_pruning_runnable_groups_and_ties]
- A group's usage collector appends `COMMAND [ARGS]...`; preserving native
  pieces therefore also describes a runnable group's optional subcommand.
  [source: typer-core]
  [pins: tests/test_cli_command_search.py::test_pruning_runnable_groups_and_ties]

## Refutation and completeness

Read the installed locked wheel's `TyperGroup.__init__`,
`Command.collect_usage_pieces`, `Context.__init__`, and
`TyperArgument._usage_display_name` in an isolated remote environment. Attempts
at the old vendored `Group` and `make_metavar` APIs raised `AttributeError`;
those APIs are not assumed. A synthetic leaf with a failing default factory
returned native usage pieces without evaluating it. Required argument names
are lower-case and braced in this release; uppercase examples are not a
portable contract. Group flags were checked on all three synthetic combinations.

## Where this project departs from the subject

Discovery intentionally prunes hidden/deprecated ancestors, persistent `serve`,
reserved help/completion entries and its own `cli` subtree. It builds canonical
root paths rather than inheriting a test runner's executable name.

## Not covered

Custom lazy command loaders and custom context subclasses with side effects
are not registered by this project. Recheck this reference when introducing
one or upgrading Typer.
