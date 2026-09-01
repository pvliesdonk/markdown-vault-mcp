"""The module map in `docs/design/module-map.md` covers `src/` exactly.

The map used to live inline in `AGENTS.md`, where every session loaded it
(#1146). Out of the always-loaded file it is cheaper but also out of sight,
so this guard replaces the attention that kept it honest: a new module cannot
land unlisted, and a deleted one cannot linger as a phantom entry.

Only the *paths* are checked. The one-line annotations beside them are the
map's actual value and no test can judge them — keeping one true is part of
the change that moves the code it describes.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = REPO_ROOT / "docs" / "design" / "module-map.md"
PACKAGE_ROOT = REPO_ROOT / "src" / "markdown_vault_mcp"

# A map line is `<indent><name>` with an optional ` -- <annotation>` tail;
# `<name>` is either a `.py` file or a directory written with a trailing `/`.
_ENTRY = re.compile(
    r"^(?P<indent> *)(?P<name>[A-Za-z_0-9]+\.py|[A-Za-z_0-9]+/)(?: +--.*)?$"
)


def _fenced_tree(text: str) -> list[str]:
    """Return the lines of the map's single fenced code block."""
    blocks = re.findall(r"^```\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, (
        f"{MAP_PATH.name} must hold exactly one fenced tree; found {len(blocks)}"
    )
    return blocks[0].splitlines()


def _listed_paths() -> set[str]:
    """Package-relative paths named in the map, rebuilt from fence indentation."""
    listed: set[str] = set()
    open_dirs: dict[int, str] = {}
    for line in _fenced_tree(MAP_PATH.read_text(encoding="utf-8")):
        if not line.strip() or line.startswith("src/"):
            continue
        match = _ENTRY.match(line)
        assert match is not None, f"unparsable module-map line: {line!r}"
        indent, name = len(match.group("indent")), match.group("name")
        if name.endswith("/"):
            open_dirs = {d: n for d, n in open_dirs.items() if d < indent}
            open_dirs[indent] = name
            continue
        prefix = "".join(d for i, d in sorted(open_dirs.items()) if i < indent)
        listed.add(prefix + name)
    return listed


def _actual_paths() -> set[str]:
    return {str(p.relative_to(PACKAGE_ROOT)) for p in PACKAGE_ROOT.rglob("*.py")}


def test_module_map_lists_every_source_file() -> None:
    missing = sorted(_actual_paths() - _listed_paths())
    assert not missing, (
        f"{len(missing)} file(s) under src/markdown_vault_mcp/ have no line in "
        f"docs/design/module-map.md: {missing}. Add each with a one-line "
        "responsibility in the same commit that adds the module."
    )


def test_module_map_names_no_missing_file() -> None:
    phantom = sorted(_listed_paths() - _actual_paths())
    assert not phantom, (
        f"docs/design/module-map.md names {len(phantom)} path(s) that no longer "
        f"exist: {phantom}. Remove or repoint each entry."
    )
