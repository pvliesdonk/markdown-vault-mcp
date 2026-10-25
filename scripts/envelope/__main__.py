"""Run the envelope measurement (#1895).

Usage, from the repository root::

    uv run python -m scripts.envelope --size deployed --backends numpy,sqlite \\
        --out ~/envelope-runs

For each backend it generates the synthetic vault in ``OUT/work/<backend>``,
then runs each phase in a fresh process (``scripts.envelope.worker``): build,
memory and warm latency, a cold search of each kind, restart, single-note
edits, and a recovery run that kills itself mid-edit and restarts. It writes
``OUT/<git sha>-<size>.json`` and prints the markdown table. At the growth
size a run needs a few GB in ``OUT``; nothing is written inside the
repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from scripts.envelope import report, synth
from scripts.envelope.worker import KINDS

REPO = Path(__file__).resolve().parents[2]
SIZES = {"deployed": 22_764, "growth": 227_640}
"""Target chunk counts: the deployed vault (#1501) and ten times that."""

BACKENDS = {"numpy": "resident", "sqlite": "streamed"}
"""Each backend measured, with the search mode it runs in."""

_SENTINEL = ".envelope-work"
_TEXTS_PER_EDIT = 1
"""Texts an edit leaves to embed: it rewrites one section, one chunk."""
_CHECKPOINT = "edits-finished"


def _count(minimum: int) -> Any:
    def parse(value: str) -> int:
        number = int(value)
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}")
        return number

    return parse


