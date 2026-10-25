"""The harness's output: the run's environment, a JSON file, a markdown table.

A result is a plain dict, so a later run can be compared with this one by
reading the JSON alone. A connection string never reaches either output:
every key that names a DSN is dropped before writing or rendering, and a
PostgreSQL configuration is labelled by where its database came from.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

_MISSING = "—"


def _scrub(value: Any) -> Any:
    """Return *value* without any mapping key that names a DSN."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if "dsn" not in k.lower()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _run(*command: str, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False, cwd=cwd, timeout=30
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _proc_field(path: str, key: str) -> str:
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        name, _, value = line.partition(":")
        if name.strip() == key:
            return value.strip()
    return ""


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


def environment(out_dir: Path) -> dict[str, str]:
    """Describe what the run's figures depend on.

    Args:
        out_dir: The folder the run writes its stores in; its filesystem type
            is recorded.

    Returns:
        The git commit, Python, SQLite, numpy and psycopg versions, the CPU,
        memory, kernel and the filesystem type.
    """
    repo = Path(__file__).resolve().parent
    return {
        "git_sha": _run("git", "rev-parse", "--short", "HEAD", cwd=repo) or "unknown",
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "numpy": _version("numpy"),
        "psycopg": _version("psycopg"),
        "kernel": platform.release(),
        "cpu": _proc_field("/proc/cpuinfo", "model name") or platform.machine(),
        "memory": _proc_field("/proc/meminfo", "MemTotal"),
        "filesystem": _run("stat", "-f", "-c", "%T", str(out_dir)),
    }


def write_json(result: dict[str, Any], out_dir: Path) -> Path:
    """Write *result* to ``<git sha>-<size>.json`` in *out_dir*.

    Args:
        result: The run's result.
        out_dir: The folder; created when missing.

    Returns:
        The file written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = _scrub(result)
    path = out_dir / f"{clean['environment']['git_sha']}-{clean['size']}.json"
    path.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")
    return path


def _mib(value: Any) -> str:
    return _MISSING if value is None else f"{value / (1 << 20):.1f} MiB"


def _ms(value: Any) -> str:
    return _MISSING if value is None else f"{value * 1000:.1f} ms"


def _plain(value: Any) -> str:
    return _MISSING if value is None else f"{value:,}"


def _spread(stats: dict[str, Any] | None, fmt: Any) -> str:
    if not stats:
        return _MISSING
    return f"{fmt(stats.get('median'))} / {fmt(stats.get('max'))}"


def _name(config: dict[str, Any]) -> str:
    return f"{config['backend']} ({config['search']})"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(row) + " |" for row in rows),
        "",
    ]


def _memory_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        memory = config.get("memory", {})
        rows.append(
            [
                _name(config),
                _mib(memory.get("adds_anon")),
                _mib(memory.get("adds_resident")),
                _mib(memory.get("process_anon")),
                _mib(memory.get("file_backed")),
            ]
        )
    return rows


def _latency_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        latency = config.get("latency", {})
        warm, cold = latency.get("warm", {}), latency.get("cold", {})
        for kind in sorted(set(warm) | set(cold)):
            stats = warm.get(kind, {})
            rows.append(
                [
                    _name(config),
                    kind,
                    _ms(stats.get("p50")),
                    _ms(stats.get("p95")),
                    _ms(cold.get(kind)),
                ]
            )
    return rows


def _edit_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        edits = config.get("edits", {})
        rows.append(
            [
                _name(config),
                _plain(edits.get("count")),
                _spread(edits.get("wchar"), _plain),
                _spread(edits.get("store_delta"), _plain),
                _spread(edits.get("note_vector_bytes"), _plain),
                _spread(edits.get("texts_embedded"), _plain),
            ]
        )
    return rows


def _restart_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        restart = config.get("restart", {})
        recovery = config.get("recovery", {})
        rows.append(
            [
                _name(config),
                _MISSING
                if restart.get("seconds") is None
                else f"{restart['seconds']:.2f} s",
                _plain(restart.get("texts_embedded")),
                _plain(recovery.get("texts_beyond_allowed")),
                _MISSING
                if "moved_aside" not in recovery
                else str(recovery["moved_aside"]),
            ]
        )
    return rows


def render_markdown(result: dict[str, Any]) -> str:
    """Render *result* as markdown tables, one per kind of figure.

    Args:
        result: The run's result.

    Returns:
        The tables, with a header naming the size, notes, chunks and commit.
    """
    clean = _scrub(result)
    configs = clean.get("configs", [])
    env = clean.get("environment", {})
    lines = [
        f"## Envelope: {clean['size']}",
        "",
        f"{_plain(clean.get('notes'))} notes, {_plain(clean.get('chunks'))} "
        f"chunks, commit {env.get('git_sha', 'unknown')}.",
        "",
    ]
    sources = [f"{_name(c)}: {c['source']}" for c in configs if c.get("source")]
    if sources:
        lines += ["Sources: " + "; ".join(sources) + ".", ""]
    lines += ["### Memory", ""]
    lines += _table(
        [
            "Configuration",
            "Adds (anonymous)",
            "Adds (resident)",
            "Process",
            "File-backed",
        ],
        _memory_rows(configs),
    )
    lines += ["### Search latency", ""]
    lines += _table(
        ["Configuration", "Kind", "Warm p50", "Warm p95", "Cold"],
        _latency_rows(configs),
    )
    lines += ["### Single-note edits (median / max per edit)", ""]
    lines += _table(
        [
            "Configuration",
            "Edits",
            "wchar bytes",
            "Store growth",
            "Note vectors",
            "Texts embedded",
        ],
        _edit_rows(configs),
    )
    lines += ["### Restart and recovery", ""]
    lines += _table(
        [
            "Configuration",
            "Until search works",
            "Texts embedded",
            "Re-embedded after kill",
            "Store moved aside",
        ],
        _restart_rows(configs),
    )
    return "\n".join(lines)
