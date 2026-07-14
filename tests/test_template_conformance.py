"""Template-conformance gate — the anti-excuse device for the de-fork epic #898.

Six files are fully owned by the copier template ``fastmcp-server-template``
(they are not in the template's ``_skip_if_exists`` / ``_exclude``, so a
``copier update`` re-renders them every time). Domain code in those files must
live inside a declared sentinel block or in a dedicated domain module — never in
the template-owned body. This module renders the template pristinely with this
repo's own answers and asserts each file matches the pristine render *outside*
its sentinel blocks. Any out-of-sentinel divergence is a fork.

The :data:`RATCHET` allowlist carries the files whose de-fork is still in
progress. Each de-fork PR **removes its file from RATCHET in the same PR**: that
removal flips the file from the weak "still forked" assertion to the strict
"conforms" assertion, so a PR cannot claim a de-fork unless the file actually
conforms. The list can only shrink truthfully — a file that already conforms
fails the weak assertion. When RATCHET is empty the gate is permanently strict
and blocks any new fork.

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

# Known-forked files, de-fork in progress → tracking issue. REMOVE a file here in
# the same PR that de-forks it; the removal is the proof (it flips the file to
# the strict check). Epic #898. Seeded below after measuring actual conformance.
RATCHET: dict[str, str] = {
    "_server_apps.py": "#905",
}

_START = re.compile(r"#\s*([A-Z0-9-]+)-START\b")
_END = re.compile(r"#\s*([A-Z0-9-]+)-END\b")


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


def _sentinel_names(pristine_lines: list[str]) -> set[str]:
    """Legal sentinel names = those the *pristine* render declares.

    Authoritative source: a fork cannot legitimize itself by inventing a
    sentinel the template does not ship. Raises on unbalanced markers.
    """
    names: set[str] = set()
    stack: list[str] = []
    for ln in pristine_lines:
        start = _START.search(ln)
        if start:
            stack.append(start.group(1))
            names.add(start.group(1))
            continue
        end = _END.search(ln)
        if end:
            assert stack and stack[-1] == end.group(1), (
                f"unbalanced sentinel {end.group(1)}-END in pristine render"
            )
            stack.pop()
    assert not stack, f"unclosed sentinel(s) in pristine render: {stack}"
    return names


def _strip_sentinels(lines: list[str], legal: set[str]) -> list[str]:
    """Replace each inclusive ``NAME-START..NAME-END`` block with one token.

    Marker-anchored (not index-anchored), so equal blocks line up regardless of
    interior length, block order is enforced, and marker-comment prose drift is
    tolerated. Only blocks whose name is ``legal`` are collapsed — an invented
    sentinel's content stays in the remainder and surfaces as a fork.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        start = _START.search(lines[i])
        if start and start.group(1) in legal:
            name = start.group(1)
            j = i + 1
            while j < n:
                end = _END.search(lines[j])
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
    missing = [rel for rel in FILE_MODULE_PATHS.values() if not (dest / rel).exists()]
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

    if fname in RATCHET:
        assert div is not None, (
            f"{fname} now CONFORMS to the template skeleton but is still in RATCHET "
            f"({RATCHET[fname]}). Remove it from RATCHET in THIS PR — that removal is "
            f"the proof of de-fork. Epic #898."
        )
    else:
        assert div is None, _fork_message(fname, rel, ctx.ref, div, repo)


def test_module_server_is_never_exempt(ctx: _Ctx) -> None:
    """`packaging/mcpb/src/server.py` is skip-listed; the module `server.py` must
    never be — a basename-based exemption would silently stop guarding it.
    """
    assert f"src/{MODULE}/server.py" not in ctx.exempt


def test_ratchet_only_lists_template_owned_files() -> None:
    unknown = set(RATCHET) - set(FILE_MODULE_PATHS)
    assert not unknown, f"RATCHET lists non-template-owned files: {sorted(unknown)}"


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
