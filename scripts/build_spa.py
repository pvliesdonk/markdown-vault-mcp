#!/usr/bin/env python3
"""Assemble the modular SPA sources into the single ``static/app.src.html``.

The MCP Apps front-end is authored as small partials under
``src/<module>/static/spa/`` (a shell, one stylesheet, a core module, and one
file per view) and assembled here into ``static/app.src.html`` — the single,
self-contained file that ``scripts/vendor_spa.py`` then vendors into the served
``static/app.html``.  The full build pipeline is::

    spa/*  ->  build_spa.py  ->  app.src.html  ->  vendor_spa.py  ->  app.html

Assembly is a recursive include: every ``/*@@FILE:relpath@@*/`` marker in a
partial is replaced by the verbatim bytes of ``spa/relpath``.  The marker is a
valid comment in both CSS (inside ``<style>``) and JavaScript (inside
``<script>``), so each partial stays independently parseable — ``core.js`` is
the whole module with view markers inside its ``try`` block, and each
``views/*.js`` is an IIFE-wrapped view module (its free variables, e.g.
``app``/``switchTab``, resolve to ``core.js`` scope once assembled).

The generated ``static/app.src.html`` carries a "do not edit" banner that this
script injects at assembly time (it is not stored in ``shell.html``, which is an
editable source partial). ``static/app.src.html`` is a **generated, committed**
artifact: edit the files under ``static/spa/`` and regenerate, never hand-edit
it.

Usage::

    python scripts/build_spa.py            # regenerate app.src.html
    python scripts/build_spa.py --check     # verify app.src.html is up-to-date
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A ``/*@@FILE:path@@*/`` include marker — valid comment in CSS and JS alike.
_MARKER = re.compile(r"/\*@@FILE:([^@]+?)@@\*/")

# Guard against a partial including itself (directly or via a cycle).
_MAX_DEPTH = 64

# The shell must open with this so the injected banner lands *after* the
# doctype: keeping the DOCTYPE the first line makes document mode unambiguous
# across parsers (legacy engines treat pre-doctype content as a quirks trigger).
_DOCTYPE = "<!DOCTYPE html>\n"

# Injected into the generated output at assembly time (kept out of shell.html,
# which is an editable source partial).
_GENERATED_BANNER = (
    "<!-- GENERATED FILE — do not edit. Source lives in static/spa/*; regenerate with\n"
    "     `python scripts/build_spa.py`, then vendor with `python scripts/vendor_spa.py`. -->\n"
)


def _find_spa_dir() -> Path:
    """Locate the single ``src/<module>/static/spa`` directory.

    Discovered at runtime (rather than hard-coding the package name) so this
    script stays project-agnostic, mirroring ``vendor_spa.py``.
    """
    repo_root = Path(__file__).resolve().parent.parent
    src_root = repo_root / "src"
    candidates = sorted(src_root.glob("*/static/spa/shell.html"))
    if not candidates:
        raise SystemExit(
            f"ERROR: no src/*/static/spa/shell.html found under {src_root}"
        )
    if len(candidates) > 1:
        found = ", ".join(str(c) for c in candidates)
        raise SystemExit(f"ERROR: multiple spa/shell.html found: {found}")
    return candidates[0].parent


def _read_partial(spa_dir: Path, rel: str) -> str:
    path = (spa_dir / rel).resolve()
    if not path.is_relative_to(spa_dir.resolve()):
        raise SystemExit(f"ERROR: include escapes spa/ dir: {rel}")
    if not path.is_file():
        raise SystemExit(f"ERROR: included partial not found: {rel}")
    return path.read_text(encoding="utf-8")


def assemble(spa_dir: Path) -> str:
    """Return the assembled ``app.src.html`` text from ``spa/shell.html``."""

    def expand(text: str, depth: int) -> str:
        if depth > _MAX_DEPTH:
            raise SystemExit("ERROR: include recursion too deep (cycle?)")
        return _MARKER.sub(
            lambda m: expand(_read_partial(spa_dir, m.group(1).strip()), depth + 1),
            text,
        )

    shell = _read_partial(spa_dir, "shell.html")
    result = expand(shell, 0)

    # A malformed marker (e.g. a missing colon) never matches _MARKER, so it
    # survives expansion as literal text — fail loudly rather than ship a file
    # with a partial silently omitted.
    if "@@FILE" in result:
        raise SystemExit(
            "ERROR: an @@FILE include marker survived assembly — check for a "
            "malformed /*@@FILE:path@@*/ marker in the spa/ partials."
        )
    if not result.startswith(_DOCTYPE):
        raise SystemExit("ERROR: spa/shell.html must begin with '<!DOCTYPE html>'.")
    return _DOCTYPE + _GENERATED_BANNER + result[len(_DOCTYPE) :]


def main(argv: list[str]) -> int:
    """Entry point. Returns 0 on success, 1 on failure."""
    check = "--check" in argv[1:]
    spa_dir = _find_spa_dir()
    out_path = spa_dir.parent / "app.src.html"
    assembled = assemble(spa_dir)

    if check:
        current = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if current == assembled:
            print("OK: app.src.html is up-to-date.")
            return 0
        print(
            "ERROR: app.src.html is out of date — run "
            "`python scripts/build_spa.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    out_path.write_text(assembled, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
