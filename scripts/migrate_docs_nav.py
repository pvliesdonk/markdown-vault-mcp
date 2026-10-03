"""Move a downstream's nav onto the goal-shaped frame during `copier update`.

Runs from copier.yml `_migrations` (after-stage, every update).  Before the
frame, the whole `nav:` was one project-owned `PROJECT-NAV` block; now the
template owns the frame and gives each section its own `PROJECT-NAV-<SECTION>`
block.  Copier cannot map one onto the other, so it leaves diff3 conflict
markers in `nav:` (`<<<<<<< before updating`, `||||||| last update`,
`=======`, `>>>>>>> after updating`).  The project's real pre-update nav is
still at git HEAD, so recover from there, never from the conflict-marked file.

Steps (each printed when taken):
1. Resolve every conflict inside `nav:` to its "after updating" side, which
   together with the cleanly merged lines is the new frame.
2. Read HEAD:mkdocs.yml's old `PROJECT-NAV` block and keep each entry whose
   page the resolved nav does not already list, with its section titles.  A
   template page the frame lists at its new path (`REDIRECTS` in
   migrate_docs_pages.py) counts as listed, so it is dropped (#745).
3. Write those entries into the `PROJECT-NAV-UNSORTED` block at the end of
   `nav:` for the agent applying the update to sort into the section blocks.
4. Resolve the conflict copier leaves where the old hand-kept
   `PROJECT-LLMSTXT-SECTIONS` list used to be (#714) to the template side:
   llms.txt is now built from the nav, and the old list is dropped.

Nothing outside `nav:` is changed.  Idempotent; a no-op on `copier copy` (no
HEAD:mkdocs.yml) and on a project whose HEAD already has the frame.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml
from migrate_docs_pages import DOCS, REDIRECTS

OLD_START = "# PROJECT-NAV-START"
OLD_END = "# PROJECT-NAV-END"
NEW_MARKER = "PROJECT-NAV-USE-START"
UNSORTED_START = "PROJECT-NAV-UNSORTED-START"
UNSORTED_END = "PROJECT-NAV-UNSORTED-END"
OLD_LLMSTXT = "PROJECT-LLMSTXT-SECTIONS-START"
OLD_LLMSTXT_END = "PROJECT-LLMSTXT-SECTIONS-END"
_OURS = "<<<<<<< "
_BASE = "||||||| "
_SEP = "======="
_THEIRS = ">>>>>>> "
_MARKERS = (_OURS, _BASE, _SEP, _THEIRS)


def _nav_bounds(lines: list[str]) -> tuple[int, int]:
    """Return ``(first, stop)``: the ``nav:`` line and the line after its block."""
    first = lines.index("nav:")
    stop = first + 1
    while stop < len(lines):
        line = lines[stop]
        top_level = line and not line.startswith((" ", "#", *_MARKERS))
        if top_level:
            break
        stop += 1
    return first, stop


def _resolve(region: list[str]) -> list[str]:
    """Keep clean lines and each conflict's "after updating" side."""
    out: list[str] = []
    side = None
    for line in region:
        if line.startswith(_OURS):
            side = "before"
        elif line.startswith(_BASE):
            side = "base"
        elif line.startswith(_SEP) and side is not None:
            side = "after"
        elif line.startswith(_THEIRS):
            side = None
        elif side in (None, "after"):
            out.append(line)
    return out


def _leaves(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [leaf for item in node for leaf in _leaves(item)]
    if isinstance(node, dict):
        return [leaf for value in node.values() for leaf in _leaves(value)]
    return []


def _old_project_nav(head_text: str) -> list[Any] | None:
    """Return the old ``PROJECT-NAV`` entries, or ``None`` when there is no old nav.

    Raises:
        ValueError: The old block has no closing marker, so it cannot be read.
    """
    if NEW_MARKER in head_text or OLD_START not in head_text:
        return None
    lines = head_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith(OLD_START)]
    ends = [i for i, line in enumerate(lines) if line.strip().startswith(OLD_END)]
    if not ends:
        msg = "HEAD:mkdocs.yml has PROJECT-NAV-START but no PROJECT-NAV-END"
        raise ValueError(msg)
    start, end = starts[0], ends[0]
    body = [
        line for line in lines[start + 1 : end] if not line.lstrip().startswith("#")
    ]
    loaded = yaml.safe_load("nav:\n" + "\n".join(body))
    return loaded["nav"] if loaded else []


def _keep_missing(node: Any, present: set[str]) -> Any:
    """Drop leaves already present in the nav, and sections left empty.

    *present* holds each page's new path and the old paths that redirect to it.
    """
    if isinstance(node, str):
        return None if node in present else node
    if isinstance(node, list):
        items = [
            k for k in (_keep_missing(item, present) for item in node) if k is not None
        ]
        return items or None
    if isinstance(node, dict):
        mapping: dict[str, Any] = {}
        for key, value in node.items():
            sub = _keep_missing(value, present)
            if sub is not None:
                mapping[key] = sub
        return mapping or None
    return None


