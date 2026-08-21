"""Template-conformance gate — the anti-excuse device for the de-fork epic #898.

Six files are fully owned by the copier template ``fastmcp-server-template``
(they are not in the template's ``_skip_if_exists`` / ``_exclude``, so a
``copier update`` re-renders them every time). Domain code in those files must
live inside a declared sentinel block or in a dedicated domain module — never in
the template-owned body. This module renders the template pristinely with this
repo's own answers and asserts each file matches the pristine render *outside*
its sentinel blocks. Any out-of-sentinel divergence is a fork.

Epic #898 is complete: all six template-owned files conform, so :data:`RATCHET`
is empty (asserted by :func:`test_ratchet_is_empty`) and the gate is
**unconditionally strict** — every file in :data:`FILE_MODULE_PATHS` must match
the pristine render outside its sentinel blocks, and any new fork fails CI.

During the epic, :data:`RATCHET` carried the files whose de-fork was still in
progress: each was held to a weak "still forked" check until its de-fork PR
removed it, the removal being the proof of conformance. That weak branch is
gone now that the list is empty; should the template ever add a new owned file
needing a staged de-fork, reintroduce the weak check alongside the entry.

Render uses ``copier copy --trust --skip-tasks`` — ``--trust`` because the
template declares tasks (copier refuses otherwise), ``--skip-tasks`` so the
``vendor_spa`` post-task never runs (no network, no ``app.html`` fabrication).
Only the ``.py`` files are compared, and copier writes them before tasks run.
A render failure is a **loud skip**, never a silent pass.
"""

from __future__ import annotations

import re
import subprocess
import sys
from itertools import zip_longest
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = "markdown_vault_mcp"
ANSWERS = REPO_ROOT / ".copier-answers.yml"

# Fully template-owned files → their repo-relative paths. Match on the full
# path (never the basename): the template skip-lists ``packaging/mcpb/src/server.py``,
# which must NOT exempt the module ``server.py`` (guarded below).
FILE_MODULE_PATHS = {
    "config.py": f"src/{MODULE}/config.py",
    "server.py": f"src/{MODULE}/server.py",
    "_server_deps.py": f"src/{MODULE}/_server_deps.py",
    "_server_apps.py": f"src/{MODULE}/_server_apps.py",
    "__init__.py": f"src/{MODULE}/__init__.py",
    "cli.py": f"src/{MODULE}/cli.py",
}

# Template-owned NON-Python files (#941, #942 — the README/ci.yml tail of the
# #898 de-fork). README.md is hybrid: DOMAIN blocks (HTML-comment sentinels)
# are ours and the GENERATED-ENV-TABLE-* regions belong to the config
# generator; everything else must match the pristine render. ci.yml declares
# no sentinels, so the whole file is compared (its former domain step lives in
# the domain-owned spa-source-check.yml workflow now). Compared with
# blank-line-only normalization — the import-dropping rule is Python-specific.
NON_PY_FILE_PATHS = {
    "README.md": "README.md",
    "ci.yml": ".github/workflows/ci.yml",
}

# Empty — epic #898 is complete and every template-owned file is held to the
# strict conformance check. Asserted empty by ``test_ratchet_is_empty``; adding
# a file here does nothing on its own (the weak-check branch was removed at
# close-out), so a genuine future staged de-fork must reintroduce that branch.
RATCHET: dict[str, str] = {}

_START = re.compile(r"#\s*([A-Z0-9-]+)-START\b")
_END = re.compile(r"#\s*([A-Z0-9-]+)-END\b")

# Markdown sentinels are HTML comments: `<!-- DOMAIN-START -->` and the
# config generator's `<!-- GENERATED-ENV-TABLE-*-START — ... -->` markers.
# One regex pair covers both; the generated regions collapse on both sides,
# so the repo's filled tables compare equal to the pristine render's empty
# ones (the generator, not the template, owns their interior).
_MD_START = re.compile(r"<!--\s*([A-Z0-9-]+)-START\b")
_MD_END = re.compile(r"<!--\s*([A-Z0-9-]+)-END\b")


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested below so the strict path is covered regardless of
# how many files are still on the RATCHET).
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> list[str]:
    """Minimal normalization applied identically to both sides.

    Strips trailing whitespace and one-or-more trailing blank lines, normalizes
    CRLF→LF. Deliberately does NOT sort imports or tolerate docstring drift —
    both are forks that cause ``copier update`` churn.
    """
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _sentinel_names(
    pristine_lines: list[str],
    start_re: re.Pattern[str] = _START,
    end_re: re.Pattern[str] = _END,
) -> set[str]:
    """Legal sentinel names = those the *pristine* render declares.

    Authoritative source: a fork cannot legitimize itself by inventing a
    sentinel the template does not ship. Raises on unbalanced markers.
    """
    names: set[str] = set()
    stack: list[str] = []
    for ln in pristine_lines:
        start = start_re.search(ln)
        if start:
            stack.append(start.group(1))
            names.add(start.group(1))
            continue
        end = end_re.search(ln)
        if end:
            assert stack and stack[-1] == end.group(1), (
                f"unbalanced sentinel {end.group(1)}-END in pristine render"
            )
            stack.pop()
    assert not stack, f"unclosed sentinel(s) in pristine render: {stack}"
    return names


