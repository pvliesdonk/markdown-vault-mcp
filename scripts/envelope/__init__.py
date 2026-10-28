"""Measure the assembled server's operating envelope (#1895, #1506).

A maintainer tool, not a test: it builds a synthetic vault at the deployed
size or ten times that, embeds it with a deterministic stand-in provider (no
paid calls), and measures each embedding backend as one whole process:
memory kept apart from page cache, warm and cold search latency, bytes
written by single-note edits, and time until search works after a restart.
It measures and does not judge; #1899 compares the figures with the budgets
agreed on #1506.

Linux only, because it reads ``/proc``. The metric definitions live in
``docs/design/design.md`` § "Measuring the operating envelope (#1506)".

Modules:
    synth: the synthetic vault and the stand-in embedding provider.
    probes: the ``/proc`` readers, the peak sampler, page-cache eviction and
        percentiles.
    report: the run's environment, the JSON file and the markdown table.
    worker: one measurement phase in a fresh process.
    postgres: the run's PostgreSQL and its service figures.
"""