class _IndentedDumper(yaml.SafeDumper):
    """Indent lists under their key, matching the nav frame's style."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        del indentless  # PyYAML passes it by keyword; always indent instead
        super().increase_indent(flow, False)


def _nav_skip_reason(lines: list[str], head_text: str) -> str | None:
    """Say why the old nav cannot be moved, or ``None`` when it can (or needn't be)."""
    try:
        old_nav = _old_project_nav(head_text)
    except ValueError as exc:
        return f"nav: left as copier wrote it ({exc})"
    if old_nav is None:
        return None
    if "nav:" not in lines:
        return "nav: left as copier wrote it (no top-level nav: key in mkdocs.yml)"
    first, stop = _nav_bounds(lines)
    if not any(UNSORTED_END in line for line in _resolve(lines[first + 1 : stop])):
        return "nav: left as copier wrote it (the new frame has no Unsorted block)"
    return None


def _insert_unsorted(region: list[str], missing: Any) -> list[str]:
    """Return *region* with *missing* written into the Unsorted block."""
    dumped = yaml.dump(
        missing,
        Dumper=_IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    block = ["  " + line for line in dumped.rstrip("\n").split("\n")]
    end = next(i for i, line in enumerate(region) if UNSORTED_END in line)
    return region[:end] + block + region[end:]


def park(updated_text: str, head_text: str) -> tuple[str, list[str]]:
    """Return the migrated ``mkdocs.yml`` text and the parked page paths.

    Any shape this migration was not written for (no ``nav:``, an unreadable
    old block, a frame without the Unsorted block) returns *updated_text*
    unchanged: copier's own conflict markers then stay for the agent applying the update, which
    loses nothing, where a guess could lose the project's entries.
    """
    lines = updated_text.split("\n")
    if _nav_skip_reason(lines, head_text) is not None:
        return updated_text, []
    old_nav = _old_project_nav(head_text)
    if old_nav is None:
        return updated_text, []
    first, stop = _nav_bounds(lines)
    region = _resolve(lines[first + 1 : stop])
    present = set(_leaves(yaml.safe_load("nav:\n" + "\n".join(region))["nav"]))
    present |= {
        old.removeprefix(DOCS)
        for old, new in REDIRECTS
        if new.removeprefix(DOCS) in present
    }
    missing = _keep_missing(old_nav, present)
    parked = _leaves(missing) if missing else []
    if missing:
        region = _insert_unsorted(region, missing)
    new_lines = lines[: first + 1] + region + lines[stop:]
    return "\n".join(new_lines), parked


def _only_the_list(old_sides: list[str]) -> bool:
    """True when the hunk's old side holds nothing but the removed llms list."""
    inside = False
    for line in old_sides:
        stripped = line.strip()
        if OLD_LLMSTXT in stripped:
            inside = True
        if inside:
            if OLD_LLMSTXT_END in stripped:
                inside = False
            continue
        if (
            stripped
            and not stripped.startswith(("#", _BASE))
            and stripped != "sections:"
        ):
            return False
    return True


def _clear_llmstxt_conflicts(lines: list[str]) -> tuple[list[str], list[str]]:
    """Resolve to the template side each conflict that only held the llms list.

    Returns the lines and a reason for every such conflict kept as copier
    wrote it (one that also holds other project lines, or a malformed one).
    """
    out: list[str] = []
    kept: list[str] = []
    i = 0
    while i < len(lines):
        end = _hunk_end(lines, i)
        if end is None:
            out.append(lines[i])
            i += 1
            continue
        hunk = lines[i : end + 1]
        sep = next((k for k, line in enumerate(hunk) if line.startswith(_SEP)), None)
        if sep is None or not any(OLD_LLMSTXT in line for line in hunk[1:sep]):
            out.extend(hunk)
        elif _only_the_list(hunk[1:sep]):
            out.extend(hunk[sep + 1 : -1])
        else:
            out.extend(hunk)
            kept.append(
                "llms.txt: the conflict where the section list was also holds other "
                "lines of yours; left for you to resolve (keep the template side, "
                "plus your own lines)"
            )
        i = end + 1
    return out, kept


def _hunk_end(lines: list[str], i: int) -> int | None:
    if not lines[i].startswith(_OURS):
        return None
    return next((j for j in range(i, len(lines)) if lines[j].startswith(_THEIRS)), None)


def migrate(updated_text: str, head_text: str) -> tuple[str, list[str]]:
    """Clear the removed llms.txt list's conflict, then move the nav (see `park`)."""
    cleared, _ = _clear_llmstxt_conflicts(updated_text.split("\n"))
    return park("\n".join(cleared), head_text)


def reasons(updated_text: str, head_text: str) -> list[str]:
    """Say what the migration left for the agent applying the update, and why."""
    cleared, kept = _clear_llmstxt_conflicts(updated_text.split("\n"))
    nav = _nav_skip_reason(cleared, head_text)
    return kept + ([nav] if nav else [])


def _head_mkdocs(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "show", "HEAD:mkdocs.yml"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> None:
    root = Path.cwd()
    path = root / "mkdocs.yml"
    head = _head_mkdocs(root)
    if head is None or not path.exists() or NEW_MARKER in head:
        return
    before = path.read_text(encoding="utf-8")
    after, parked = migrate(before, head)
    for reason in reasons(before, head):
        print(
            f"migrate_docs_nav: {reason}; your old file is at `git show HEAD:mkdocs.yml`"
        )
    if after != before:
        path.write_text(after, encoding="utf-8")
        print("migrate_docs_nav: rebuilt nav: on the section frame")
    if parked:
        print(
            f"migrate_docs_nav: parked {len(parked)} of your nav entries under "
            "Unsorted at the end of nav: in mkdocs.yml; move each into the "
            "section it belongs to, then `git add mkdocs.yml`"
        )


if __name__ == "__main__":
    main()
