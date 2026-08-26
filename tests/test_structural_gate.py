"""Structural-health gate: behavioural, wiring, and anti-drift tests.

These run inside the rendered project's suite (and any downstream's suite that
keeps the structural gate enabled), continuously verifying that this project's
own rendered gate artifacts (pre-commit hook, CI job, pyproject, AGENTS.md) are
wired correctly. Only rendered when ``enable_structural_gate`` is true.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Canonical structural ruff selection — MUST stay byte-identical to the string
# in .pre-commit-config.yaml, .github/workflows/ci.yml, and AGENTS.md. The
# anti-drift test in this module enforces that.
STRUCTURAL = "C901,PLR0911,PLR0912,PLR0913,PLR0915,S"

REPO_ROOT = Path(__file__).resolve().parents[1]

# A function whose cyclomatic complexity exceeds the mccabe default (10): a
# long if/elif chain. Written to a temp file and linted via subprocess so this
# test module itself stays simple (and never trips the gate it is testing).
OVER_COMPLEX = """
def tangled(n):
    if n == 1:
        return 1
    elif n == 2:
        return 2
    elif n == 3:
        return 3
    elif n == 4:
        return 4
    elif n == 5:
        return 5
    elif n == 6:
        return 6
    elif n == 7:
        return 7
    elif n == 8:
        return 8
    elif n == 9:
        return 9
    elif n == 10:
        return 10
    elif n == 11:
        return 11
    return 0
