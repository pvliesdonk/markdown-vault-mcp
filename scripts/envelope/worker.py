"""One measurement phase in a fresh process (#1895).

``python -m scripts.envelope.worker <phase>`` reads its configuration as
JSON on stdin, runs one phase against the work folder it names and writes
its figures as one JSON line on stdout. The orchestrator in ``__main__`` starts a process
per phase, so each reading starts from a process that has done nothing else.

Phases:
    build: index the synthetic vault and embed it.
    memory: the "adds" memory figures and warm latency, in one process.
    cold: the first search of one kind after the files leave page cache.
    restart: the work a restarted server does before search works.
    edits: single-note edits through the server's write path; with
        ``kill_at`` set, the process kills itself mid-edit.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import signal
import statistics
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.envelope import probes, synth

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DSN_ENV = "MVM_ENVELOPE_POSTGRES_DSN"
"""How the orchestrator hands a PostgreSQL worker its connection string."""

KINDS = ("semantic", "hybrid", "filtered", "similar")
_KEYWORD_SEARCHES = 20
_STORE_GLOB = "index-db-embeddings*"
_INDEX_GLOB = "index.db*"
_CHECKPOINT = "edits-finished"  # read by the orchestrator
_STATUS_KEYS = (
    "search_mode",
    "search_workspace_bytes",
    "stored_bytes",
    "resident_vector_bytes",
)


def _postgres_dsn() -> str:
    """The connection string the orchestrator put in this process's environment.

    Held as a ``SecretDsn``, so a traceback that shows it prints it hidden.
    """
    from tests.fixtures.postgres import SecretDsn

    return SecretDsn(os.environ[DSN_ENV])


def _vault(config: dict[str, Any], provider: synth.StandInProvider) -> Vault:
    work = Path(config["work"])
    postgres: dict[str, Any] = {}
    if config["backend"] == "postgres":
        postgres = {
            "embedding_postgres_dsn": _postgres_dsn(),
            "embedding_postgres_schema": config["schema"],
        }
    return Vault(
        source_dir=work / "vault",
        settings=VaultSettings(
            index_path=work / "index.db",
            read_only=False,
            embedding_store=config["backend"],
            embedding_search=config["search"],
            embedding_batch_size=config["batch_size"],
            **postgres,
        ),
        embedding_provider=provider,
    )


def _paths(config: dict[str, Any]) -> list[str]:
    root = Path(config["work"]) / "vault"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))


def _files(config: dict[str, Any], pattern: str) -> list[Path]:
    return sorted(Path(config["work"]).glob(pattern))


def _store_bytes(config: dict[str, Any]) -> int:
    return sum(p.stat().st_size for p in _files(config, _STORE_GLOB) if p.is_file())


def _searches(vault: Vault, config: dict[str, Any]) -> dict[str, Callable[[int], Any]]:
    """Return one call per kind, taking the sample's number."""
    queries, paths = synth.QUERIES, _paths(config)
    reader = vault.reader
    return {
        "semantic": lambda i: reader.search(queries[i % len(queries)], mode="semantic"),
        "hybrid": lambda i: reader.search(queries[i % len(queries)], mode="hybrid"),
        "filtered": lambda i: reader.search(
            queries[i % len(queries)], mode="semantic", folder=synth.FILTER_FOLDER
        ),
        "similar": lambda i: reader.get_similar(paths[(i * 7919) % len(paths)]),
    }


def _timed(call: Callable[[], Any]) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def build(config: dict[str, Any]) -> dict[str, Any]:
    """Index the vault and embed it.

    The process's peak memory is sampled from before the vault opens until
    it has closed, so it includes reading every note and its metadata and
    holding the texts to embed (#1898). The seconds are timed with the
    sampler running.

    Args:
        config: The run's configuration.

    Returns:
        The notes and chunks indexed, the texts embedded, the seconds, and
        the process's peak anonymous and resident memory, in bytes.
    """
    provider = synth.StandInProvider(config["dim"])
    with probes.PeakSampler() as peak:
        vault = _vault(config, provider)
        try:
            start = time.perf_counter()
            stats = vault.index.build_index()
            vault.index.build_embeddings()
            seconds = time.perf_counter() - start
        finally:
            vault.close()
    return {
        "notes": stats.documents_indexed,
        "chunks": stats.chunks_indexed,
        "texts_embedded": provider.count,
        "seconds": seconds,
        "peak_anon": peak.peak_anon,
        "peak_resident": peak.peak_resident,
    }


