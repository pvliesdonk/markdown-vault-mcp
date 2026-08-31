#!/usr/bin/env python3
"""Helpers for the copier-update workflow: upgrade notes and staged targets.

Two subcommands, both consumed by ``.github/workflows/copier-update.yml``:

``notes``
    Select the UPGRADING.md sections a copier-update PR must surface.
``section-files``
    Print the per-minor ``upgrading/vX.Y.md`` paths a jump needs, so the
    workflow can fetch each one before running ``notes``.
``pick-target``
    Choose the ref an *automated* update run should target, stepping at
    most one major at a time so a multi-major jump becomes a sequence of
    per-major PRs instead of one giant one.

notes
=====

The template's UPGRADING.md is template-repo only (listed in ``_exclude``),
so an operator reviewing a copier-update PR does not have it in the project
tree.  The copier-update workflow fetches the file from the template source
at the target ref and runs this subcommand to embed exactly the sections
the jump needs, following the file's own reading instructions: the previous
ref's minor section first (later patches in that line may carry migration
work this project has not done yet), then every later minor through the
target's.

Since the template split UPGRADING.md into an index plus per-minor
``upgrading/vX.Y.md`` files, the index's released sections are one-line
pointers.  Pass ``--sections-dir`` with a directory holding the fetched
per-minor files (see ``section-files``) and each in-range minor embeds its
file's full content; a minor whose file is absent from the directory falls
back to its index pointer section, so the embed degrades to a working link
rather than going silently empty.  Without ``--sections-dir`` the selection
runs over the index text alone — against a pre-split template that IS the
full content, and against a post-split template it is the pointer rows,
the same degraded-but-linked shape.

Selection rules:

- previous ``vX.Y.Z`` and target ``vA.B.C``: every ``## vX.Y ...`` section
  whose minor lies in ``[previous minor, target minor]``, in file order.
- target is not a version tag (a branch or sha run): from the previous
  minor through the end of the file, ``## Unreleased`` included — an
  unreleased ref may carry migration notes not yet promoted under a
  version heading.  The Unreleased section always comes from the index;
  it is the one section the split leaves there in full.
- previous is missing or unparseable (first tracked update): emit nothing;
  the caller links the full file instead of guessing a range.
- selection longer than ``--max-chars``: emit a one-line overflow note
  naming the range instead of the sections — GitHub PR bodies cap at
  65536 characters, and the caller's link to the full file is the durable
  path.

All of the above exit 0 (an empty output file is a valid outcome the
workflow renders around); only an unreadable input or unwritable output
fails.

section-files
=============

Reads the index the same way ``notes`` does and prints the repo-relative
``upgrading/vX.Y.md`` path of every in-range minor, one per line — the
fetch list for the workflow, derived from the index rather than guessed
from version arithmetic, so a minor that never existed (a skipped number)
is never requested and a 404 never looks like a missing note.  Prints
nothing when the previous ref is unparseable, matching ``notes``.

pick-target
===========

Reads the template's tag list (one per line, as ``git ls-remote --tags
--refs`` prints the short names) and the previous ref, and prints the ref
the run should pass as ``--vcs-ref`` — or nothing, meaning "use copier's
default" (the latest tag):

- stable tags (``vX.Y.Z``) with a major above the previous ref's exist:
  print the newest stable tag of the *smallest* such major.  Merging that
  PR moves ``_commit`` forward, so the next scheduled run picks the next
  major — stacked update PRs per major, without branch-on-branch mechanics.
- no majors ahead (only minor/patch drift), or the previous ref is not a
  version tag: print nothing.  An explicit ``vcs_ref`` dispatch input
  bypasses this subcommand entirely and jumps straight to the asked ref.
- release candidates and other non-``vX.Y.Z`` tags never become automated
  targets.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MINOR_HEADING_RE = re.compile(r"^## v(\d+)\.(\d+)(?:\s|$)")
UNRELEASED_HEADING_RE = re.compile(r"^## Unreleased(?:\s|$)")
VERSION_REF_RE = re.compile(r"^v(\d+)\.(\d+)(?:\.\d+)?$")
STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_minor(ref: str) -> tuple[int, int] | None:
    """``vX.Y[.Z]`` -> ``(X, Y)``; anything else (branch, sha) -> None."""
    match = VERSION_REF_RE.match(ref.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)))


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split on ``## `` headings into (heading_line, full_section) pairs.

    The preamble before the first heading is dropped — it is reading
    guidance for the full file, not migration content for a range.
    """
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(lines).rstrip() + "\n"))
            heading = line
            lines = [line]
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(lines).rstrip() + "\n"))
    return sections


def _in_range(
    heading: str,
    lower: tuple[int, int],
    upper: tuple[int, int] | None,
) -> bool:
    """Whether a section heading falls inside the [lower, upper] minor range.

    ``upper=None`` means "through the end of the file": every minor at or
    above ``lower`` matches, and so does ``## Unreleased``.
    """
    minor_match = MINOR_HEADING_RE.match(heading)
    if minor_match is not None:
        minor = (int(minor_match.group(1)), int(minor_match.group(2)))
        return minor >= lower and (upper is None or minor <= upper)
    return upper is None and UNRELEASED_HEADING_RE.match(heading) is not None


