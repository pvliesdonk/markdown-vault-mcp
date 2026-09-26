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


def _calls() -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"is_file", "is_dir", "exists"}
                    and not node.args
                ):
                    found.append((rel, func.name, node.lineno))
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
