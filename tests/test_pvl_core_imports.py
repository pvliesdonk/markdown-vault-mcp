"""Every use of pvl-core goes through ``markdown_vault_mcp._pvl_core`` (#2274).

#2241 dissolves pvl-core one area at a time. Routing every import, and every
``monkeypatch``/``patch`` target, through one module means each area changes
that module and its own call sites, and a new direct import cannot slip in
while the dissolution is under way.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIM = REPO_ROOT / "src" / "markdown_vault_mcp" / "_pvl_core.py"
CORE = "fastmcp_pvl_core"

#: Files allowed to import pvl-core directly, with the reason.
ALLOWED = {
    SHIM: "the shim itself",
    # Its config-surface introspection still comes from pvl-core until #2289.
    REPO_ROOT / "scripts" / "gen_config_surface.py": "introspection until #2289",
}

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


def test_only_the_shim_uses_pvl_core() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in _python_files()
        if path not in ALLOWED
        for line in _direct_uses(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, (
        "import pvl-core names from markdown_vault_mcp._pvl_core, and patch "
        "'markdown_vault_mcp._pvl_core.<name>', not fastmcp_pvl_core (#2274): "
        + ", ".join(offenders)
    )


def test_the_check_sees_each_form() -> None:
    source = (
        "from fastmcp_pvl_core import env\n"
        "import fastmcp_pvl_core\n"
        "from fastmcp_pvl_core._middleware import X\n"
        "monkeypatch.setattr('fastmcp_pvl_core.get_subject', f)\n"
        "patch('fastmcp_pvl_core._kv_store.D', 1)\n"
        "from markdown_vault_mcp._pvl_core import env\n"
        "monkeypatch.setattr('markdown_vault_mcp._pvl_core.get_subject', f)\n"
    )
    assert _direct_uses(ast.parse(source)) == [1, 2, 3, 4, 5]