def _status(vault: Vault, config: dict[str, Any]) -> dict[str, Any]:
    """Return ``embeddings_status``'s cost figures and the store's measured size.

    PostgreSQL's store has no local files; its measured size is its
    schema's, tables, indexes and TOAST.
    """
    reported = vault.index.embeddings_status()
    if config["backend"] == "postgres":
        from scripts.envelope import postgres

        sizes = postgres.sizes(_postgres_dsn(), config["schema"])
        measured = sizes["schema_bytes"]
    else:
        measured = _store_bytes(config)
    return {
        **{key: reported[key] for key in _STATUS_KEYS},
        "measured_store_bytes": measured,
    }


def memory(config: dict[str, Any]) -> dict[str, Any]:
    """Measure what semantic search adds to memory, and warm latency.

    The vault opens and answers keyword searches with the vector store still
    closed; the peak there is the baseline. Warm semantic searches of every
    kind follow; their peak minus the baseline is what semantic search adds.

    Args:
        config: The run's configuration.

    Returns:
        The memory figures, the index's and store's bytes in page cache
        after the searches (``None`` without ``fincore``), each kind's
        warm p50 and p95, in seconds, and what ``embeddings_status``
        reports after the searches beside the store's measured size.
    """
    provider = synth.StandInProvider(config["dim"])
    vault = _vault(config, provider)
    try:
        vault.index.build_index()
        with probes.PeakSampler() as before:
            for i in range(_KEYWORD_SEARCHES):
                vault.reader.search(
                    synth.QUERIES[i % len(synth.QUERIES)], mode="keyword"
                )
        closed = vault._vectors is None
        searches = _searches(vault, config)  # its path list is not search's
        warm = {}
        with probes.PeakSampler() as after:
            for kind, search in searches.items():
                for i in range(config["discard"]):
                    search(i)
                samples = [
                    _timed(functools.partial(search, i))
                    for i in range(config["queries"])
                ]
                warm[kind] = probes.percentiles(samples)
        now = probes.read_memory(os.getpid())
        cached = probes.resident_bytes(
            _files(config, _INDEX_GLOB) + _files(config, _STORE_GLOB)
        )
        status = _status(vault, config)
    finally:
        vault.close()
    return {
        "memory": {
            "store_closed_at_baseline": closed,
            "adds_anon": after.peak_anon - before.peak_anon,
            "adds_resident": after.peak_resident - before.peak_resident,
            "process_anon": now.anon,
            # The index and store are read, not mapped, so this is mostly
            # shared libraries; their page cache is the next figure.
            "mapped_files": now.file,
            "page_cache": cached,
        },
        "warm": warm,
        "status": status,
    }


def _eviction(config: dict[str, Any]) -> str:
    """Evict the index and store files; say whether they left page cache.

    SQLite maps ``-shm`` into the process, and mapped pages cannot be
    dropped, so it is evicted with the rest but left out of the check.
    """
    files = probes.evict(_files(config, _INDEX_GLOB) + _files(config, _STORE_GLOB))
    checked = [f for f in files if not f.name.endswith("-shm")]
    resident = probes.resident_bytes(checked)
    if resident is None:
        return "unconfirmed"
    return "confirmed" if resident == 0 else f"resident {resident} bytes"


def cold(config: dict[str, Any]) -> dict[str, Any]:
    """Time the first search of ``config["kind"]`` after eviction.

    Args:
        config: The run's configuration, with the kind to time.

    Returns:
        The seconds and whether the files left page cache.
    """
    vault = _vault(config, synth.StandInProvider(config["dim"]))
    try:
        vault.index.build_index()
        eviction = _eviction(config)
        search = _searches(vault, config)[config["kind"]]
        seconds = _timed(lambda: search(0))
    finally:
        vault.close()
    return {"seconds": seconds, "eviction": eviction}


