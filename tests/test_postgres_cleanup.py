"""Conservative recovery of PostgreSQL containers after SIGKILL (#1952)."""

from __future__ import annotations

import errno
import inspect
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import mock_open

import pytest

from tests.fixtures import postgres as harness

if TYPE_CHECKING:
    from collections.abc import Callable

_PREFIX = harness._OWNER_LABEL
_CID = "a" * 64
_BOOT = "a4b40fc9-41e6-44e4-928b-edde03cba590"
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scope() -> dict[str, str]:
    """A complete ownership domain, independent of the test machine."""
    return {
        f"{_PREFIX}.{key}": value
        for key, value in {
            "version": "1",
            "boot": _BOOT,
            "host": "test-host",
            "pidns": "4:4026531836",
            "userns": "4:4026531837",
            "uid": "1000",
        }.items()
    }


class _Docker:
    def __init__(self, scope: dict[str, str]) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.outputs = {
            "ps": subprocess.CompletedProcess([], 0, _CID + "\n", ""),
            "inspect": subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    [
                        {
                            "Id": _CID,
                            "Config": {"Labels": {**scope, f"{_PREFIX}.pid": "123"}},
                        }
                    ]
                ),
                "",
            ),
            "rm": subprocess.CompletedProcess([], 0, _CID + "\n", ""),
        }
        self.failure: tuple[str, Exception] | None = None

    def __call__(self, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        assert 0 < timeout <= 60
        self.calls.append(args)
        if self.failure is not None and args[0] == self.failure[0]:
            raise self.failure[1]
        return self.outputs[args[0]]


def _kill_with(error: int | None) -> Callable[[int, int], None]:
    def kill(pid: int, signal: int) -> None:
        assert pid == 123 and signal == 0
        if error is not None:
            raise OSError(error, "probe")

    return kill


@pytest.mark.parametrize(
    ("name", "inode", "kind"),
    [
        ("pid", 0xEFFFFFFC, 0x20000000),
        ("user", 0xEFFFFFFD, 0x10000000),
    ],
)
@pytest.mark.parametrize("fault", [None, "type", "inode", "open", "ioctl", "stat"])
def test_initial_namespace_requires_genuine_initial_handle(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    inode: int,
    kind: int,
    fault: str | None,
) -> None:
    """A nested namespace or unavailable/non-namespace handle never qualifies."""
    fcntl = pytest.importorskip("fcntl")
    opened = mock_open()
    opened.return_value.fileno.return_value = 42
    if fault == "open":
        opened.side_effect = PermissionError
    monkeypatch.setattr(Path, "open", lambda path, *args: opened(path, *args))

    def ioctl(fd: int, operation: int) -> int:
        assert fd == 42 and operation == 0xB703
        if fault == "ioctl":
            raise OSError(errno.ENOTTY, "unsupported")
        return 0 if fault == "type" else kind

    real_stat = os.fstat

    def stat(fd: int) -> SimpleNamespace | os.stat_result:
        if fd != 42:
            return real_stat(fd)
        if fault == "stat":
            raise PermissionError
        return SimpleNamespace(st_dev=4, st_ino=inode + (fault == "inode"))

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr(os, "fstat", stat)
    if fault is not None:
        with pytest.raises((OSError, ValueError)):
            harness._initial_namespace(name, inode, kind)
    else:
        assert harness._initial_namespace(name, inode, kind) == f"4:{inode}"
    # Inherited ancestor proc mounts must not turn getpid() into a /proc path.
    opened.assert_called_once_with(Path(f"/proc/self/ns/{name}"), "rb")


@pytest.mark.parametrize(
    "fault",
    [None, "platform", "setuid", "namespace", "boot", "canonical", "host", "read"],
)
def test_owner_scope_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    fault: str | None,
) -> None:
    """Only a complete native-Linux identity can label a collectable container."""
    monkeypatch.setattr(sys, "platform", "darwin" if fault == "platform" else "linux")
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        os, "geteuid", lambda: 0 if fault == "setuid" else 1000, raising=False
    )
    monkeypatch.setattr(
        os,
        "uname",
        lambda: SimpleNamespace(nodename="" if fault == "host" else "test-host"),
        raising=False,
    )

    def namespace(name: str, inode: int, kind: int) -> str:
        assert (name, inode, kind) in [
            ("pid", 0xEFFFFFFC, 0x20000000),
            ("user", 0xEFFFFFFD, 0x10000000),
        ]
        if fault == "namespace":
            raise ValueError("nested")
        return f"4:{inode}"

    def read(path: Path) -> str:
        assert path == Path("/proc/sys/kernel/random/boot_id")
        if fault == "read":
            raise PermissionError
        return (
            "invalid"
            if fault == "boot"
            else _BOOT.upper()
            if fault == "canonical"
            else _BOOT + "\n"
        )

    monkeypatch.setattr(harness, "_initial_namespace", namespace)
    monkeypatch.setattr(Path, "read_text", read)
    assert harness._owner_scope() == ({} if fault else scope)


