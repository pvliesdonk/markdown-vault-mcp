"""The PostgreSQL test harness and the ``postgres`` extra (#1504)."""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
_POSTGRES_PACKAGES = ("psycopg[binary]", "psycopg-pool", "pgvector", "numpy")


def _extras() -> dict[str, list[str]]:
    with _PYPROJECT.open("rb") as fh:
        extras: dict[str, list[str]] = tomllib.load(fh)["project"][
            "optional-dependencies"
        ]
    return extras


def _names(requirements: list[str]) -> set[str]:
    return {r.split(">")[0].split("<")[0].split("=")[0].strip() for r in requirements}


def test_postgres_extra_declares_the_client_libraries() -> None:
    assert _names(_extras()["postgres"]) == set(_POSTGRES_PACKAGES)


def test_all_extra_includes_the_postgres_client_libraries() -> None:
    assert set(_POSTGRES_PACKAGES) <= _names(_extras()["all"])
