"""Behavioral tests for scripts/merge_back.sh against sandbox git repos.

After any release cut from a release/X.Y branch, the release workflow's
merge-back job merges the branch back into main — mandatory, because PSR's
already-released check is repo-global while its version computation is
ancestry-scoped: an unmerged branch release leaves PSR on main recomputing the
same version, finding the tag taken, and reporting "already released" forever.

The script's contract, asserted here:

* already-merged branches are a no-op (idempotent job re-runs);
* a clean merge (the rc-finalise case: main untouched since the cut) lands the
  branch's release commits, version metadata included;
* conflicts on version-coupled files (the backport case: main released again
  after the cut) resolve to MAIN's side — main is authoritative for current
  metadata, the tag preserves the branch's state;
* any other conflict aborts the merge, leaves a clean tree, and exits
  non-zero for a human to take over.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MERGE_BACK = REPO_ROOT / "scripts" / "merge_back.sh"

PYPROJECT_STUB = """\
[project]
name = "sandbox"
version = "{version}"

[tool.semantic_release]
assets = ["server.json"]

[tool.semantic_release.changelog.default_templates]
changelog_file = "CHANGELOG.md"
"""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _write_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(PYPROJECT_STUB.format(version=version))
    # json.dumps, not an f-string literal: doubled braces in this
    # template-rendered file would read as Jinja delimiters.
    (repo / "server.json").write_text(json.dumps({"version": version}) + "\n")


def _make_repo(tmp_path: Path) -> Path:
    """A repo on main at version 4.0.0 with a release/4.0 branch cut from it."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _write_version(repo, "4.0.0")
    (repo / "CHANGELOG.md").write_text("# Changelog\n")
    (repo / "app.py").write_text("x = 1\n")
    _commit_all(repo, "chore: baseline")
    _git(repo, "branch", "release/4.0")
    return repo


def _merge_back(repo: Path, branch: str, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MERGE_BACK), branch, tag],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_already_merged_branch_is_a_noop(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")

    result = _merge_back(repo, "release/4.0", "v4.0.0")

    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout
    assert _git(repo, "rev-parse", "HEAD") == before


def test_clean_merge_lands_release_commits(tmp_path: Path) -> None:
    """The rc-finalise case: main untouched since the cut, so the branch's
    version bump flows onto main via an ordinary clean merge."""
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "release/4.0")
    _write_version(repo, "4.1.0")
    _commit_all(repo, "4.1.0")
    _git(repo, "checkout", "main")

    result = _merge_back(repo, "release/4.0", "v4.1.0")

    assert result.returncode == 0, result.stderr
    assert '"4.1.0"' in (repo / "server.json").read_text()
    subject = _git(repo, "log", "-1", "--format=%s")
    assert subject == "chore(release): merge release/4.0 back into main after v4.1.0"
    # A real merge commit: two parents, so the tag becomes reachable from main.
    assert len(_git(repo, "log", "-1", "--format=%P").split()) == 2


def test_version_conflicts_resolve_to_mains_side(tmp_path: Path) -> None:
    """The backport case: main has released a newer version since the cut, so
    the version-coupled files conflict and main's side must win."""
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "release/4.0")
    _write_version(repo, "4.0.1")
    _commit_all(repo, "4.0.1")
    _git(repo, "checkout", "main")
    _write_version(repo, "5.0.0")
    _commit_all(repo, "5.0.0")

    result = _merge_back(repo, "release/4.0", "v4.0.1")

    assert result.returncode == 0, result.stderr
    assert 'version = "5.0.0"' in (repo / "pyproject.toml").read_text()
    assert '"5.0.0"' in (repo / "server.json").read_text()
    assert len(_git(repo, "log", "-1", "--format=%P").split()) == 2
    # Ancestry restored: the branch tip is now reachable from main.
    _git(repo, "merge-base", "--is-ancestor", "release/4.0", "HEAD")


def test_code_conflicts_abort_cleanly(tmp_path: Path) -> None:
    """A conflict outside the version-coupled set is real divergence: the
    script must abort the merge, leave the tree clean, and exit non-zero."""
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "release/4.0")
    (repo / "app.py").write_text("x = 2  # branch\n")
    _commit_all(repo, "fix: branch-side change")
    _git(repo, "checkout", "main")
    (repo / "app.py").write_text("x = 3  # main\n")
    _commit_all(repo, "fix: main-side change")
    before = _git(repo, "rev-parse", "HEAD")

    result = _merge_back(repo, "release/4.0", "v4.0.1")

    assert result.returncode != 0
    assert "need a human" in result.stderr
    assert "app.py" in result.stderr
    # Merge aborted: no MERGE_HEAD, clean tree, main tip unchanged.
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "HEAD") == before