def _backends(value: str) -> list[str]:
    names = [n.strip() for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in BACKENDS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"unknown backend {', '.join(unknown) or '(none)'}; "
            f"choose from {', '.join(BACKENDS)}"
        )
    return names


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.envelope")
    parser.add_argument("--size", choices=sorted(SIZES), default="deployed")
    parser.add_argument("--notes", type=_count(1), help="note count; overrides --size")
    parser.add_argument("--backends", type=_backends, default=list(BACKENDS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dim", type=_count(1), default=synth.DEFAULT_DIMENSION)
    parser.add_argument("--queries", type=_count(1), default=50)
    parser.add_argument("--discard", type=_count(0), default=5)
    parser.add_argument("--edits", type=_count(1), default=200)
    parser.add_argument(
        "--kill-after",
        type=_count(0),
        help="finished edits before the kill; default half",
    )
    parser.add_argument(
        "--batch-size", type=_count(1), default=64, help="texts per provider call"
    )
    parser.add_argument("--seed", type=int, default=1506)
    parser.add_argument(
        "--phase-timeout", type=_count(1), default=7200, help="seconds per phase"
    )
    parser.add_argument(
        "--drain-timeout",
        type=_count(1),
        default=600,
        help="seconds an edit may wait for the index writer",
    )
    return parser


def cold_warning(environment: dict[str, str]) -> str | None:
    """Return a warning when the work folder's pages cannot leave memory.

    Args:
        environment: The run's environment, with its filesystem type.

    Returns:
        The warning on tmpfs or ramfs, else ``None``.
    """
    if environment.get("filesystem") not in {"tmpfs", "ramfs"}:
        return None
    return (
        "warning: --out is on a memory-backed filesystem, so cold latency is "
        "not cold; choose a folder on disk\n"
    )


def _work_folder(out: Path) -> Path:
    """Return a fresh ``OUT/work``, removing only one this harness made."""
    if out.resolve().is_relative_to(REPO):
        raise SystemExit(
            f"--out {out} is inside the repository; choose a folder outside it"
        )
    work = out / "work"
    if work.exists():
        if not (work / _SENTINEL).exists():
            raise SystemExit(
                f"{work} exists and was not made by the envelope harness; "
                "choose another --out"
            )
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / _SENTINEL).touch()
    return work


def _spawn(
    phase: str, config: dict[str, Any], *, expect_kill: bool = False
) -> tuple[dict[str, Any], float]:
    """Run one phase in a fresh process; return its figures and wall seconds.

    Raises:
        SystemExit: If the phase fails, outlasts ``phase_timeout_s``, or,
            with *expect_kill*, ends other than by SIGKILL.
    """
    start = time.perf_counter()
    try:
        done = subprocess.run(  # fixed argv, no shell; see pyproject's S603 entry
            [
                sys.executable,
                "-m",
                "scripts.envelope.worker",
                phase,
                json.dumps(config),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=config["phase_timeout_s"],
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"phase {phase} did not finish within {config['phase_timeout_s']} s"
        ) from None
    seconds = time.perf_counter() - start
    if expect_kill and done.returncode == -signal.SIGKILL:
        return {}, seconds
    if expect_kill and done.returncode == 0:
        raise SystemExit(f"phase {phase} was to kill itself but finished")
    if done.returncode:
        tail = "\n".join(done.stderr.splitlines()[-20:])
        raise SystemExit(f"phase {phase} failed ({done.returncode}):\n{tail}")
    figures: dict[str, Any] = json.loads(done.stdout.splitlines()[-1])
    return figures, seconds


def _recovery(config: dict[str, Any]) -> dict[str, Any]:
    """Kill an edit run once ``kill_after`` edits finished, then restart.

    The finished edits were embedded before the kill, so a clean restart
    embeds only what the edit in progress left: one rewritten section.
    """
    kill_after = config["kill_after"]
    run = {
        **config,
        "first_edit": config["edits"],
        "edits": kill_after + 1,
        "kill_at": kill_after,
    }
    checkpoint = Path(config["work"]) / _CHECKPOINT
    checkpoint.unlink(missing_ok=True)  # the plain edit run left its count
    _spawn("edits", run, expect_kill=True)
    finished = int(checkpoint.read_text()) if checkpoint.exists() else 0
    after, wall = _spawn("restart", config)
    return {
        "finished_edits": finished,
        "texts_embedded": after["texts_embedded"],
        "texts_allowed": _TEXTS_PER_EDIT,
        "texts_beyond_allowed": max(0, after["texts_embedded"] - _TEXTS_PER_EDIT),
        "moved_aside": after["moved_aside"],
        "seconds": after["seconds"],
        "wall_seconds": wall,
    }


def measure(backend: str, config: dict[str, Any]) -> dict[str, Any]:
    """Run every phase for *backend*.

    Args:
        backend: The backend's registry name.
        config: The run's settings and its work folder.

    Returns:
        The backend's figures.
    """
    built, _ = _spawn("build", config)
    memory, _ = _spawn("memory", config)
    cold, eviction = {}, {}
    for kind in KINDS:
        figures, _ = _spawn("cold", {**config, "kind": kind})
        cold[kind] = figures["seconds"]
        eviction[kind] = figures["eviction"]
    restarted, wall = _spawn("restart", config)
    edits, _ = _spawn("edits", config)
    return {
        "backend": backend,
        "search": config["search"],
        "build": built,
        "memory": memory["memory"],
        "latency": {"warm": memory["warm"], "cold": cold, "eviction": eviction},
        "restart": {**restarted, "wall_seconds": wall},
        "edits": edits,
        "recovery": _recovery(config),
    }


def main(argv: list[str] | None = None) -> int:
    """Measure each backend and write the result.

    Args:
        argv: The command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        The exit status.

    Raises:
        SystemExit: Off Linux, over a work folder another tool made, or when
            a phase fails.
    """
    args = _parser().parse_args(argv)
    if not sys.platform.startswith("linux"):
        raise SystemExit("the envelope harness reads /proc and runs on Linux only")
    size = "custom" if args.notes else args.size
    notes = args.notes or synth.notes_for_chunks(SIZES[args.size])
    settings = {
        "dim": args.dim,
        "queries": args.queries,
        "discard": args.discard,
        "edits": args.edits,
        "kill_after": args.edits // 2 if args.kill_after is None else args.kill_after,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "phase_timeout_s": args.phase_timeout,
        "drain_timeout_s": args.drain_timeout,
    }
    if settings["kill_after"] >= args.edits:
        raise SystemExit("--kill-after must be below --edits")
    work = _work_folder(args.out)
    environment = report.environment(work)
    warning = cold_warning(environment)
    if warning:
        sys.stderr.write(warning)
    configs = []
    for backend in args.backends:
        folder = work / backend
        synth.generate_vault(folder / "vault", notes=notes, seed=args.seed)
        config = {**settings, "work": str(folder), "backend": backend}
        config["search"] = BACKENDS[backend]
        configs.append(measure(backend, config))
    result = {
        "size": size,
        "notes": configs[0]["build"]["notes"],
        "chunks": configs[0]["build"]["chunks"],
        "settings": settings,
        "environment": environment,
        "configs": configs,
    }
    path = report.write_json(result, args.out)
    sys.stdout.write(report.render_markdown(result) + "\n")
    sys.stderr.write(f"wrote {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
