"""The supported PostgreSQL range is the one the range workflow runs (#1504)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from markdown_vault_mcp.stores._postgres import MIN_PGVECTOR

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "postgres-range.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GUIDE = ROOT / "docs" / "use" / "postgresql.md"
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


def test_the_jobs_fail_rather_than_skip_and_never_name_a_database() -> None:
    env = {k: str(v) for k, v in _workflow()["jobs"]["range"]["env"].items()}
    assert (env["CI"], env["MVM_TEST_POSTGRES_IMAGE"]) == (
        "true",
        "${{ matrix.image }}",
    )
    assert "MVM_TEST_POSTGRES_DSN" not in WORKFLOW.read_text(encoding="utf-8")


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