def _strip_sentinels(
    lines: list[str],
    legal: set[str],
    start_re: re.Pattern[str] = _START,
    end_re: re.Pattern[str] = _END,
) -> list[str]:
    """Replace each inclusive ``NAME-START..NAME-END`` block with one token.

    Marker-anchored (not index-anchored), so equal blocks line up regardless of
    interior length, block order is enforced, and marker-comment prose drift is
    tolerated. Only blocks whose name is ``legal`` are collapsed — an invented
    sentinel's content stays in the remainder and surfaces as a fork.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        start = start_re.search(lines[i])
        if start and start.group(1) in legal:
            name = start.group(1)
            j = i + 1
            while j < n:
                end = end_re.search(lines[j])
                if end and end.group(1) == name:
                    break
                j += 1
            if j < n:  # matched END found → collapse the inclusive block
                out.append(f"@@SENTINEL:{name}@@")
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return out


def _significant(lines: list[str]) -> list[str]:
    """Drop blank lines and import statements before comparison.

    Blank lines are formatting (ruff-governed) and imports are the template's
    sanctioned domain-extension surface — ``config.py``'s own skeleton comment
    tells you to add ``from pathlib import Path`` for domain fields, and the
    template's ``test_config_wizard_drift`` gate requires ``from_env`` to
    reference the domain sub-configs (which need imports). Neither is structure,
    so ignoring them keeps this gate from false-flagging a conformant file.
    Anything else outside a sentinel is still strictly compared: an import is
    inert, and any *use* of it outside a sentinel is caught. A ``;`` on the
    import (or on a multi-line import's closing-paren line) means a second
    statement is riding along — the only way to smuggle code onto an import
    line in valid Python — so such lines are NOT dropped and still compared.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped == "":
            i += 1
            continue
        if stripped.startswith(("import ", "from ")) and ";" not in lines[i]:
            if "(" in lines[i] and ")" not in lines[i]:
                # Parenthesized multi-line import: find its closing-paren line.
                j = i + 1
                while j < n and ")" not in lines[j]:
                    j += 1
                if j < n and ";" not in lines[j]:
                    i = j + 1  # pure import block → drop it
                    continue
                # unclosed, or a smuggled statement after ')' → keep + compare
            else:
                i += 1  # pure single-line import → drop it
                continue
        out.append(lines[i])
        i += 1
    return out


def _significant_text(lines: list[str]) -> list[str]:
    """Blank-line-only significance filter for non-Python files.

    The import-dropping in :func:`_significant` is a Python-specific
    allowance; Markdown prose or YAML starting with ``from `` / ``import ``
    must still be compared, so only blank lines are dropped here.
    """
    return [ln for ln in lines if ln.strip() != ""]


def _first_divergence(
    repo_lines: list[str], pristine_lines: list[str]
) -> tuple[int, str | None, str | None] | None:
    """Return ``(index, repo_line, pristine_line)`` of the first mismatch, or None."""
    for idx, (r, p) in enumerate(zip_longest(repo_lines, pristine_lines)):
        if r != p:
            return idx, r, p
    return None


def _fork_message(
    fname: str,
    rel: str,
    ref: str,
    div: tuple[int, str | None, str | None],
    repo_lines: list[str],
) -> str:
    _, repo_line, pristine_line = div
    # Map the divergence back to a real source line: the compared sequence is
    # sentinel-collapsed and blank/import-stripped, so its index is not a file
    # line. Locate the offending repo line's text in the normalized repo file
    # instead (first occurrence). When repo_line is None the repo is missing a
    # line the skeleton renders.
    if repo_line is not None and repo_line in repo_lines:
        loc = f"{rel}:{repo_lines.index(repo_line) + 1}"
    else:
        loc = f"{rel} (repo is missing a line the skeleton renders)"
    return (
        f"FORK DETECTED at {loc} (template-owned, ref={ref}).\n"
        f"  Out-of-sentinel divergence:\n"
        f"      repo:     {repo_line!r}\n"
        f"      pristine: {pristine_line!r}\n"
        f"  This is OUTSIDE every sentinel block, so `copier update` will overwrite "
        f"it (or cause merge churn). Fix by reverting to pristine, moving the change "
        f"INTO a sentinel block, or relocating it to a domain module. Epic #898. "
        f"Do NOT add {fname} to RATCHET to silence this unless it is a genuine "
        f"work-in-progress fork tracked by an issue."
    )


# --------------------------------------------------------------------------- #
# Pristine render (session-scoped) + exemption discovery.
# --------------------------------------------------------------------------- #
def _answers() -> dict:
    return yaml.safe_load(ANSWERS.read_text())


def _template_git_dir(tmp_path_factory: pytest.TempPathFactory, src_url: str) -> Path:
    """A local git checkout of the template — the sibling checkout if present
    (offline-friendly), else a blobless clone of ``_src_path``.

    When no sibling checkout exists the clone needs network. Probe the remote
    first with a short timeout so an offline environment skips in seconds
    (loudly) instead of stalling on the multi-minute clone/fetch timeouts.
    """
    local = REPO_ROOT.parent / "fastmcp-server-template"
    if (local / ".git").is_dir():
        return local
    dest = tmp_path_factory.mktemp("template")
    try:
        subprocess.run(  # pragma: no cover — clone path only runs without a sibling
            ["git", "ls-remote", "--exit-code", src_url, "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        subprocess.run(
            ["git", "clone", "--filter=blob:none", src_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--tags"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(
            "template-conformance gate: cannot obtain the template — SKIPPED, "
            f"not a pass ({exc})"
        )
    return dest


def _ensure_ref(template_git: Path, ref: str) -> None:
    """Guarantee ``ref`` resolves in ``template_git``; fetch once if not.

    A persistent sibling checkout (the documented template-reconciliation
    workflow) can lag ``.copier-answers.yml``'s ``_commit`` after a bump. Rather
    than let a later ``git show <ref>:...`` hard-error, fetch once and, if the
    ref is still absent, skip loudly — never a silent pass or an ugly traceback.
    """

    def _resolves() -> bool:
        return (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(template_git),
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"{ref}^{{commit}}",
                ],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )

    if _resolves():
        return
    try:  # pragma: no cover — only when a local checkout lags the pinned ref
        subprocess.run(
            ["git", "-C", str(template_git), "fetch", "--tags"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(
            f"template-conformance gate: cannot fetch template ref {ref} — "
            f"SKIPPED, not a pass ({exc})"
        )
    if not _resolves():  # pragma: no cover
        pytest.skip(
            f"template-conformance gate: template ref {ref} not found after "
            "fetch — SKIPPED, not a pass"
        )


def _exempt_paths(template_git: Path, ref: str, python_module: str) -> set[str]:
    """Repo-relative paths the template will not re-render (skip-listed/excluded).

    Rendered from the template ``copier.yml`` at ``ref`` — so if a file is added
    to ``_skip_if_exists`` upstream it auto-exempts here with no test edit.
    """
    raw = subprocess.run(
        ["git", "-C", str(template_git), "show", f"{ref}:copier.yml"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    cfg = yaml.safe_load(raw)
    entries = list(cfg.get("_skip_if_exists", []) or []) + list(
        cfg.get("_exclude", []) or []
    )
    # Render the one Jinja variable these path entries use, tolerant of spacing.
    var = re.compile(r"\{\{\s*python_module\s*\}\}")
    return {var.sub(python_module, str(e)) for e in entries}


class _Ctx:
    def __init__(self, render_dir: Path, exempt: set[str], ref: str) -> None:
        self.render_dir = render_dir
        self.exempt = exempt
        self.ref = ref


@pytest.fixture(scope="session")
def ctx(tmp_path_factory: pytest.TempPathFactory) -> _Ctx:
    answers = _answers()
    ref = answers["_commit"]
    template_git = _template_git_dir(tmp_path_factory, answers["_src_path"])
    _ensure_ref(template_git, ref)
    dest = tmp_path_factory.mktemp("pristine")
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "copier",
                "copy",
                "--trust",
                "--skip-tasks",
                "--defaults",
                "--overwrite",
                "--data-file",
                str(ANSWERS),
                "--vcs-ref",
                ref,
                str(template_git),
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(
            f"template-conformance gate: pristine render errored — SKIPPED, "
            f"not a pass ({exc})"
        )
    # Render success = clean exit AND every watched file present. Inferring it
    # from one file would turn a partial render into a spurious per-file FAIL.
    watched = list(FILE_MODULE_PATHS.values()) + list(NON_PY_FILE_PATHS.values())
    missing = [rel for rel in watched if not (dest / rel).exists()]
    if proc.returncode != 0 or missing:  # pragma: no cover
        pytest.skip(
            "template-conformance gate: pristine render FAILED — SKIPPED, not a "
            f"pass. rc={proc.returncode} missing={missing}\n{proc.stderr[-2000:]}"
        )
    try:
        exempt = _exempt_paths(template_git, ref, answers["python_module"])
    except (subprocess.SubprocessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(
            "template-conformance gate: cannot read template copier.yml — "
            f"SKIPPED, not a pass ({exc})"
        )
    return _Ctx(dest, exempt, ref)


# --------------------------------------------------------------------------- #
# The gate.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fname", sorted(FILE_MODULE_PATHS))
def test_template_owned_file_conforms(fname: str, ctx: _Ctx) -> None:
    rel = FILE_MODULE_PATHS[fname]
    repo_path = REPO_ROOT / rel
    pristine_path = ctx.render_dir / rel

    # Env-gated like the render/clone branches: no watched file is skip-listed
    # at the pinned ref, so this never runs in practice — pragma for parity.
    if rel in ctx.exempt:  # pragma: no cover
        assert repo_path.exists(), f"{fname} is template-exempt but missing from repo"
        assert fname not in RATCHET, f"{fname} is exempt — remove it from RATCHET"
        return

    assert pristine_path.exists(), (
        f"pristine render did not produce {rel}; gate cannot verify it"
    )
    pristine = _normalize(pristine_path.read_text())
    repo = _normalize(repo_path.read_text())
    legal = _sentinel_names(pristine)
    div = _first_divergence(
        _significant(_strip_sentinels(repo, legal)),
        _significant(_strip_sentinels(pristine, legal)),
    )

    # Unconditionally strict (epic #898 complete): every template-owned file
    # must conform outside its sentinel blocks. See test_ratchet_is_empty.
    assert div is None, _fork_message(fname, rel, ctx.ref, div, repo)


@pytest.mark.parametrize("fname", sorted(NON_PY_FILE_PATHS))
def test_non_python_template_owned_file_conforms(fname: str, ctx: _Ctx) -> None:
    """README.md / ci.yml conform to the pristine render (#941, #942).

    Same gate as the Python files, with Markdown-comment sentinels and a
    blank-line-only significance filter. For ci.yml the pristine render
    declares no sentinels, so the comparison is the strict whole file.
    """
    rel = NON_PY_FILE_PATHS[fname]
    repo_path = REPO_ROOT / rel
    pristine_path = ctx.render_dir / rel

    if rel in ctx.exempt:  # pragma: no cover — neither file is skip-listed
        assert repo_path.exists(), f"{fname} is template-exempt but missing from repo"
        return

    assert pristine_path.exists(), (
        f"pristine render did not produce {rel}; gate cannot verify it"
    )
    pristine = _normalize(pristine_path.read_text())
    repo = _normalize(repo_path.read_text())
    legal = _sentinel_names(pristine, _MD_START, _MD_END)
    div = _first_divergence(
        _significant_text(_strip_sentinels(repo, legal, _MD_START, _MD_END)),
        _significant_text(_strip_sentinels(pristine, legal, _MD_START, _MD_END)),
    )
    assert div is None, _fork_message(fname, rel, ctx.ref, div, repo)


def test_module_server_is_never_exempt(ctx: _Ctx) -> None:
    """`packaging/mcpb/src/server.py` is skip-listed; the module `server.py` must
    never be — a basename-based exemption would silently stop guarding it.
    """
    assert f"src/{MODULE}/server.py" not in ctx.exempt


def test_ratchet_only_lists_template_owned_files() -> None:
    unknown = set(RATCHET) - set(FILE_MODULE_PATHS)
    assert not unknown, f"RATCHET lists non-template-owned files: {sorted(unknown)}"


def test_ratchet_is_empty() -> None:
    """Epic #898 close-out: every template-owned file is de-forked.

    An empty RATCHET is the proof the epic is complete and the per-file gate is
    unconditionally strict. A file cannot be added here to silence a fork
    without also reintroducing the weak-check branch removed at close-out (see
    the module docstring), so this assertion keeps the gate honest.
    """
    assert RATCHET == {}, (
        f"RATCHET must stay empty post-#898 (found {sorted(RATCHET)}); the gate "
        "is unconditionally strict. Re-opening a staged de-fork requires "
        "restoring the weak-check branch, not just adding an entry here."
    )


# --------------------------------------------------------------------------- #
# Unit tests for the pure comparison logic (cover the strict path independent of
# how many files are still on the RATCHET).
# --------------------------------------------------------------------------- #
_SKELETON = [
    "x = 1",
    "# DOMAIN-WIRING-START — add wiring",
    "PLACEHOLDER",
    "# DOMAIN-WIRING-END",
    "y = 2",
]


def test_conforming_file_has_no_divergence() -> None:
    repo = [
        "x = 1",
        "# DOMAIN-WIRING-START — add wiring",
        "custom_wiring()",  # domain content inside the sentinel — allowed
        "more_wiring()",
        "# DOMAIN-WIRING-END",
        "y = 2",
    ]
    legal = _sentinel_names(_SKELETON)
    assert (
        _first_divergence(
            _strip_sentinels(repo, legal), _strip_sentinels(_SKELETON, legal)
        )
        is None
    )


def test_out_of_sentinel_change_is_a_fork() -> None:
    repo = ["x = 999", *_SKELETON[1:]]  # changed a line OUTSIDE the sentinel
    legal = _sentinel_names(_SKELETON)
    div = _first_divergence(
        _strip_sentinels(repo, legal), _strip_sentinels(_SKELETON, legal)
    )
    assert div is not None
    assert div[0] == 0 and div[1] == "x = 999"
    msg = _fork_message("f.py", "src/f.py", "v1", div, repo)
    assert "FORK DETECTED" in msg
    assert "src/f.py:1" in msg  # real source line, not the stripped-remainder index

    # repo shorter than the skeleton → repo_line is None → "missing a line" branch
    short = _first_divergence(["x = 1"], ["x = 1", "extra()"])
    assert short is not None
    assert "missing a line" in _fork_message("f.py", "src/f.py", "v1", short, ["x = 1"])


def test_invented_sentinel_content_is_not_stripped() -> None:
    """A sentinel the pristine render does not declare must not shield content."""
    repo = [
        "x = 1",
        "# DOMAIN-WIRING-START — add wiring",
        "PLACEHOLDER",
        "# DOMAIN-WIRING-END",
        "# DOMAIN-INVENTED-START",
        "sneaky()",
        "# DOMAIN-INVENTED-END",
        "y = 2",
    ]
    legal = _sentinel_names(_SKELETON)  # only DOMAIN-WIRING is legal
    div = _first_divergence(
        _strip_sentinels(repo, legal), _strip_sentinels(_SKELETON, legal)
    )
    assert div is not None  # the invented block diverges


def test_normalize_ignores_trailing_whitespace_and_blank_lines() -> None:
    assert _normalize("a  \nb\n\n\n") == _normalize("a\nb\n")


def test_significant_drops_imports_and_blank_lines() -> None:
    """An added import (single or multi-line) and blank lines are ignored."""
    skeleton = ["x = 1", "code()"]
    repo = [
        "from pathlib import Path",  # single-line domain import — dropped
        "from mod import (",  # parenthesized multi-line domain import — dropped
        "    A,",
        "    B,",
        ")",
        "",  # blank — dropped
        "x = 1",
        "",
        "code()",
    ]
    assert _first_divergence(_significant(repo), _significant(skeleton)) is None


def test_significant_keeps_use_of_import_so_a_fork_still_trips() -> None:
    """The anti-false-pass guarantee: importing is inert, but *using* the import
    outside a sentinel is still compared and surfaces as a fork.
    """
    skeleton = ["x = 1", "code()"]
    repo = [
        "from pathlib import Path",  # the import itself is dropped ...
        "x = 1",
        "SNEAKY = Path('/etc')",  # ... but this *use* is real code and must trip
        "code()",
    ]
    div = _first_divergence(_significant(repo), _significant(skeleton))
    assert div is not None
    assert div[1] == "SNEAKY = Path('/etc')"


def test_significant_does_not_drop_semicolon_smuggled_import() -> None:
    """A ``;``-joined statement riding on an import line is real code, not dropped."""
    skeleton = ["x = 1"]
    single = ["import os; smuggled()", "x = 1"]
    multi = ["from mod import (", "    A,", ") ; sneaky()", "x = 1"]
    assert _first_divergence(_significant(single), _significant(skeleton)) is not None
    assert _first_divergence(_significant(multi), _significant(skeleton)) is not None


def test_unbalanced_sentinel_raises() -> None:
    with pytest.raises(AssertionError):
        _sentinel_names(["# FOO-START", "x = 1"])  # never closed
