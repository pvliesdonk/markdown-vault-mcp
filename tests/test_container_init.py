"""Contract test: the image runs an init as PID 1, not the server.

A process that detaches from its parent is re-parented to PID 1. The server
reaps only the children it waits on, so as PID 1 it leaves every such orphan
a zombie, and the zombies pile up until the container's pids limit stops
every fork (template #840). tini as PID 1 reaps them. The runtime proof, that
a running container really reaps an orphan, lives in the template's CI
container job, which needs a Docker daemon this lane does not have.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"


def _entrypoint() -> list[str]:
    """The exec-form argument list of the Dockerfile's last ``ENTRYPOINT``."""
    lines = DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()
    found = [line for line in lines if line.startswith("ENTRYPOINT ")]
    assert found, "the Dockerfile declares no ENTRYPOINT"
    try:
        argv = json.loads(found[-1].removeprefix("ENTRYPOINT "))
    except json.JSONDecodeError:
        argv = None
    assert isinstance(argv, list), f"ENTRYPOINT is not exec form: {found[-1]!r}"
    return [str(arg) for arg in argv]


def test_entrypoint_starts_with_tini() -> None:
    """tini, not the entrypoint script or the server, is PID 1."""
    argv = _entrypoint()
    assert argv[0] == "/usr/bin/tini", (
        f"ENTRYPOINT {argv!r} does not start with tini, so the server runs as "
        "PID 1 and nothing reaps the orphans its subprocesses leave"
    )
    assert "--" in argv, f"ENTRYPOINT {argv!r} lacks the `--` ending tini's flags"
    assert argv[-1] == "/usr/local/bin/docker-entrypoint.sh", (
        f"ENTRYPOINT {argv!r} no longer runs the entrypoint script under tini"
    )


def test_tini_reaps_when_not_pid_1() -> None:
    """``-s`` keeps tini reaping under ``docker run --init`` or ``init: true``."""
    argv = _entrypoint()
    assert "-s" in argv[: argv.index("--")], (
        f"ENTRYPOINT {argv!r} omits tini's -s, so under another init tini "
        "is no subreaper and warns on every start"
    )
