#!/usr/bin/env python3
"""Check a pull-request title against the conventional-commit type set.

A squash merge takes the pull-request title as the resulting commit subject,
so the title is the last point at which a subject can be corrected before it
reaches ``main``.  That matters more than style: knope, the release tool,
computes versions and writes ``CHANGELOG.md`` from these subjects -- it
counts only ``feat``, ``fix``, and the ``!``/breaking marker, and every
other subject is ignored silently, with no warning and no fallback heading.
An unrecognised type is exactly that silent-drop class, so this gate keeps
the history parseable and the accepted set deliberate.  The release audit
trail is only as complete as the subjects feeding it.

The type list below, the knope-counted subset, and ``CLAUDE.md``'s
Conventions prose are three legs of one contract.
``tests/test_commit_conventions.py`` fails when they disagree, so no leg can
drift alone.

Usage::

    python3 scripts/check_pr_title.py "feat(search): add hybrid ranking"
    python3 scripts/check_pr_title.py --list

Exits 0 when the title conforms, 1 otherwise, after printing a GitHub
workflow error annotation naming the accepted types.
"""

from __future__ import annotations

import re
import sys

#: Commit types this project accepts.  Only ``feat``, ``fix``, and the ``!``
#: marker drive releases (knope ignores the rest for both version and
#: changelog); the wider set keeps history classifiable and is documented in
#: ``CLAUDE.md``, with ``tests/test_commit_conventions.py`` holding all
#: three in lockstep.
ALLOWED_TYPES: tuple[str, ...] = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

# The conventional-commit subject shape: an optional parenthesised scope, an
# optional `!` breaking marker, a colon, whitespace, then a non-empty
# subject.  Case-sensitive, as knope's parsing is.
_TITLE_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)"
    r"(?:\((?P<scope>[^()\n]+)\))?"
    r"(?P<breaking>!)?"
    r":[ \t]+"
    r"(?P<subject>\S.*)$"
)

# `git revert` and GitHub's revert button both produce `Revert "<original>"`.
# That is the wider ecosystem's shape, not a GitHub quirk, and Conventional
# Commits deliberately leaves revert handling open -- so it is accepted rather
# than second-guessed.  The greedy `.+` also accepts the stacked form a revert
# of a revert produces: `Revert "Revert "feat: x""`.
#
# It does carry a cost, and the caller is told about it rather than left to
# find out: NO revert form reaches CHANGELOG.md -- knope counts only
# feat/fix/breaking subjects, so this shape and the conventional `revert:`
# type are both changelog-invisible.  The release-notes page is where a
# revert gets narrated -- its research runs off merged pull requests and
# linked issues, not commit subjects.
_GIT_REVERT_RE = re.compile(r'^Revert\s+".+"\s*$')

_TYPES_LINE = ", ".join(ALLOWED_TYPES)

_HOW_TO_FIX = (
    "Retitle the pull request, then re-run this job -- it reads the current "
    "title from the API, so no push is needed."
)


def _example() -> str:
    return (
        "Accepted shape: type(optional-scope)!: subject\n"
        "  feat(search): add hybrid ranking\n"
        "  fix: stop the writer thread on shutdown\n"
        "  feat(config)!: drop the deprecated env alias\n"
        f"Accepted types: {_TYPES_LINE}"
    )


#: Emitted for an accepted title the release tooling still ignores.
REVERT_CAVEAT = (
    'Accepted: this is the Revert "..." form git and GitHub generate. '
    "Neither revert form reaches CHANGELOG.md, though -- knope counts only "
    "feat/fix/breaking subjects, so 'revert: ORIGINAL SUBJECT' is equally "
    "changelog-invisible. The release-notes page is what narrates reverts, "
    "since that research runs off merged pull requests rather than commit "
    "subjects."
)


def is_git_revert(title: str) -> bool:
    """Report whether a title is the ``Revert "..."`` form git generates.

    Args:
        title: The pull-request title, as GitHub reports it.

    Returns:
        ``True`` for the git/GitHub revert shape, including the stacked form
        a revert of a revert produces (``Revert`` applied twice).
    """
    return _GIT_REVERT_RE.match(title.strip()) is not None


def check_title(title: str) -> str | None:
    """Validate a pull-request title.

    Args:
        title: The pull-request title, as GitHub reports it.

    Returns:
        ``None`` when the title conforms, otherwise a multi-line explanation
        of what is wrong and how to fix it.
    """
    stripped = title.strip()
    if not stripped:
        return f"The pull-request title is empty.\n{_example()}\n{_HOW_TO_FIX}"

    if is_git_revert(stripped):
        return None

    match = _TITLE_RE.match(stripped)
    if not match:
        return (
            "The pull-request title is not a conventional-commit subject.\n"
            f"{_example()}\n{_HOW_TO_FIX}"
        )

    commit_type = match.group("type")
    if commit_type not in ALLOWED_TYPES:
        lowered = commit_type.lower()
        hint = ""
        if lowered in ALLOWED_TYPES:
            hint = f"\nTypes are case-sensitive; use {lowered!r}, not {commit_type!r}."
        return (
            f"{commit_type!r} is not an accepted commit type. The release "
            "tooling silently ignores a subject with an unrecognised type -- "
            "no version bump, no CHANGELOG.md entry, no warning.\n"
            f"Accepted types: {_TYPES_LINE}{hint}\n{_HOW_TO_FIX}"
        )

    return None


def main(argv: list[str]) -> int:
    """Run the check.

    Args:
        argv: Command-line arguments without the program name. Either
            ``["--list"]`` or a single pull-request title.

    Returns:
        Process exit status: 0 when the title conforms, 1 otherwise.
    """
    if argv == ["--list"]:
        print("\n".join(ALLOWED_TYPES))
        return 0
    if len(argv) != 1:
        print("usage: check_pr_title.py [--list | TITLE]", file=sys.stderr)
        return 2

    problem = check_title(argv[0])
    if problem is None:
        print(f"Pull-request title OK: {argv[0]}")
        if is_git_revert(argv[0]):
            print(REVERT_CAVEAT)
            print(f"::warning title=Revert title::{REVERT_CAVEAT}")
        return 0

    print(problem, file=sys.stderr)
    summary = problem.splitlines()[0]
    print(f"::error title=Pull-request title::{summary}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