class _MovedAside(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.seen = False

    def emit(self, record: logging.LogRecord) -> None:
        if str(record.msg).startswith("vector_store_moved_aside"):
            self.seen = True


def restart(config: dict[str, Any]) -> dict[str, Any]:
    """Boot as the server does, then search once.

    The server's boot (``domain.py``) builds the index (a no-op on a warm
    start), reindexes to pick up changes made while it was down (the
    default ``boot_reindex``), then builds embeddings. The search's single
    query is the one text a clean restart embeds.

    Args:
        config: The run's configuration.

    Returns:
        The process's age when the search returned, the texts embedded
        besides the query, and whether a corrupt store was moved aside.
    """
    provider = synth.StandInProvider(config["dim"])
    handler = _MovedAside()
    logging.getLogger("markdown_vault_mcp").addHandler(handler)
    vault = _vault(config, provider)
    try:
        vault.index.build_index()
        vault.index.reindex()
        vault.index.build_embeddings()
        vault.reader.search(synth.QUERIES[0], mode="semantic")
        seconds = probes.process_age()
    finally:
        vault.close()
    return {
        "seconds": seconds,
        "texts_embedded": provider.count - 1,
        "moved_aside": handler.seen,
    }


def _spread(values: list[int]) -> dict[str, float]:
    return {"median": statistics.median(values), "max": max(values)}


def _edit(vault: Vault, config: dict[str, Any], path: str, number: int) -> None:
    text = (Path(config["work"]) / "vault" / path).read_text(encoding="utf-8")
    frontmatter, body = synth.edited_note(text, edit=number)
    vault.writer.write(path, body, frontmatter)


def _wal_reader(config: dict[str, Any]) -> Callable[[], int] | None:
    """Return a reader of PostgreSQL's WAL position, for that backend only."""
    if config["backend"] != "postgres":
        return None
    from scripts.envelope import postgres

    dsn = _postgres_dsn()
    return lambda: postgres.wal_position(dsn)


def _edit_numbers(config: dict[str, Any]) -> Iterator[int]:
    first = config.get("first_edit", 0)
    yield from range(first, first + config["edits"])


def _drain(vault: Vault, config: dict[str, Any]) -> None:
    if not vault.index.wait_for_drain(timeout=config["drain_timeout_s"]):
        raise RuntimeError(
            f"the index writer did not drain within {config['drain_timeout_s']} s"
        )


def edits(config: dict[str, Any]) -> dict[str, Any]:
    """Make single-note edits through the server's write path.

    Each edit writes the note, then waits for the index writer to drain,
    which re-indexes and re-embeds it. With ``kill_at`` set, the process
    records each finished edit and kills itself once that many have
    finished and the next note is written, while the index writer's work
    on it is still queued or running.

    Args:
        config: The run's configuration.

    Returns:
        Per-edit spreads of the bytes written, store growth, texts embedded
        and the note's vector bytes. PostgreSQL's store has no local files,
        so its store growth is ``None`` and its WAL written is given instead
        (``wal_bytes``, counting every session's: ``wal_scope``).
    """
    provider = synth.StandInProvider(config["dim"])
    vault = _vault(config, provider)
    paths = _paths(config)
    checkpoint = Path(config["work"]) / _CHECKPOINT
    note_bytes = synth.CHUNKS_PER_NOTE * config["dim"] * 4
    wal = _wal_reader(config)
    figures: dict[str, list[int]] = {k: [] for k in ("wchar", "store", "texts", "wal")}
    try:
        vault.index.build_index()
        vault.reader.search(synth.QUERIES[0], mode="semantic")  # open the store
        for done, number in enumerate(_edit_numbers(config)):
            path = paths[(number * 7919) % len(paths)]
            # PostgreSQL writes no local store files: its growth is the WAL,
            # read outside the wchar window.
            position = wal() if wal else _store_bytes(config)
            io, texts = probes.read_io(os.getpid()), provider.count
            _edit(vault, config, path, number)
            if done == config.get("kill_at"):
                os.kill(os.getpid(), signal.SIGKILL)
            _drain(vault, config)
            figures["wchar"].append(probes.read_io(os.getpid())["wchar"] - io["wchar"])
            figures["texts"].append(provider.count - texts)
            if wal:
                figures["wal"].append(wal() - position)
            else:
                figures["store"].append(_store_bytes(config) - position)
            checkpoint.write_text(str(done + 1))
    finally:
        vault.close()
    result: dict[str, Any] = {
        "count": len(figures["texts"]),
        "wchar": _spread(figures["wchar"]),
        "store_delta": None if wal else _spread(figures["store"]),
        "texts_embedded": _spread(figures["texts"]),
        "note_vector_bytes": {"median": note_bytes, "max": note_bytes},
    }
    if wal:
        result["wal_bytes"] = _spread(figures["wal"])
        result["wal_scope"] = "cluster"
    return result


PHASES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "build": build,
    "memory": memory,
    "cold": cold,
    "restart": restart,
    "edits": edits,
}


def main(argv: list[str]) -> int:
    """Run the phase *argv* names on the configuration stdin carries.

    Args:
        argv: The phase's name.

    Returns:
        The exit status.
    """
    phase, config = argv[0], json.loads(sys.stdin.read())
    figures = PHASES[phase](config)
    sys.stdout.write(json.dumps(figures) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
