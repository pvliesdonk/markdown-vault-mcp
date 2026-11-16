---
type: Reference
title: CLI reader test logging lifetimes
description: Capture and handler lifetimes used by the synthetic reader CLI harness.
subject_version: "Python 3.14.6, pytest 9.2.0, Click 8.5.1, Typer 0.29.1, pvl-core 10.2.0, Rich 15.0.0"
valid_for: "Recheck when the capture or logging dependencies change."
generated:
  by: process:researching-references
  at: 2026-11-14T17:48:00+00:00
stale_after: 2027-05-14T17:48:00+00:00
sources:
  - id: pytest
    title: pytest capture implementation
    resource: https://github.com/pytest-dev/pytest/blob/9.2.0/src/_pytest/capture.py
    accessed: 2026-11-14
  - id: click
    title: Click test capture implementation
    resource: https://github.com/pallets/click/blob/8.5.1/src/click/testing.py
    accessed: 2026-11-14
  - id: core
    title: pvl-core root logging installation
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/v10.2.0/src/fastmcp_pvl_core/_logging.py
    accessed: 2026-11-14
  - id: python
    title: Python logging emission and error handling
    resource: https://github.com/python/cpython/blob/v3.14.6/Lib/logging/__init__.py
    accessed: 2026-11-14
  - id: rich
    title: Rich console destination
    resource: https://github.com/Textualize/rich/blob/v15.0.0/rich/console.py
    accessed: 2026-11-14
---

# CLI reader test logging lifetimes

Synthetic reader capture/logging facts; historical CI handler attribution is unproven.

- pytest's `SysCapture.done` closes its temporary capture stream. A handler
  retaining that stream can outlive capture teardown. [source: pytest]
  [pins: tests/test_cli_logging_lifetime.py::test_reader_logging_survives_capture_teardown]
- Click's `CliRunner.isolation` temporarily replaces `sys.stderr`; in 8.5.1,
  `_NamedTextIOWrapper.close` is a no-op. A preceding runner invocation alone
  does not establish that its stream closed. [source: click]
- pvl-core's bootstrap replaces console handlers, preserves other sinks, and
  installs JSON `StreamHandler(sys.stderr)` or Rich handlers using
  `Console(stderr=True)`. [source: core]
- `StreamHandler.emit` reports a failed stream write through `handleError`,
  which writes its diagnostic to the current `sys.stderr`. [source: python]
  [pins: tests/test_cli_logging_lifetime.py::test_reader_logging_survives_capture_teardown]
- Rich's `Console.file` resolves current stderr when no explicit file is set;
  its public setter fixes the destination. Temporary redirection during
  construction alone does not fix subsequent writes. [source: rich]
  [pins: tests/test_cli_logging_lifetime.py::test_reader_logs_render_outside_command_capture]
