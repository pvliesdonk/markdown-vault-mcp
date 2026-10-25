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
from scripts.envelope.worker import KINDS, PHASES

REPO = Path(__file__).resolve().parents[2]
SIZES = {"deployed": 22_764, "growth": 227_640}
"""Target chunk counts: the deployed vault (#1501) and ten times that."""

BACKENDS = {"numpy": "resident", "sqlite": "streamed"}
"""Each backend measured, with the search mode it runs in."""

_SENTINEL = ".envelope-work"
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
    if phase not in PHASES:
        raise ValueError(f"no worker phase {phase!r}")
    start = time.perf_counter()
    try:
        done = subprocess.run(  # fixed argv, no shell; see pyproject's S603 entry
            [sys.executable, "-m", "scripts.envelope.worker", phase],
            input=json.dumps(config),  # on stdin: kept out of argv and ps
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
    lines = done.stdout.splitlines()
    if not lines:
        raise SystemExit(f"phase {phase} exited cleanly but printed no figures")
    figures: dict[str, Any] = json.loads(lines[-1])
    return figures, seconds


def _recovery(config: dict[str, Any], *, texts_per_edit: int) -> dict[str, Any]:
    """Kill an edit run once ``kill_after`` edits finished, then restart.

    The finished edits were embedded before the kill, so a clean restart
    embeds only what the edit in progress left: what one edit was measured
    to embed, *texts_per_edit*.

    Raises:
        SystemExit: If the run died after another number of edits, such as
            from the OOM killer, so the kill was not the harness's.
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
    if finished != kill_after:
        raise SystemExit(
            f"the edit run was killed after {finished} edits, not {kill_after}: "
            "a kill the harness did not make (out of memory?)"
        )
    after, wall = _spawn("restart", config)
    return {
        "finished_edits": finished,
        "texts_embedded": after["texts_embedded"],
        "texts_allowed": texts_per_edit,
        "texts_beyond_allowed": max(0, after["texts_embedded"] - texts_per_edit),
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
        The backend's figures, also kept in ``partial.json`` in the backend's
        work folder as each phase finishes.
    """
    partial = Path(config["work"]) / "partial.json"
    figures: dict[str, Any] = {"backend": backend, "search": config["search"]}

    def keep(key: str, value: Any) -> None:
        # A later phase can fail after hours of work; what is done stays.
        figures[key] = value
        partial.write_text(json.dumps(figures, indent=2) + "\n")

    keep("build", _spawn("build", config)[0])
    memory, _ = _spawn("memory", config)
    keep("memory", memory["memory"])
    cold, eviction = {}, {}
    for kind in KINDS:
        timed, _ = _spawn("cold", {**config, "kind": kind})
        cold[kind] = timed["seconds"]
        eviction[kind] = timed["eviction"]
    keep("latency", {"warm": memory["warm"], "cold": cold, "eviction": eviction})
    restarted, wall = _spawn("restart", config)
    keep("restart", {**restarted, "wall_seconds": wall})
    edits, _ = _spawn("edits", config)
    keep("edits", edits)
    texts_per_edit = int(edits["texts_embedded"]["max"])
    keep("recovery", _recovery(config, texts_per_edit=texts_per_edit))
    return figures


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