@pytest.mark.parametrize(
    "error", [None, errno.ESRCH, errno.EPERM, errno.EACCES, errno.EINVAL]
)
def test_only_esrch_reclaims_exact_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scope: dict[str, str],
    error: int | None,
) -> None:
    """Live/reused PIDs and every ambiguous failure preserve the container."""
    docker = _Docker(scope)
    monkeypatch.setattr(harness, "_docker", docker)
    monkeypatch.setattr(os, "kill", _kill_with(error))
    with caplog.at_level("INFO", logger=harness.__name__):
        harness._collect_orphans(scope)
    assert (
        ("rm", "-f", _CID) in docker.calls
        if error == errno.ESRCH
        else len(docker.calls) == 2
    )
    assert ("postgres_orphan_removed" in caplog.text) == (error == errno.ESRCH)
    assert docker.calls[0] == (
        "ps",
        "--all",
        "--quiet",
        "--no-trunc",
        *(
            arg
            for key, value in scope.items()
            for arg in ("--filter", f"label={key}={value}")
        ),
    )


@pytest.mark.parametrize("key", ["version", "boot", "host", "pidns", "userns", "uid"])
@pytest.mark.parametrize("value", [None, "foreign-or-future"])
def test_foreign_incomplete_and_future_labels_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    key: str,
    value: str | None,
) -> None:
    """Every field is checked, regardless of the daemon's discovery filter."""
    labels = {**scope, f"{_PREFIX}.pid": "123"}
    if value is None:
        labels.pop(f"{_PREFIX}.{key}")
    else:
        labels[f"{_PREFIX}.{key}"] = value
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("foreign PID probed"))
    assert not harness._owner_is_gone(labels, scope)


@pytest.mark.parametrize(
    "pid",
    [
        None,
        123,
        "",
        "0",
        "-1",
        "+1",
        " 123",
        "123\n",
        "0123",
        "١٢٣",
        "2147483648",
        "9" * 100,
    ],
)
def test_malformed_pids_are_not_probed(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    pid: object,
) -> None:
    """Only a positive ASCII process ID is eligible for signal zero."""
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("invalid PID probed"))
    assert not harness._owner_is_gone({**scope, f"{_PREFIX}.pid": pid}, scope)


@pytest.mark.parametrize(
    "record",
    [
        None,
        {},
        [],
        [None],
        [{}, {}],
        [{"Id": "b" * 64}],
        [{"Id": _CID, "Config": None}],
        [{"Id": _CID, "Config": {"Labels": None}}],
        [{"Id": _CID, "Config": {"Labels": {}}}],
        [{"Id": _CID, "Config": {"Labels": []}}],
    ],
)
def test_malformed_inspection_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    record: object,
) -> None:
    """No unexpected inspection shape is destructive."""
    docker = _Docker(scope)
    docker.outputs["inspect"].stdout = json.dumps(record)
    monkeypatch.setattr(harness, "_docker", docker)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("invalid metadata probed"))
    harness._collect_orphans(scope)
    assert len(docker.calls) == 2


@pytest.mark.parametrize(
    ("command", "failure"),
    [
        (command, failure)
        for command in ["ps", "inspect", "rm"]
        for failure in ["exit", "oserror", "timeout", "bad-output"]
        if (command, failure) != ("rm", "bad-output")
    ],
)
def test_docker_failures_never_report_a_removal(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scope: dict[str, str],
    command: str,
    failure: str,
) -> None:
    """Failures and another collector winning the removal race are harmless."""
    docker = _Docker(scope)
    if failure == "exit":
        docker.outputs[command].returncode = 1
    elif failure == "bad-output":
        docker.outputs[command].stdout = "not-json-or-a-full-id"
    else:
        docker.failure = (
            command,
            OSError("unavailable")
            if failure == "oserror"
            else subprocess.TimeoutExpired("docker", 30),
        )
    monkeypatch.setattr(harness, "_docker", docker)
    monkeypatch.setattr(os, "kill", _kill_with(errno.ESRCH))
    with caplog.at_level("INFO", logger=harness.__name__):
        harness._collect_orphans(scope)
    assert "postgres_orphan_removed" not in caplog.text
    assert len(docker.calls) <= {"ps": 1, "inspect": 2, "rm": 3}[command]


