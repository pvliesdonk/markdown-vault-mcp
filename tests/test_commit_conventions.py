"""Guards for the commit-type contract between the gate, knope, and the docs.

Three parties define what a commit subject means here: `scripts/check_pr_title.py`
(what CI rejects before a squash merge turns a title into a subject), knope
(the release tool, which counts only `feat`, `fix`, and the `!`/breaking
marker — every other type is invisible to both the version computation and
`CHANGELOG.md`, silently), and `AGENTS.md` (what a contributor reads). Let
any one drift and the failure is silent: knope does not warn about a subject
it does not count, it simply ignores it — the same silent-drop class the
gate was built around under python-semantic-release, so the gate's rationale
carries over unchanged (migration M4).

That is not hypothetical. Seven commits reached one downstream project's
`main` with `diag(review):` / `experiment(review):` / prose subjects and are
absent from its generated changelog (see the template's #368). These tests
are what keeps the three parties honest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_pr_title.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CLAUDE_MD = REPO_ROOT / "AGENTS.md"

# The subset knope actually counts, plus the `!` marker (or a
# `BREAKING CHANGE:` footer) for a major. Fixed by the tool, not configured:
# knope has no allowed-tags list to read, so this constant is the assertion
# surface the old PSR-config leg used to provide.
KNOPE_COUNTED_TYPES = ("feat", "fix")

# The exact prose AGENTS.md's Conventions section must carry, so the
# documented reality cannot drift from the tool's (the third leg of the
# three-way lockstep).
CLAUDE_MD_COUNTED_PHRASE = "Only `feat`, `fix`, and the `!` marker drive releases"


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _checker_types() -> list[str]:
    result = _run_checker("--list")
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_knope_counted_types_are_accepted_by_the_gate() -> None:
    """The gate must accept every type the release tool counts."""
    assert set(KNOPE_COUNTED_TYPES) <= set(_checker_types())


def test_claude_md_documents_every_accepted_type() -> None:
    """Contributors read AGENTS.md; an undocumented type is a trap."""
    prose = CLAUDE_MD.read_text(encoding="utf-8")
    missing = [f"`{tag}`" for tag in _checker_types() if f"`{tag}`" not in prose]
    assert not missing, f"AGENTS.md does not document: {missing}"


def test_claude_md_calls_out_the_knope_counted_subset() -> None:
    """AGENTS.md states which types actually drive releases, verbatim.

    knope counts only ``feat``/``fix``/``!`` — the other accepted types keep
    history parseable but cut nothing and never reach ``CHANGELOG.md``. The
    docs must say so in the exact phrase pinned here, or the documented
    conventions drift from the tool's silently.
    """
    prose = CLAUDE_MD.read_text(encoding="utf-8")
    assert CLAUDE_MD_COUNTED_PHRASE in prose, (
        f"AGENTS.md's Conventions section must carry the phrase "
        f"{CLAUDE_MD_COUNTED_PHRASE!r} (kept in lockstep with this test)"
    )


def test_ci_runs_the_checker_and_gates_on_it() -> None:
    """The check is worthless unless the required aggregate depends on it."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check_pr_title.py" in workflow
    assert "\n      - pr-title\n" in workflow, (
        "the pr-title job must be a `needs` entry of the ci-success aggregate"
    )


@pytest.mark.parametrize(
    "title",
    [
        "feat: add a thing",
        "fix(search): stop the writer thread on shutdown",
        "feat(config)!: drop the deprecated env alias",
        "chore(deps): update dependency ruff to v0.9.0",
        "chore: prepare release 1.2.3",
        "revert: feat(search): add hybrid ranking",
        'Revert "feat: add a thing"',
        'Revert "Revert "feat: add a thing""',
        "perf: reuse the compiled pattern",
        "docs: document the new env var",
    ],
)
def test_accepted_titles(title: str) -> None:
    result = _run_checker(title)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "title",
    [
        "diag(review): pin the reviewer action",
        "experiment(review): show full output",
        "Modify CI workflow to handle Renovate PRs",
        'Reverted "feat: add a thing"',
        "Feat: capitalised type",
        "fix:no space after the colon",
        "feat: ",
        "",
    ],
)
def test_rejected_titles(title: str) -> None:
    result = _run_checker(title)
    assert result.returncode == 1
    assert "::error" in result.stdout


def test_rejection_names_the_accepted_types() -> None:
    """An actionable failure tells the author what to type instead."""
    result = _run_checker("diag(review): pin the reviewer action")
    assert result.returncode == 1
    assert ", ".join(_checker_types()) in result.stderr


def test_git_revert_titles_pass_but_warn_about_the_changelog() -> None:
    """The git/GitHub revert form is accepted; its cost is stated, not hidden.

    Neither revert form reaches ``CHANGELOG.md`` under knope — it counts only
    ``feat``/``fix``/``!`` — so the check accepts the title (it is what git
    itself generates) and annotates the run pointing at the notes page, which
    is where reverts get narrated.
    """
    result = _run_checker('Revert "feat: add a thing"')
    assert result.returncode == 0, result.stderr
    assert "::warning" in result.stdout
    assert "CHANGELOG.md" in result.stdout


def test_conventional_revert_titles_pass_without_a_warning() -> None:
    """`revert:` is an accepted type, so it passes the gate cleanly.

    It is changelog-invisible like every non-counted type (chore, docs, ...),
    which earns no per-title warning — the convention is documented in
    AGENTS.md instead; the ``Revert "..."`` warning exists because that shape
    is not a conventional-commit subject at all.
    """
    result = _run_checker("revert: feat: add a thing")
    assert result.returncode == 0, result.stderr
    assert "::warning" not in result.stdout