"""


def _ruff(args: list[str], target: Path) -> subprocess.CompletedProcess[str]:
    # --config pins THIS project's pyproject.toml. ruff otherwise discovers
    # config from the target file's directory (tmp_path here), which would test
    # ruff's defaults instead of the project's `select` — the base-config
    # assertion below must reflect the project's real selection.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(REPO_ROOT / "pyproject.toml"),
            "--output-format",
            "concise",
            *args,
            str(target),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_structural_rule_fires_only_as_overlay(tmp_path: Path) -> None:
    """C901 fires under --extend-select but NOT under the base project config.

    Guards the core invariant: strict-on-diff, lenient-on-repo. If someone
    promotes these rules into the whole-repo `select`, the base run starts
    reporting C901 and this test fails.
    """
    target = tmp_path / "snippet.py"
    target.write_text(OVER_COMPLEX)

    overlay = _ruff([f"--extend-select={STRUCTURAL}"], target)
    base = _ruff([], target)

    assert "C901" in overlay.stdout, f"overlay should flag C901:\n{overlay.stdout}"
    assert "C901" not in base.stdout, f"base config must NOT flag C901:\n{base.stdout}"


def test_security_rules_are_overlay_only(tmp_path: Path) -> None:
    """The `S` (security) family fires under --extend-select but NOT under the
    base config — the same strict-on-diff/lenient-on-repo invariant as C901, for
    the other high-stakes cluster. If `S` were promoted into the whole-repo
    `select`, the tests/** per-file-ignores would silently become load-bearing
    for the entire repo lint, not just the gate; this test fails first.
    """
    target = tmp_path / "insecure.py"
    # `assert` trips S101; the snippet lives outside tests/, so the tests/**
    # per-file-ignores do not apply and the overlay's `S` is free to fire.
    target.write_text("def check(x):\n    assert x\n    return x\n")

    overlay = _ruff([f"--extend-select={STRUCTURAL}"], target)
    base = _ruff([], target)

    assert "S101" in overlay.stdout, f"overlay should flag S101:\n{overlay.stdout}"
    assert "S101" not in base.stdout, f"base config must NOT flag S101:\n{base.stdout}"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _diff_quality(repo: Path, extend: bool) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "diff_cover.diff_quality_tool",
        "--violations=ruff.check",
        # `main`, NOT `origin/main` as the rendered pre-push hook and CI use:
        # the tmp_path repo this drives is `git init`-ed with no remote, so
        # `origin/main` does not resolve. Do not "fix" this to match the hook.
        "--compare-branch=main",
        "--fail-under=100",
    ]
    if extend:
        # EXACTLY the argv element a shell produces from the hook's
        # --options="--extend-select=..." — this is the splitting under test.
        cmd.append(f"--options=--extend-select={STRUCTURAL}")
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True)


def test_options_reach_ruff_and_gate_is_diff_scoped(tmp_path: Path) -> None:
    """A new over-complex function fails the gate ONLY with the structural
    overlay passed through diff-quality --options; a clean base run passes.

    Proves three things at once: (1) --options actually reaches ruff,
    (2) the diff scoping works, (3) without the overlay the same diff passes.
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    (repo / "base.py").write_text("x = 1\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)

    _git(["checkout", "-b", "feature"], repo)
    (repo / "tangled.py").write_text(OVER_COMPLEX)
    _git(["add", "."], repo)
    _git(["commit", "-m", "add tangled"], repo)

    with_overlay = _diff_quality(repo, extend=True)
    without_overlay = _diff_quality(repo, extend=False)

    assert with_overlay.returncode != 0, (
        "structural overlay must fail the diff gate:\n"
        f"{with_overlay.stdout}\n{with_overlay.stderr}"
    )
    assert without_overlay.returncode == 0, (
        "base ruff (no structural overlay) must pass the same diff:\n"
        f"{without_overlay.stdout}\n{without_overlay.stderr}"
    )


def _structural_hook_block(text: str) -> str:
    """The `structural-diff-gate` hook's own YAML block, sliced out of the config.

    Whole-file `in text` checks are too weak here: `types: [python]`,
    `language: system` and friends also appear in the ruff/mypy hooks, so
    dropping one from THIS hook would still leave it present elsewhere and the
    assertion would pass. Slice from this hook's `- id:` to the next one and
    assert inside that window instead.
    """
    start = text.index("- id: structural-diff-gate")
    # End at whichever comes first: the next hook, or the next repo block (this
    # hook is currently last in `- repo: local`, so the repo boundary is the
    # real terminator today).
    ends = [
        pos
        for marker in ("\n      - id:", "\n  - repo:")
        if (pos := text.find(marker, start)) != -1
    ]
    return text[start : min(ends)] if ends else text[start:]


def test_precommit_config_wires_pre_push_hook() -> None:
    text = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    assert "default_install_hook_types: [pre-commit, pre-push]" in text
    assert "id: structural-diff-gate" in text

    block = _structural_hook_block(text)
    assert "stages: [pre-push]" in block
    assert "language: system" in block
    # Mirrors the script's no-Python skip: no Python in the push → hook does
    # not run.
    assert "types: [python]" in block
    # The hook delegates to the shared script (one implementation for hook and
    # CI); the diff-quality command itself is asserted on the script below.
    assert "entry: bash scripts/structural_gate.sh" in block


def test_ci_has_structure_job() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "structure:" in text
    # CI runs the same shared script as the pre-push hook, pinning the compare
    # branch to the PR's real base via the script's env override.
    assert "scripts/structural_gate.sh" in text
    assert "STRUCTURAL_GATE_BASE" in text
    # PR-only, like the diff-cover patch-coverage steps.
    assert "github.event_name == 'pull_request'" in text


def test_claudemd_documents_the_gate_command() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text()
    assert "diff-quality" in text
    assert f"--extend-select={STRUCTURAL}" in text
    # Advisory audit + eyes content present. Split, not `and`-joined, so a
    # failure names which tool went missing instead of a bare AssertionError.
    assert "radon" in text
    assert "vulture" in text
    assert "Why it compounds" in text  # decay-issue template marker


def test_structural_select_string_is_identical_across_surfaces() -> None:
    """The select string lives in the shared script (which the pre-commit hook
    and the CI job both execute) and in AGENTS.md's documented command; drift
    between them is a silent gate weakening. Assert both carry the canonical
    string verbatim.
    """
    needle = f"--extend-select={STRUCTURAL}"
    script = (REPO_ROOT / "scripts" / "structural_gate.sh").read_text()
    claudemd = (REPO_ROOT / "AGENTS.md").read_text()
    for name, text in [("structural_gate.sh", script), ("AGENTS.md", claudemd)]:
        assert needle in text, f"{name} missing canonical structural select"


def _print_base(repo: Path, env: dict[str, str] | None = None) -> str:
    """Run the script's base-derivation seam and return the resolved base."""
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "structural_gate.sh")],
        cwd=repo,
        env={**os.environ, "STRUCTURAL_GATE_PRINT_BASE": "1", **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _commit(repo: Path, name: str, stamp: str) -> None:
    (repo / name).write_text(name + "\n")
    _git(["add", "."], repo)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", name],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        },
    )


def test_gate_script_derives_compare_branch(tmp_path: Path) -> None:
    """Base derivation: a branch off main compares against origin/main; a
    backport branch off release/X.Y compares against origin/release/X.Y (the
    branch its PR targets); an explicit STRUCTURAL_GATE_BASE always wins.

    The remote-tracking refs are created directly with update-ref — the
    derivation reads refs/remotes/origin/*, not a live remote.
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _commit(repo, "c1.py", "2026-01-01T10:00:00")
    _commit(repo, "c2.py", "2026-01-02T10:00:00")
    _git(["update-ref", "refs/remotes/origin/main", "HEAD"], repo)

    # Feature branch off main: only origin/main exists -> origin/main.
    _git(["checkout", "-b", "feature"], repo)
    _commit(repo, "f1.py", "2026-01-05T10:00:00")
    assert _print_base(repo) == "origin/main"

    # Cut release/4.0 from main's tip, advance it (newer merge-base than
    # main's for anything branched off it), publish as a remote ref.
    _git(["checkout", "-b", "release/4.0", "main"], repo)
    _commit(repo, "r1.py", "2026-01-03T10:00:00")
    _git(["update-ref", "refs/remotes/origin/release/4.0", "HEAD"], repo)

    # Backport branch off the release branch -> origin/release/4.0.
    _git(["checkout", "-b", "backport", "release/4.0"], repo)
    _commit(repo, "b1.py", "2026-01-04T10:00:00")
    assert _print_base(repo) == "origin/release/4.0"

    # The feature branch still resolves to origin/main: both merge-bases land
    # on the same cut commit, and the tie deliberately prefers origin/main...
    _git(["checkout", "feature"], repo)
    assert _print_base(repo) == "origin/main"
    # ...and the explicit override beats any derivation.
    assert (
        _print_base(repo, {"STRUCTURAL_GATE_BASE": "origin/somewhere"})
        == "origin/somewhere"
    )