@pytest.mark.parametrize(
    "identity",
    [
        "",
        "short-id",
        "mvm-test-pg-123-abcdef",
        "A" * 64,
        "a" * 63,
        "a" * 65,
        _CID + " ",
    ],
)
def test_discovery_requires_full_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    identity: str,
) -> None:
    """Names and partial IDs never reach inspection or removal."""
    docker = _Docker(scope)
    docker.outputs["ps"].stdout = identity
    monkeypatch.setattr(harness, "_docker", docker)
    harness._collect_orphans(scope)
    assert len(docker.calls) == 1


def test_unknown_scope_does_not_touch_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsupported scope cannot inspect or reclaim anything."""
    monkeypatch.setattr(
        harness, "_docker", lambda *_args, **_kwargs: pytest.fail("Docker probed")
    )
    harness._collect_orphans({})
    assert not harness._owner_is_gone({f"{_PREFIX}.pid": "123"}, {})


@pytest.mark.parametrize("supported", [True, False])
def test_start_labels_current_creator_before_docker_run(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    supported: bool,
) -> None:
    """Each invocation overwrites inherited ownership, even when unsupported."""
    events: list[object] = []
    monkeypatch.setattr(harness, "_owner_scope", lambda: scope if supported else {})
    monkeypatch.setattr(harness, "_collect_orphans", lambda owner: events.append(owner))
    monkeypatch.setattr(harness, "_await_container", lambda *_args: "dsn")
    pid = iter(
        [123, 123, 456, 456] if supported else [123, 456]
    )  # label, name, then the next invocation
    monkeypatch.setattr(os, "getpid", lambda: next(pid))

    def docker(*args: str) -> subprocess.CompletedProcess[str]:
        events.append(args)
        return subprocess.CompletedProcess([], 0, _CID, "")

    monkeypatch.setattr(harness, "_docker", docker)
    for creator in [123, 456]:
        # Unsupported mode only asks for the name's PID.
        assert harness.start_container("image") == (_CID, "dsn")
        assert events[-2] == (scope if supported else {})
        command = events[-1]
        assert isinstance(command, tuple)
        labels = dict(
            command[i + 1].split("=", 1)
            for i, arg in enumerate(command)
            if arg == "--label"
        )
        assert labels == (
            {**scope, f"{_PREFIX}.pid": str(creator)}
            if supported
            else {f"{_PREFIX}.version": "unsupported"}
        )


@pytest.mark.parametrize("exhausted_after", ["ps", "inspect"])
def test_sweep_budget_stops_further_docker_calls(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    exhausted_after: str,
) -> None:
    """Many candidates cannot multiply the startup timeout."""
    docker = _Docker(scope)
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(os, "kill", _kill_with(errno.ESRCH))

    def run(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        result = docker(*args, timeout=timeout)
        if args[0] == exhausted_after:
            clock[0] = harness._CLEANUP_TIMEOUT_S
        return result

    monkeypatch.setattr(harness, "_docker", run)
    harness._collect_orphans(scope)
    assert [command[0] for command in docker.calls] == (
        ["ps"] if exhausted_after == "ps" else ["ps", "inspect"]
    )


@pytest.mark.skipif(sys.platform != "linux", reason="signal-zero probe requires Linux")
def test_real_process_liveness_is_conservative(scope: dict[str, str]) -> None:
    """Signal zero preserves a live subprocess and recognizes its reaped exit."""
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
    ) as child:
        labels = {**scope, f"{_PREFIX}.pid": str(child.pid)}
        try:
            assert not harness._owner_is_gone(labels, scope)
            child.communicate(b"x", timeout=10)
            assert harness._owner_is_gone(labels, scope)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)


_CHILD = """
import os
import time
from pathlib import Path
from tests.fixtures import postgres as harness
harness._OWNER_LABEL = os.environ['MVM_CLEANUP_LABEL_PREFIX']
original = harness._docker
report = Path(os.environ['MVM_CLEANUP_ID_FILE'])
def record(*args, **kwargs):
    if args[0] == 'run':
        args = (args[0], '--cidfile', str(report.with_suffix('.cid')), *args[1:])
    return original(*args, **kwargs)
harness._docker = record
container, _ = harness.start_container(os.environ['MVM_TEST_POSTGRES_IMAGE'])
Path(os.environ['MVM_CLEANUP_ID_FILE']).write_text(container)
while True:
    time.sleep(1)
