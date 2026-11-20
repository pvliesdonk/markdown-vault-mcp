"""Static typing of the env readers' ``required`` overloads, checked with mypy.

Ported from fastmcp_pvl_core 10.2.0 with the helpers (#2275).

The overloads are the contract downstream configs type-check against under
``mypy --strict``: ``required=True`` narrows to a non-optional value, a
non-literal ``required`` flag stays optional, and a default combined with a
flag that may be ``True`` (a runtime ``TypeError``) is rejected statically.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

mypy_api = pytest.importorskip("mypy.api")

_ACCEPTED = """
from markdown_vault_mcp._env import env, env_float, env_int

def check(flag: bool) -> None:
    reveal_type(env("P", "X", required=True))
    reveal_type(env("P", "X", None, required=True))
    reveal_type(env("P", "X", required=False))
    reveal_type(env("P", "X", "d", required=False))
    reveal_type(env("P", "X", required=flag))
    reveal_type(env_int("P", "X", required=True, minimum=1))
    reveal_type(env_int("P", "X", 3, required=False))
    reveal_type(env_int("P", "X", required=flag))
    reveal_type(env_float("P", "X", required=True))
    reveal_type(env_float("P", "X", required=flag, strict=True))
"""

_EXPECTED_TYPES = [
    "str",
    "str",
    "str | None",
    "str",
    "str | None",
    "int",
    "int",
    "int | None",
    "float",
    "float | None",
]

_REJECTED = """
from markdown_vault_mcp._env import env, env_int

def check(flag: bool) -> None:
    env("P", "X", "d", required=True)
    env("P", "X", "d", required=flag)
    env_int("P", "X", 3, required=flag)
"""


def _mypy(tmp_path: Path, source: str) -> tuple[str, int]:
    probe = tmp_path / "probe.py"
    probe.write_text(textwrap.dedent(source), encoding="utf-8")
    stdout, _stderr, status = mypy_api.run(
        ["--strict", "--no-error-summary", "--hide-error-context", str(probe)]
    )
    return stdout, status


def test_required_overloads_narrow_as_documented(tmp_path: Path) -> None:
    stdout, _status = _mypy(tmp_path, _ACCEPTED)
    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert not errors, "\n".join(errors)
    revealed = [
        # mypy versions differ on printing ``builtins.``; compare the names.
        line.split('Revealed type is "', 1)[1].rstrip('"').replace("builtins.", "")
        for line in stdout.splitlines()
        if "Revealed type is" in line
    ]
    assert revealed == _EXPECTED_TYPES


def test_a_default_with_a_possibly_true_required_is_rejected(tmp_path: Path) -> None:
    stdout, status = _mypy(tmp_path, _REJECTED)
    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert status != 0
    assert len(errors) == 3, stdout
    assert all("No overload variant" in line for line in errors), stdout
