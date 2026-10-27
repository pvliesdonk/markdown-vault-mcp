"""The supported PostgreSQL range is the one the range workflow runs (#1504)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from markdown_vault_mcp.stores._postgres import MIN_PGVECTOR
from tests.fixtures.postgres import OLD_PGVECTOR_ENV

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "postgres-range.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GUIDE = ROOT / "docs" / "use" / "postgresql.md"
PYPROJECT = ROOT / "pyproject.toml"
_PGVECTOR = re.compile(r"^pgvector/pgvector:(\d+)\.(\d+)\.(\d+)-pg(\d+)$")
_VCHORD = re.compile(r"^tensorchord/vchord-suite:pg(\d+)-\d{8}$")


def _workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def _images() -> list[str]:
    return list(_workflow()["jobs"]["range"]["strategy"]["matrix"]["image"])


def _major(image: str) -> int:
    match = _PGVECTOR.match(image) or _VCHORD.match(image)
    assert match is not None, image
    return int(match.groups()[-1])


def test_every_image_is_pinned_to_an_exact_tag() -> None:
    loose = [i for i in _images() if not (_PGVECTOR.match(i) or _VCHORD.match(i))]
    assert loose == []


def test_the_lowest_pgvector_tested_is_the_minimum_the_store_accepts() -> None:
    versions = [
        (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        for m in map(_PGVECTOR.match, _images())
        if m is not None
    ]
    assert min(versions) == MIN_PGVECTOR


def test_the_guide_states_the_postgresql_majors_the_workflow_runs() -> None:
    majors = sorted({_major(i) for i in _images()})
    floor = ".".join(map(str, MIN_PGVECTOR))
    sentence = f"PostgreSQL {majors[0]} to {majors[-1]} with pgvector {floor} or later"
    assert sentence in GUIDE.read_text(encoding="utf-8")


def test_the_guide_names_each_major_whose_lowest_pgvector_is_higher() -> None:
    lowest: dict[int, tuple[int, int, int]] = {}
    for match in filter(None, map(_PGVECTOR.match, _images())):
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        major = int(match.group(4))
        lowest[major] = min(lowest.get(major, version), version)
    guide = GUIDE.read_text(encoding="utf-8")
    missing = [
        f"{major} with pgvector {'.'.join(map(str, floor))} or later"
        for major, floor in sorted(lowest.items())
        if floor > MIN_PGVECTOR
        and f"{major} with pgvector {'.'.join(map(str, floor))} or later" not in guide
    ]
    assert missing == []


def test_the_tested_majors_have_no_gap() -> None:
    majors = sorted({_major(i) for i in _images()})
    assert majors == list(range(majors[0], majors[-1] + 1))


@pytest.mark.parametrize("job", ["range", "floors", "refusal"])
def test_the_jobs_fail_rather_than_skip_and_never_name_a_database(job: str) -> None:
    env = {k: str(v) for k, v in _workflow()["jobs"][job]["env"].items()}
    assert (env["CI"], env["MVM_TEST_POSTGRES_IMAGE"]) == (
        "true",
        "${{ matrix.image }}",
    )
    assert "MVM_TEST_POSTGRES_DSN" not in WORKFLOW.read_text(encoding="utf-8")


def _job_images(job: str) -> list[str]:
    return list(_workflow()["jobs"][job]["strategy"]["matrix"]["image"])


def _step_command(job: str, name: str) -> str:
    steps = _workflow()["jobs"][job]["steps"]
    return str(next(step["run"] for step in steps if step.get("name") == name))


def _release(text: str) -> tuple[int, ...]:
    """A release number with trailing zeros dropped, so ``3.3`` equals ``3.3.0``."""
    parts = [int(part) for part in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _floors(extra_name: str) -> dict[str, tuple[int, ...]]:
    extra = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ][extra_name]
    floors: dict[str, tuple[int, ...]] = {}
    for requirement in extra:
        match = re.fullmatch(r"([a-z-]+)(\[[a-z]+\])?>=([\d.]+)", requirement)
        if match is not None:
            floors[match.group(1) + (match.group(2) or "")] = _release(match.group(3))
    return floors


def _postgres_floors() -> dict[str, tuple[int, ...]]:
    return _floors("postgres")


def _pins(text: str) -> dict[str, tuple[int, ...]]:
    found = re.findall(r'"([a-z-]+(?:\[[a-z]+\])?)==([\d.]+)"', text)
    return {name: _release(version) for name, version in found}


def test_the_floors_job_installs_exactly_the_postgres_extras_floors() -> None:
    floors = _postgres_floors()
    assert set(floors) == {"psycopg[binary]", "psycopg-pool", "pgvector"}
    assert _pins(_step_command("floors", "Install the driver floors")) == floors


def test_the_all_extra_carries_the_postgres_extras_floors() -> None:
    every = _floors("all")
    assert {name: every.get(name) for name in _postgres_floors()} == _postgres_floors()


def test_the_floors_job_checks_what_it_installed_before_the_tests_run() -> None:
    names = [step.get("name") for step in _workflow()["jobs"]["floors"]["steps"]]
    install, check_at, run_at = (
        names.index(name)
        for name in (
            "Install the driver floors",
            "Check the installed drivers",
            "Run the PostgreSQL tests",
        )
    )
    assert install < check_at < run_at
    check = _step_command("floors", "Check the installed drivers")
    wanted = re.search(r"wanted = (\{.*\})", check)
    assert wanted is not None
    expected = {
        name.split("[")[0]: version for name, version in _postgres_floors().items()
    }
    pins = re.findall(r'"([a-z-]+)": "([\d.]+)"', wanted.group(1))
    assert {name: _release(version) for name, version in pins} == expected
    assert "uv run --no-sync" in check
    assert "uv run --no-sync" in _step_command("floors", "Run the PostgreSQL tests")


def test_the_floors_job_runs_on_the_lowest_supported_python() -> None:
    requires = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    steps = _workflow()["jobs"]["floors"]["steps"]
    setup = next(
        s for s in steps if s.get("uses") == "./.github/actions/setup-project-env"
    )
    assert requires == f">={setup['with']['python-version']}"


def test_the_floors_job_runs_the_lowest_and_newest_range_images() -> None:
    def key(image: str) -> tuple[int, ...]:
        match = _PGVECTOR.match(image)
        assert match is not None, image
        return (int(match.group(4)), *map(int, match.groups()[:3]))

    pgvector = sorted((i for i in _images() if _PGVECTOR.match(i)), key=key)
    by_floor = sorted(pgvector, key=lambda i: (key(i)[1:], key(i)[0]))
    assert _job_images("floors") == [by_floor[0], pgvector[-1]]


def test_the_refusal_job_runs_on_a_pgvector_below_the_minimum() -> None:
    images = _job_images("refusal")
    assert images
    for image in images:
        match = _PGVECTOR.match(image)
        assert match is not None, image
        assert tuple(map(int, match.groups()[:3])) < MIN_PGVECTOR
    assert str(_workflow()["jobs"]["refusal"]["env"][OLD_PGVECTOR_ENV]) == "true"
    run = _step_command("refusal", "Run the refusal test")
    assert "test_a_real_pgvector_below_the_minimum_is_refused_at_open" in run
    # A skip exits 0, so the job requires the one test to have passed.
    assert 'grep -q "1 passed"' in run


def test_the_workflow_runs_when_the_store_or_its_tests_change() -> None:
    on = _workflow()[True]  # YAML 1.1 reads the bare key `on` as True
    wanted = {
        "src/markdown_vault_mcp/stores/**",
        "tests/fixtures/postgres.py",
        "tests/conftest.py",
        "tests/*postgres*.py",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/postgres-range.yml",
    }
    assert wanted <= set(on["pull_request"]["paths"])
    assert wanted <= set(on["push"]["paths"])
    assert {"schedule", "workflow_dispatch"} <= set(on)


def test_runs_of_different_events_do_not_cancel_each_other() -> None:
    group = _workflow()["concurrency"]["group"]
    assert "github.event_name" in group


def test_checkout_is_pinned_as_ci_pins_it() -> None:
    pin = re.compile(r"uses: actions/checkout@[0-9a-f]{40}  # v\d+")
    ours = set(pin.findall(WORKFLOW.read_text(encoding="utf-8")))
    assert ours
    assert ours <= set(pin.findall(CI_WORKFLOW.read_text(encoding="utf-8")))