"""


def _container_exists(container: str) -> bool:
    return harness._docker("inspect", container, timeout=30).returncode == 0


def _wait_for_child(child: subprocess.Popen[bytes], path: Path) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if path.exists():
            container = path.read_text()
            if harness._FULL_CONTAINER_ID.fullmatch(container):
                return container
        assert child.poll() is None, (
            "fixture child exited before reporting its container"
        )
        time.sleep(0.1)
    pytest.fail("fixture child did not report its container within 120 seconds")


@pytest.fixture
def isolated_docker_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Use Docker only in an isolated test-only ownership namespace."""
    if os.environ.get(harness.DSN_ENV, "").strip():
        pytest.skip("uses disposable Docker containers, never an external DSN")
    if not harness._driver_installed():
        harness.report_unavailable(
            "orphan regression requires the postgres extra", os.environ
        )
    if not harness.docker_available():
        harness.report_unavailable("orphan regression requires Docker", os.environ)
    # Isolate the real sweep from every existing run on a shared Docker daemon.
    prefix = f"{_PREFIX}.regression-{uuid.uuid4().hex}"
    monkeypatch.setattr(harness, "_OWNER_LABEL", prefix)
    scope = harness._owner_scope()
    if not scope:
        harness.report_unavailable(
            "orphan regression requires initial Linux PID/user namespaces", os.environ
        )
    return scope


@pytest.mark.parametrize("ci", [False, True])
def test_docker_regression_requires_the_optional_driver(
    monkeypatch: pytest.MonkeyPatch,
    ci: bool,
) -> None:
    """A checkout without psycopg skips locally, fails in CI, and never starts Docker."""
    monkeypatch.delenv(harness.DSN_ENV, raising=False)
    monkeypatch.setenv("CI", str(ci))
    monkeypatch.setattr(harness, "_driver_installed", lambda: False)
    monkeypatch.setattr(
        harness, "docker_available", lambda: pytest.fail("Docker probed")
    )
    outcome = pytest.fail.Exception if ci else pytest.skip.Exception
    with pytest.raises(outcome, match="postgres extra"):
        inspect.unwrap(isolated_docker_scope)(monkeypatch)


def test_sigkill_orphan_reclaimed_without_touching_other_containers(
    tmp_path: Path,
    isolated_docker_scope: dict[str, str],
) -> None:
    """A real killed fixture is recovered; live, foreign and legacy IDs survive."""
    scope = isolated_docker_scope
    prefix = harness._OWNER_LABEL
    image = os.environ.get(harness.IMAGE_ENV, "").strip() or harness.DEFAULT_IMAGE
    owned: set[str] = set()
    children: list[subprocess.Popen[bytes]] = []
    reports: list[Path] = []
    try:
        for index in range(2):
            path = tmp_path / f"child-{index}.id"
            reports.append(path)
            env = {
                **os.environ,
                harness.IMAGE_ENV: image,
                "MVM_CLEANUP_ID_FILE": str(path),
                "MVM_CLEANUP_LABEL_PREFIX": prefix,
                "PYTHONPATH": os.pathsep.join(
                    [str(_ROOT), str(_ROOT / "src"), os.environ.get("PYTHONPATH", "")]
                ),
            }
            child = subprocess.Popen([sys.executable, "-c", _CHILD], cwd=_ROOT, env=env)
            children.append(child)
            owned.add(_wait_for_child(child, path))
        orphan, live = (path.read_text() for path in reports)
        children[0].kill()
        children[0].wait(timeout=10)
        assert _container_exists(orphan), "SIGKILL must reproduce the original leak"
        sentinels: list[str] = []
        for labels in [
            {
                **scope,
                f"{prefix}.host": scope[f"{prefix}.host"] + "-foreign",
                f"{prefix}.pid": str(children[0].pid),
            },
            {},
        ]:
            args = [
                arg
                for key, value in labels.items()
                for arg in ("--label", f"{key}={value}")
            ]
            created = harness._docker(
                "create", *args, image, "sleep", "300", timeout=60
            )
            assert created.returncode == 0, created.stderr
            container = created.stdout.strip()
            assert harness._FULL_CONTAINER_ID.fullmatch(container)
            owned.add(container)
            sentinels.append(container)
        new, _ = harness.start_container(image)
        owned.add(new)
        assert not _container_exists(orphan)
        assert children[1].poll() is None and _container_exists(live)
        assert all(_container_exists(container) for container in sentinels)
        assert _container_exists(new)
        harness._collect_orphans(scope)  # repeated recovery preserves every survivor
        assert all(
            _container_exists(container) for container in [live, new, *sentinels]
        )
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        for path in [
            candidate
            for report in reports
            for candidate in (report, report.with_suffix(".cid"))
        ]:
            if path.exists() and harness._FULL_CONTAINER_ID.fullmatch(
                path.read_text().strip()
            ):
                owned.add(path.read_text().strip())
        for container in owned:
            harness._remove(container)
