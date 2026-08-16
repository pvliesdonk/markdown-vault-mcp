"""Guards for the commit-type contract between PSR, CI, and the docs.

Three places name the accepted commit types: `pyproject.toml`'s
`[tool.semantic_release.commit_parser_options] allowed_tags` (what
python-semantic-release will parse), `scripts/check_pr_title.py` (what CI
rejects before a squash merge turns a title into a subject), and `CLAUDE.md`
(what a contributor reads). Let any one drift and the failure is silent: PSR
does not warn about a subject it cannot parse, it simply omits the commit from
`CHANGELOG.md` — no fallback heading, no entry.

That is not hypothetical. Seven commits reached one downstream project's
`main` with `diag(review):` / `experiment(review):` / prose subjects and are
absent from its generated changelog (see the template's #368). These tests are
what keeps the three halves honest.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_pr_title.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


def _parser_options() -> dict[str, list[str]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    options = data["tool"]["semantic_release"]["commit_parser_options"]
    return {key: [str(item) for item in value] for key, value in options.items()}


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


def test_checker_and_psr_accept_the_same_types() -> None:
    """The gate must not reject what PSR accepts, or accept what it drops."""
    assert set(_checker_types()) == set(_parser_options()["allowed_tags"])


def test_release_bump_types_are_parseable() -> None:
    """A type that bumps a version but does not parse would never bump one."""
    options = _parser_options()
    bumping = set(options["minor_tags"]) | set(options["patch_tags"])
    assert bumping <= set(options["allowed_tags"])


def test_claude_md_documents_every_accepted_type() -> None:
    """Contributors read CLAUDE.md; an undocumented type is a trap."""
    prose = CLAUDE_MD.read_text(encoding="utf-8")
    missing = [f"`{tag}`" for tag in _checker_types() if f"`{tag}`" not in prose]
    assert not missing, f"CLAUDE.md does not document: {missing}"


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

    Neither of python-semantic-release 10.5.3's parsers reads that shape --
    both return ParseError -- so the commit never reaches CHANGELOG.md. The
    check accepts the title (it is what git itself generates) and annotates
    the run rather than leaving the author to discover the gap at release
    time.
    """
    result = _run_checker('Revert "feat: add a thing"')
    assert result.returncode == 0, result.stderr
    assert "::warning" in result.stdout
    assert "CHANGELOG.md" in result.stdout


def test_conventional_revert_titles_pass_without_a_warning() -> None:
    """`revert:` is in allowed_tags, so that form does reach the changelog."""
    result = _run_checker("revert: feat: add a thing")
    assert result.returncode == 0, result.stderr
    assert "::warning" not in result.stdout
