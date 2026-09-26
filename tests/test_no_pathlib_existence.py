"""Existence is decided with stat, not pathlib's predicates (#1625).

On Python 3.14 ``Path.is_file()`` / ``is_dir()`` / ``exists()`` answer
``False`` for a path the process cannot stat, so a permission problem reads as
"not there". ``markdown_vault_mcp.utils.fs`` decides existence instead; this
guard keeps new calls from creeping back in. Each allowed site is a
best-effort probe whose reason is written at the call.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "markdown_vault_mcp"

# (file relative to the package, function) -> why "not there" is acceptable.
_ALLOWED = {
    (
        "_file_watcher.py",
        "_derive_watch_roots",
    ): "an unwatched root is logged by the walk",
    (
        "git/_run.py",
        "_find_git_root",
    ): "falls back to the parent; git reports its own error",
}


_PREDICATES = {"is_file", "is_dir", "exists"}
_OS_PATH = {"exists", "isfile", "isdir"}


def _is_existence_call(node: ast.Call) -> bool:
    """``p.is_file()``, ``Path.exists(p)`` or ``os.path.isfile(p)``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _PREDICATES and (
        not node.args or (isinstance(func.value, ast.Name) and func.value.id == "Path")
    ):
        return True
    owner = func.value
    return (
        func.attr in _OS_PATH
        and isinstance(owner, ast.Attribute)
        and owner.attr == "path"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "os"
    )


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """The name of the function a node sits in, or ``<module>``."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return "<module>"


def _calls() -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        found.extend(
            (rel, _enclosing(node, parents), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_existence_call(node)
        )
    return found


def test_no_pathlib_existence_predicates_outside_the_allowlist() -> None:
    offenders = [
        f"{rel}:{line} in {name}()"
        for rel, name, line in _calls()
        if (rel, name) not in _ALLOWED
    ]
    assert not offenders, (
        "decide existence with markdown_vault_mcp.utils.fs "
        f"(is_regular_file / is_directory / path_exists): {offenders}"
    )


def test_every_allowlisted_site_still_exists() -> None:
    """A stale allowlist entry would quietly permit a new call there."""
    present = {(rel, name) for rel, name, _ in _calls()}
    assert set(_ALLOWED) <= present


def test_the_guard_recognises_every_form() -> None:
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "X = Path('a').exists()\n"
        "def f(p):\n"
        "    return p.is_file() or Path.is_dir(p) or os.path.isfile(p)\n"
        "def g(d):\n"
        "    return d.get('k') and os.path.join('a', 'b')\n"
    )
    tree = ast.parse(source)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    hits = sorted(
        (_enclosing(n, parents), n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _is_existence_call(n)
    )
    assert hits == [("<module>", 3), ("f", 5), ("f", 5), ("f", 5)]
