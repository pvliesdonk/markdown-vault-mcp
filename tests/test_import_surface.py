"""Guard for the public import surface of the `markdown_vault_mcp` package root.

A change that removes or renames a name importable from the package root is a
breaking change to the public library interface, and must be marked as such
(`feat!:` / `fix!:`, or a `BREAKING CHANGE:` footer) so the release pipeline
cuts a major.  Historically this class of break shipped unmarked: the author
sees an internal refactor, downstream consumers see `ImportError`.  These tests
make the surface mechanical — the project-owned snapshot
`tests/public_import_surface.txt` is the expected set of names, and any drift
between it and the real, imported package fails at PR time with instructions.

The surface definition: every non-underscore name in ``dir(package)`` or
``__all__`` that resolves via ``getattr``, enumerated in a fresh interpreter.
``dir()`` + ``getattr`` (rather than a static ``__all__`` read) is deliberate —
a root that re-exports through a lazy PEP 562 ``__getattr__`` map still counts,
and a minimal root exporting nothing yields an empty surface.  The fresh
interpreter keeps the result independent of whatever submodules earlier tests
happened to import.

Ownership: this file is template-owned (re-rendered on `copier update`); the
snapshot is project-owned (seeded once, then yours).  Regenerate it with::

    uv run python tests/test_import_surface.py --update
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "tests" / "public_import_surface.txt"
UPDATE_COMMAND = "uv run python tests/test_import_surface.py --update"

_HEADER = """\
# Public import surface of the `markdown_vault_mcp` package root — one name
# per line, sorted.  `#` lines and blank lines are ignored.
#
# Project-owned (seeded once by the template, never re-rendered), consumed by
# the template-owned tests/test_import_surface.py.  Regenerate with:
#
#     uv run python tests/test_import_surface.py --update
#
# Removing a name from this file is a breaking change to the public library
# interface: the commit that does it must be marked breaking (`feat!:` /
# `fix!:`, or a `BREAKING CHANGE:` footer) per the versioning policy
# (pvliesdonk/fastmcp-server-template#342).
"""

# Runs in a fresh interpreter so the enumeration cannot be polluted by
# submodules that earlier tests imported (importing `pkg.sub` binds `sub` as an
# attribute of `pkg` for the rest of the process).
_ENUMERATE = """\
import importlib
import json

module = importlib.import_module("markdown_vault_mcp")
names = set(dir(module)) | set(getattr(module, "__all__", ()))
missing = object()
public = sorted(
    name
    for name in names
    if not name.startswith("_") and getattr(module, name, missing) is not missing
)
print(json.dumps(public))
"""


def current_surface() -> list[str]:
    """Enumerate the package root's public names in a fresh interpreter.

    Returns:
        Sorted list of public attribute names importable from the package root.
    """
    proc = subprocess.run(
        [sys.executable, "-P", "-c", _ENUMERATE],
        capture_output=True,
        text=True,
        check=True,
    )
    result: list[str] = json.loads(proc.stdout)
    return result


def snapshot_names() -> list[str]:
    """Parse the snapshot file: one name per line, `#` comments and blanks ignored.

    Returns:
        The declared names, in file order.
    """
    lines = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    stripped = (line.strip() for line in lines)
    return [line for line in stripped if line and not line.startswith("#")]


def test_snapshot_file_exists() -> None:
    """The snapshot is the contract; without it there is nothing to hold."""
    assert SNAPSHOT.is_file(), (
        f"{SNAPSHOT.relative_to(REPO_ROOT)} is missing — it records the public "
        f"import surface of `markdown_vault_mcp`. Generate it with:\n"
        f"    {UPDATE_COMMAND}\n"
        "and commit the result."
    )


def test_snapshot_is_sorted_and_unique() -> None:
    """A canonical (sorted, deduplicated) snapshot keeps its diffs reviewable."""
    names = snapshot_names()
    assert names == sorted(set(names)), (
        f"{SNAPSHOT.relative_to(REPO_ROOT)} is not sorted/deduplicated — "
        f"regenerate it with:\n    {UPDATE_COMMAND}"
    )


def test_public_import_surface_matches_snapshot() -> None:
    """The imported surface and the snapshot must agree exactly, both ways.

    Removals are the dangerous direction (an unmarked library break); additions
    fail too, so the snapshot diff stays the reviewable record of every surface
    change rather than silently lagging reality.
    """
    actual = set(current_surface())
    expected = set(snapshot_names())
    removed = sorted(expected - actual)
    added = sorted(actual - expected)
    problems: list[str] = []
    if removed:
        problems.append(
            "public names REMOVED from the import surface of "
            f"`markdown_vault_mcp`: {', '.join(removed)}\n"
            "  Removing or renaming a public name is a BREAKING change to the "
            "public library interface.\n"
            "  Either restore the names, or — if the removal is intentional —\n"
            f"    1. regenerate the snapshot:  {UPDATE_COMMAND}\n"
            "    2. mark the commit/PR breaking (`feat!:` / `fix!:`, or a "
            "`BREAKING CHANGE:` footer)\n"
            "       per the versioning policy's public-library-interface tier "
            "(pvliesdonk/fastmcp-server-template#342)."
        )
    if added:
        problems.append(
            "public names ADDED to the import surface of "
            f"`markdown_vault_mcp`: {', '.join(added)}\n"
            "  Additions are not breaking — record them so the snapshot stays "
            "the reviewed surface:\n"
            f"    {UPDATE_COMMAND}\n"
            "  then commit the updated snapshot with this change."
        )
    assert not problems, "\n".join(problems)


def _update_snapshot() -> int:
    """Rewrite the snapshot from the currently importable surface.

    Returns:
        Number of names written.
    """
    names = current_surface()
    SNAPSHOT.write_text(
        _HEADER + "".join(f"{name}\n" for name in names), encoding="utf-8"
    )
    return len(names)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Public import-surface snapshot tool for `markdown_vault_mcp`."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="regenerate tests/public_import_surface.txt from the imported package",
    )
    if not parser.parse_args().update:
        parser.error("nothing to do — pass --update to regenerate the snapshot")
    count = _update_snapshot()
    sys.stdout.write(
        f"wrote {SNAPSHOT.relative_to(REPO_ROOT)} ({count} public names)\n"
    )
