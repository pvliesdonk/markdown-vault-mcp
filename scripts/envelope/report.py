"""The harness's output: the run's environment, a JSON file, a markdown table.

A result is a plain dict, so a later run can be compared with this one by
reading the JSON alone. A connection string never reaches either output:
every key that names a DSN is dropped before writing or rendering, and a
PostgreSQL configuration is labelled by where its database came from.
"""

from __future__ import annotations

import json
import platform
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

_MISSING = "—"
_HIDDEN = "<hidden>"
_SECRET_KEY = re.compile(r"dsn|conninfo|password|database_url", re.IGNORECASE)
_SECRET_TEXT = (
    re.compile(r"postgres(?:ql)?://\S+", re.IGNORECASE),
    re.compile(r"password\s*=\s*\S+", re.IGNORECASE),
)


def hide_secrets(text: str) -> str:
    """Replace connection URIs and ``password=`` pairs in *text*.

    Args:
        text: Free text, such as a worker's error output.

    Returns:
        The text with each match replaced by ``<hidden>``.
    """
    for pattern in _SECRET_TEXT:
        text = pattern.sub(_HIDDEN, text)
    return text


def _scrub(value: Any) -> Any:
    """Return *value* without connection strings.

    Mapping keys that name one (a DSN, a conninfo, a password, a database
    URL) are dropped and the rest made strings, as JSON needs, and URIs and
    ``password=`` pairs inside strings are replaced, through dicts, lists,
    tuples and sets.
    """
    if isinstance(value, dict):
        return {
            str(k): _scrub(v)
            for k, v in value.items()
            if not _SECRET_KEY.search(str(k))
        }
    if isinstance(value, list | tuple | set):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return hide_secrets(value)
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


def environment(out_dir: Path) -> dict[str, Any]:
    """Describe what the run's figures depend on.

    Args:
        out_dir: The folder the run writes its stores in; its filesystem type
            is recorded.

    Returns:
        The git commit and whether tracked files differ from it, the Python,
        SQLite, numpy and psycopg versions, the CPU, memory, kernel and the
        filesystem type.
    """
    repo = Path(__file__).resolve().parent
    status = _run("git", "status", "--porcelain", "--untracked-files=no", cwd=repo)
    return {
        "git_sha": _run("git", "rev-parse", "--short", "HEAD", cwd=repo) or "unknown",
        "dirty": bool(status),
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
    """Write *result* to ``<git sha>[-dirty]-<size>-<UTC time>.json`` in *out_dir*.

    The time keeps a rerun from overwriting an earlier result.

    Args:
        result: The run's result.
        out_dir: The folder; created when missing.

    Returns:
        The file written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = _scrub(result)
    env = clean["environment"]
    commit = env["git_sha"] + ("-dirty" if env.get("dirty") else "")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = out_dir / f"{commit}-{clean['size']}-{stamp}.json"
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


def _yes_no(value: bool | None) -> str:
    if value is None:
        return _MISSING
    return "yes" if value else "no"


def _memory_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        memory = config.get("memory", {})
        closed = memory.get("store_closed_at_baseline")
        adds = [_mib(memory.get("adds_anon")), _mib(memory.get("adds_resident"))]
        if closed is False:  # an open store at the baseline hides what search adds
            adds = ["invalid: store open"] * 2
        rows.append(
            [
                _name(config),
                *adds,
                _mib(memory.get("process_anon")),
                _mib(memory.get("mapped_files")),
                _mib(memory.get("page_cache")),
                _yes_no(closed),
            ]
        )
    return rows


def _latency_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        latency = config.get("latency", {})
        warm, cold = latency.get("warm", {}), latency.get("cold", {})
        eviction = latency.get("eviction", {})
        for kind in sorted(set(warm) | set(cold)):
            stats = warm.get(kind, {})
            rows.append(
                [
                    _name(config),
                    kind,
                    _ms(stats.get("p50")),
                    _ms(stats.get("p95")),
                    _ms(cold.get(kind)),
                    eviction.get(kind, _MISSING),
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
                _plain(recovery.get("texts_embedded")),
                _plain(recovery.get("texts_beyond_allowed")),
                f"{_yes_no(restart.get('moved_aside'))} / "
                f"{_yes_no(recovery.get('moved_aside'))}",
            ]
        )
    return rows


def _service_rows(configs: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for config in configs:
        service = config.get("service")
        if service is None:
            continue
        rows.append(
            [
                _name(config),
                _mib(service.get("memory")),
                _mib(service.get("database_bytes")),
                _mib(service.get("schema_bytes")),
                _spread(config.get("edits", {}).get("wal_bytes"), _plain),
                f"{service.get('server_version', _MISSING)} / "
                f"{service.get('pgvector_version', _MISSING)}",
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
            "Mapped files",
            "Index and store in page cache",
            "Store closed at baseline",
        ],
        _memory_rows(configs),
    )
    lines += ["### Search latency", ""]
    lines += _table(
        ["Configuration", "Kind", "Warm p50", "Warm p95", "Cold", "Cold eviction"],
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
    if any(c.get("backend") == "postgres" for c in configs):
        lines += [
            "PostgreSQL's store writes no local files: its wchar counts the "
            "local index only, and its store's writes are the WAL column of "
            "the PostgreSQL service table.",
            "",
        ]
    lines += ["### Restart and recovery", ""]
    lines += _table(
        [
            "Configuration",
            "Until search works",
            "Texts embedded",
            "Texts embedded after a kill",
            "Beyond the edit in progress",
            "Store moved aside (restart / after a kill)",
        ],
        _restart_rows(configs),
    )
    services = _service_rows(configs)
    if services:
        lines += ["### PostgreSQL service", ""]
        lines += _table(
            [
                "Configuration",
                "Service memory",
                "Database",
                "Vectors' schema",
                "WAL per edit, cluster-wide (median / max)",
                "PostgreSQL / pgvector",
            ],
            services,
        )
    return "\n".join(lines)