def select_sections(
    text: str, previous: str, target: str, sections_dir: Path | None = None
) -> str:
    """The markdown to embed in the PR body — possibly empty.

    With *sections_dir*, an in-range ``## vX.Y`` index section is replaced
    by the full content of ``sections_dir/vX.Y.md`` when that file exists;
    otherwise (file missing, or no directory given) the index section
    itself is used — a pointer row after the split, the full content
    before it. ``## Unreleased`` always comes from *text*.
    """
    lower = parse_minor(previous)
    if lower is None:
        return ""
    upper = parse_minor(target)
    selected: list[str] = []
    for heading, section in split_sections(text):
        if not _in_range(heading, lower, upper):
            continue
        minor_match = MINOR_HEADING_RE.match(heading)
        if sections_dir is not None and minor_match is not None:
            section_path = (
                sections_dir / f"v{minor_match.group(1)}.{minor_match.group(2)}.md"
            )
            if section_path.is_file():
                section = section_path.read_text(encoding="utf-8").rstrip() + "\n"
        selected.append(section)
    return "\n".join(selected)


def section_files(text: str, previous: str, target: str) -> list[str]:
    """Repo-relative per-minor paths for the jump, in index order."""
    lower = parse_minor(previous)
    if lower is None:
        return []
    upper = parse_minor(target)
    paths: list[str] = []
    for heading, _section in split_sections(text):
        minor_match = MINOR_HEADING_RE.match(heading)
        if minor_match is not None and _in_range(heading, lower, upper):
            paths.append(f"upgrading/v{minor_match.group(1)}.{minor_match.group(2)}.md")
    return paths


def pick_target(previous: str, tags: list[str]) -> str:
    """The ref an automated run should target, or "" for copier's default.

    One major at a time: with stable tags whose major exceeds the previous
    ref's, the newest tag of the smallest such major is the target.
    """
    prev = parse_minor(previous)
    if prev is None:
        return ""
    parsed = [
        (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        for tag in tags
        if (m := STABLE_TAG_RE.match(tag.strip())) is not None
    ]
    ahead = [version for version in parsed if version[0] > prev[0]]
    if not ahead:
        return ""
    next_major = min(version[0] for version in ahead)
    newest = max(version for version in ahead if version[0] == next_major)
    return f"v{newest[0]}.{newest[1]}.{newest[2]}"


def _run_notes(args: argparse.Namespace) -> int:
    try:
        text = args.upgrading.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error::cannot read {args.upgrading}: {exc}", file=sys.stderr)
        return 1

    notes = select_sections(text, args.previous, args.target, args.sections_dir)
    if len(notes) > args.max_chars:
        notes = (
            "> The upgrade notes for this jump are too long to embed here — "
            f"read `UPGRADING.md` from the `{args.previous}` minor's section "
            "through the target's (linked below).\n"
        )

    try:
        args.output.write_text(notes, encoding="utf-8")
    except OSError as exc:
        print(f"::error::cannot write {args.output}: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_section_files(args: argparse.Namespace) -> int:
    try:
        text = args.upgrading.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error::cannot read {args.upgrading}: {exc}", file=sys.stderr)
        return 1
    for path in section_files(text, args.previous, args.target):
        print(path)
    return 0


def _run_pick_target(args: argparse.Namespace) -> int:
    try:
        tags = args.tags.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"::error::cannot read {args.tags}: {exc}", file=sys.stderr)
        return 1
    print(pick_target(args.previous, tags))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    notes = subparsers.add_parser(
        "notes", help="select UPGRADING.md sections for the PR body"
    )
    notes.add_argument("--upgrading", required=True, type=Path)
    notes.add_argument("--previous", required=True)
    notes.add_argument("--target", required=True)
    notes.add_argument("--output", required=True, type=Path)
    notes.add_argument("--max-chars", type=int, default=30000)
    notes.add_argument(
        "--sections-dir",
        type=Path,
        default=None,
        help="Directory of fetched per-minor upgrading/vX.Y.md files; an "
        "in-range minor found here embeds in full, one missing falls back "
        "to its index pointer section.",
    )
    notes.set_defaults(func=_run_notes)

    files = subparsers.add_parser(
        "section-files",
        help="print the per-minor upgrading/vX.Y.md paths a jump needs",
    )
    files.add_argument("--upgrading", required=True, type=Path)
    files.add_argument("--previous", required=True)
    files.add_argument("--target", required=True)
    files.set_defaults(func=_run_section_files)

    pick = subparsers.add_parser(
        "pick-target", help="choose the next automated update ref (one major max)"
    )
    pick.add_argument("--previous", required=True)
    pick.add_argument("--tags", required=True, type=Path)
    pick.set_defaults(func=_run_pick_target)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
