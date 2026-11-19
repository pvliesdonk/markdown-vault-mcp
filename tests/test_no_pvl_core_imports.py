"""Nothing in this project uses ``fastmcp_pvl_core`` any more (#2283).

#2241 folded pvl-core in area by area through one import shim (#2274); with the
last area moved, the shim and the dependency are gone. This guard fails on any
import of ``fastmcp_pvl_core``, and on a ``monkeypatch``/``patch`` target that
names it, anywhere in ``src``, ``tests`` or ``scripts``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = "fastmcp_pvl_core"

_PATCHERS = {"setattr", "patch", "delattr", "setitem"}


def _is_core(name: str) -> bool:
    return name == CORE or name.startswith(CORE + ".")


def _direct_uses(tree: ast.AST) -> list[int]:
    """Line numbers that import pvl-core or patch one of its names by string."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom) and node.module and _is_core(node.module)
        ) or (
            isinstance(node, ast.Import) and any(_is_core(a.name) for a in node.names)
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            first = node.args[0]
            if (
                name in _PATCHERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _is_core(first.value)
            ):
                lines.append(node.lineno)
    return lines


def _python_files() -> list[Path]:
    roots = ("src", "tests", "scripts")
    return sorted(p for root in roots for p in (REPO_ROOT / root).rglob("*.py"))


def test_nothing_uses_pvl_core() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in _python_files()
        if path.resolve() != Path(__file__).resolve()
        for line in _direct_uses(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, (
        "fastmcp-pvl-core was folded into this project (#2241); import the "
        "local module instead: " + ", ".join(offenders)
    )


def test_the_shim_is_gone() -> None:
    assert not (REPO_ROOT / "src" / "markdown_vault_mcp" / "_pvl_core.py").exists()


def test_the_check_sees_each_form() -> None:
    source = (
        "from fastmcp_pvl_core import env\n"
        "import fastmcp_pvl_core\n"
        "from fastmcp_pvl_core._middleware import X\n"
        "monkeypatch.setattr('fastmcp_pvl_core.get_subject', f)\n"
        "patch('fastmcp_pvl_core._kv_store.D', 1)\n"
    )
    assert _direct_uses(ast.parse(source)) == [1, 2, 3, 4, 5]
