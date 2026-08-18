"""Guards for the knope release-PR flow's contract (fastmcp-server-template#406).

The only release-contract suite since the Phase-2 swap deleted
python-semantic-release together with its ``tests/test_release_contract.py``:
this file guards the knope flow, which the same swap armed by removing
``[tool.semantic_release]`` from ``pyproject.toml`` (an invariant asserted
below — the workflows' interlock step refuses if the block reappears).

The invariants mirror the retired PSR suite's intent, respecified for the
release-PR model:

- ``knope.toml`` and ``scripts/stamp_manifests.py`` are two halves of one
  mechanism — knope's ``versioned_files`` own ``pyproject.toml`` alone, the
  stamp Command owns ``uv.lock``'s self-version entry (every release, PEP
  440 canonical spelling — uv re-locks would rewrite a SemVer-spelled entry
  and break knope's version-agreement requirement) and the install-channel
  manifests (stable only), and a stamp declared in one half only must fail
  here rather than ship a stale file (the #325 failure shape, carried
  over).
- The stamp script is fail-loud and atomic (the markdown-vault-mcp#1083
  lesson: no warn-and-continue): a stable version moves every published
  pin, a pre-release moves ``uv.lock`` alone and leaves the manifests
  byte-identical, and a missing or malformed pin exits non-zero naming the
  file.
- The promotion guard runs in two layers (release-vision D10): at prepare
  time between the prep commit and the push/PR steps (a drifted promotion
  never becomes a mergeable PR), and again BEFORE knope's Release step (a
  refusal must leave no tag behind).  It admits only release stamps plus
  ``docs/releases/**`` (migration M6).
- Committed pins may equal the last stable release OR the version the
  current diff prepares — a stable release PR is exactly the state the old
  tag-coupled asserts would reject, and under the release-PR model it must
  pass CI.  ``prepared`` counts only when it is itself a stable ``X.Y.Z``:
  an rc in ``pyproject.toml`` must never make an rc pin in the published
  manifests pass CI (the markdown-vault-mcp#1053 class).
- Tags-absent behavior fails loudly only when the repo demonstrably has
  releases (``CHANGELOG.md`` carries version sections) while no tags are
  visible — the template#387 failure mode is a tag-less checkout of a
  tagged repo, not a tag-less repo; a fresh render keeps its skip.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOPE_TOML = REPO_ROOT / "knope.toml"
STAMPER = REPO_ROOT / "scripts" / "stamp_manifests.py"
GUARD = REPO_ROOT / "scripts" / "promotion_guard.sh"
PREPARE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-prepare.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PLUGIN_JSON = Path(".claude-plugin/plugin/.claude-plugin/plugin.json")
MCP_JSON = Path(".claude-plugin/plugin/.mcp.json")


def _knope_config() -> dict[str, Any]:
    return tomllib.loads(KNOPE_TOML.read_text(encoding="utf-8"))


def _knope_workflow(name: str) -> dict[str, Any]:
    for workflow in _knope_config()["workflows"]:
        if workflow.get("name") == name:
            assert isinstance(workflow, dict)
            return workflow
    pytest.fail(f"knope.toml declares no workflow named {name!r}")


def _stamper_text() -> str:
    return STAMPER.read_text(encoding="utf-8")


def test_knope_versioned_files_is_pyproject_only() -> None:
    """knope owns ``pyproject.toml`` and nothing else.

    ``uv.lock`` must NOT be a versioned file: uv rewrites the self-version
    entry to the PEP 440 canonical spelling (``4.0.0rc1``) on any re-lock,
    and knope requires versioned files to agree with pyproject.toml's
    SemVer spelling (``4.0.0-rc.1``) — as a versioned file, every re-lock
    during an rc window would break the next ``PrepareRelease``.  The stamp
    script owns the lockfile entry instead (canonical spelling, every
    release), asserted behaviorally below.
    """
    files = _knope_config()["package"]["versioned_files"]
    assert files == ["pyproject.toml"], (
        f"versioned_files must be exactly ['pyproject.toml'], got: {files!r}"
    )


def test_knope_changelog_config_keeps_todays_section_titles() -> None:
    """``CHANGELOG.md`` stays the changelog; sections keep today's names.

    Order matters: knope renders sections in declaration order, and breaking
    changes must lead the changelog section and the release body.
    """
    package = _knope_config()["package"]
    assert package["changelog"] == "CHANGELOG.md"
    sections = [
        (str(s["name"]), list(s["types"])) for s in package["extra_changelog_sections"]
    ]
    assert sections == [
        ("Breaking Changes", ["major"]),
        ("Features", ["minor"]),
        ("Bug Fixes", ["patch"]),
    ]


def test_prepare_workflow_invokes_the_stamp_script() -> None:
    """The invocation-coupling assert, knope edition.

    knope's ``versioned_files`` cover only ``pyproject.toml``; ``uv.lock``'s
    self-version entry and the install-channel manifests move via the stamp
    Command.  A stamp script that exists but is never invoked (or an
    invocation whose script is gone) ships a stale lockfile and manifests
    silently — exactly the #325 failure shape.
    """
    steps = _knope_workflow("prepare-release")["steps"]
    types = [step.get("type") for step in steps]
    assert "PrepareRelease" in types
    commands = [
        str(step.get("command", "")) for step in steps if step.get("type") == "Command"
    ]
    stamp_commands = [c for c in commands if "scripts/stamp_manifests.py" in c]
    assert stamp_commands, "prepare-release never invokes scripts/stamp_manifests.py"
    assert all("$version" in c for c in stamp_commands), (
        "the stamp Command must pass knope's $version"
    )
    assert STAMPER.is_file(), f"{STAMPER} is missing"
    # The stamp runs after PrepareRelease (which computes $version) and
    # before the commit Command that stages the release commit.
    prepare_idx = types.index("PrepareRelease")
    stamp_idx = next(
        i
        for i, step in enumerate(steps)
        if "scripts/stamp_manifests.py" in str(step.get("command", ""))
    )
    commit_idx = next(
        i
        for i, step in enumerate(steps)
        if "git commit" in str(step.get("command", ""))
    )
    assert prepare_idx < stamp_idx < commit_idx


def test_prepare_workflow_runs_the_guard_between_commit_and_push() -> None:
    """The prepare-time half of the two-layer guard (release-vision D10).

    A drifted promotion must refuse at prepare time — after the prep commit
    exists (the guard reads the stamped pyproject version and diffs the
    last reachable rc against HEAD) and BEFORE the push and
    CreatePullRequest steps — so it never becomes a mergeable PR and no
    stamped branch is left behind.  The tag-release guard below stays as
    the backstop for drift landing between prepare and merge.
    """
    steps = _knope_workflow("prepare-release")["steps"]
    commands = [str(step.get("command", "")) for step in steps]
    guard_indices = [
        i for i, c in enumerate(commands) if "scripts/promotion_guard.sh" in c
    ]
    assert guard_indices, "prepare-release never invokes scripts/promotion_guard.sh"
    commit_idx = next(i for i, c in enumerate(commands) if "git commit" in c)
    push_idx = next(i for i, c in enumerate(commands) if "git push" in c)
    pr_idx = next(
        i for i, step in enumerate(steps) if step.get("type") == "CreatePullRequest"
    )
    assert commit_idx < guard_indices[0] < push_idx < pr_idx, (
        "the prepare-time guard must run after the prep commit and before "
        "the push/CreatePullRequest steps"
    )


def test_tag_workflow_runs_the_guard_before_release() -> None:
    """Guard-before-Release ordering is load-bearing (release-vision D10).

    Tags are immutable: a same-source refusal that fired after tagging would
    recreate exactly the burned-tag failure class the redesign removes —
    this is the hard backstop behind the prepare-time gate above.
    """
    steps = _knope_workflow("tag-release")["steps"]
    guard_indices = [
        i
        for i, step in enumerate(steps)
        if "scripts/promotion_guard.sh" in str(step.get("command", ""))
    ]
    assert guard_indices, "tag-release never invokes scripts/promotion_guard.sh"
    release_idx = next(
        i for i, step in enumerate(steps) if step.get("type") == "Release"
    )
    assert guard_indices[0] < release_idx, (
        "the promotion guard must run BEFORE the Release step"
    )
    assert GUARD.is_file(), f"{GUARD} is missing"


def test_pyproject_carries_no_semantic_release_block() -> None:
    """The PSR config block stays gone — the knope flow's arming condition.

    Both release workflows' interlock step refuses while a
    ``[tool.semantic_release]`` block exists, so its reappearance would not
    just resurrect dead config: it would turn every prepare dispatch and
    every release-PR merge into a refusal.  Two release models must never
    both be live (migration P1.0/P2).
    """
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    assert "semantic_release" not in data.get("tool", {}), (
        "pyproject.toml carries [tool.semantic_release] — python-semantic-"
        "release was removed in the knope swap, and the release workflows' "
        "interlock refuses while the block exists"
    )


def test_changelog_carries_the_insertion_flag() -> None:
    """``CHANGELOG.md`` keeps the ``<!-- version list -->`` insertion flag.

    Carried over from the retired PSR contract suite (template#350): the
    seeded changelog places machine-written version sections below this flag
    line, and the port-bookkeeping job in release.yml inserts a backported
    section directly beneath it.  If this project's CHANGELOG.md predates
    the flag, add the line once, by hand, where version sections should
    begin (typically after the intro prose).
    """
    flag = "<!-- version list -->"
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert flag in changelog, (
        f"CHANGELOG.md lacks the insertion flag — add this exact line once, "
        f"where version sections should be inserted: {flag}"
    )


def test_promotion_guard_allows_only_release_metadata() -> None:
    """The guard's allowed set is release stamps + ``docs/releases/**`` (M6).

    Notes pages are release metadata like the changelog section: the notes PR
    for the release being promoted legitimately lands between rc and stable,
    and a stamps-only guard would burn an rc on a docs page.
    """
    text = GUARD.read_text(encoding="utf-8")
    for allowed in ("pyproject.toml", "uv.lock", "CHANGELOG.md", "server.json"):
        assert f'"{allowed}"' in text, f"guard allowed set lost {allowed}"
    assert '".claude-plugin/plugin/.claude-plugin/plugin.json"' in text
    assert '".claude-plugin/plugin/.mcp.json"' in text
    assert "docs/releases/" in text, (
        "guard must admit docs/releases/** — notes merged during the rc "
        "window are part of the promotion diff (migration M6)"
    )
    assert ".vale/styles/config/vocabularies/" in text, (
        "guard must admit the Vale vocabulary subtree — notes PRs "
        "legitimately add accept.txt entries alongside the page (M6)"
    )


def test_domain_manifest_sentinels_present_once_and_in_scope() -> None:
    """Both seams survive as matched pairs, in the scopes downstreams extend.

    Same contract as the PSR-era bumper: helpers at module level, calls
    inside ``main()`` after the template's own stamps and before the FINAL
    git staging, so a domain manifest is committed with the release (the
    pre-release path stages the lockfile and returns before the block —
    domain manifests are stable-only, like the template's own).
    """
    text = _stamper_text()
    for name in ("DOMAIN-MANIFESTS-HELPERS", "DOMAIN-MANIFESTS"):
        assert text.count(f"# {name}-START") == 1, f"{name}-START fence missing"
        assert text.count(f"# {name}-END") == 1, f"{name}-END fence missing"
    main_def = text.index("def main(")
    start = text.index("# DOMAIN-MANIFESTS-START")
    end = text.index("# DOMAIN-MANIFESTS-END")
    assert text.index("# DOMAIN-MANIFESTS-HELPERS-START") < main_def
    assert main_def < start < end
    assert text.index("_stamp_server_json(version)") < start
    assert end < text.rindex("_git_stage(stamped)")


def test_release_workflows_are_interlocked_on_the_psr_block() -> None:
    """Both GitHub workflows refuse if the PSR block reappears (P1.0/P2).

    During the additive phase this interlock kept the knope flow inert;
    since the Phase-2 swap deleted ``[tool.semantic_release]`` it always
    passes, and it stays as the permanent swap-completeness guard: two
    release models must never both be live, so a resurrected PSR block must
    make a dispatch or a merged release PR exit non-zero before any knope
    step runs.
    """
    for workflow in (PREPARE_WORKFLOW, RELEASE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        # Anchor on the interlock STEP's name, not on the first mention of
        # semantic_release (header comments mention it too, which would make
        # the ordering assert vacuous).
        step_name = "Refuse while python-semantic-release"
        assert step_name in text, f"{workflow.name}: interlock step missing"
        interlock = text.index(step_name)
        assert "exit 1" in text[interlock:], f"{workflow.name}: interlock does not fail"
        assert interlock < text.index("knope-dev/action"), (
            f"{workflow.name}: the interlock must run before knope is even installed"
        )
        assert "fetch-tags: true" in text, (
            f"{workflow.name}: knope's version computation and the promotion "
            "guard both need release tags in the checkout"
        )


def _run_stamper(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real stamp script against ``workdir``."""
    return subprocess.run(
        [sys.executable, str(STAMPER), *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def stamp_sandbox(tmp_path: Path) -> Path:
    """A minimal repo root the stamper can rewrite without touching this repo.

    ``server.json`` and the plugin manifests are copied verbatim so the tests exercise the
    real manifest shapes.  ``pyproject.toml`` is a stand-in that must come
    through byte-identical (knope's sole versioned file, never the
    stamper's); ``uv.lock`` is a stand-in self-package entry the stamper
    rewrites, seeded with a dependency block of a different name to prove
    the rewrite targets the self entry alone.  ``git init`` gives the
    script's staging step a real index.
    """
    shutil.copy(REPO_ROOT / "server.json", tmp_path / "server.json")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sandbox-pkg"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        "[[package]]\n"
        'name = "sandbox-pkg"\n'
        'version = "1.2.3"\n'
        "\n"
        "[[package]]\n"
        'name = "other-dep"\n'
        'version = "0.5.0"\n',
        encoding="utf-8",
    )
    for manifest in (PLUGIN_JSON, MCP_JSON):
        (tmp_path / manifest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / manifest, tmp_path / manifest)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _lock_text(sandbox: Path) -> str:
    return (sandbox / "uv.lock").read_text(encoding="utf-8")


def test_stable_version_stamps_every_published_pin(stamp_sandbox: Path) -> None:
    """A stable version moves every install-channel pin plus the lockfile."""
    before_pyproject = (stamp_sandbox / "pyproject.toml").read_bytes()

    result = _run_stamper(stamp_sandbox, "9.9.9")
    assert result.returncode == 0, result.stderr

    server = json.loads((stamp_sandbox / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == "9.9.9"
    pypi = [p for p in server["packages"] if p.get("registryType") == "pypi"]
    assert pypi and all(p["version"] == "9.9.9" for p in pypi)
    oci = [p for p in server["packages"] if p.get("registryType") == "oci"]
    assert oci and all(p["identifier"].endswith(":v9.9.9") for p in oci)

    plugin = json.loads((stamp_sandbox / PLUGIN_JSON).read_text(encoding="utf-8"))
    assert plugin["version"] == "9.9.9"
    mcp = json.loads((stamp_sandbox / MCP_JSON).read_text(encoding="utf-8"))
    pins = [
        arg
        for server_cfg in mcp.values()
        for arg in server_cfg.get("args", [])
        if "==" in arg
    ]
    assert pins and all(pin.endswith("==9.9.9") for pin in pins)

    # The self entry moves; the dependency entry and pyproject.toml (knope's
    # versioned file) must come through untouched.
    lock = _lock_text(stamp_sandbox)
    assert 'name = "sandbox-pkg"\nversion = "9.9.9"' in lock
    assert 'name = "other-dep"\nversion = "0.5.0"' in lock
    assert (stamp_sandbox / "pyproject.toml").read_bytes() == before_pyproject

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=stamp_sandbox,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert re.search(r"^A .*server\.json$", status, re.MULTILINE), (
        "the stamped manifests must be staged for knope's release commit"
    )
    assert re.search(r"^A .*uv\.lock$", status, re.MULTILINE), (
        "the stamped lockfile must be staged for knope's release commit"
    )


@pytest.mark.parametrize(
    ("version", "canonical"),
    [("9.9.9-rc.1", "9.9.9rc1"), ("9.9.9-rc.12", "9.9.9rc12")],
)
def test_prerelease_stamps_only_the_lockfile_in_canonical_spelling(
    stamp_sandbox: Path, version: str, canonical: str
) -> None:
    """Pre-releases move ``uv.lock`` alone — in PEP 440 canonical spelling.

    The published manifests stay byte-identical (their pins name artifacts
    PyPI/registry/marketplace never publish for an rc), while the lockfile
    entry is stamped canonically so a later ``uv lock`` run rewrites
    nothing — uv canonicalizes ``-rc.N`` to ``rcN``, which as a knope
    versioned file used to break the next prepare.
    """
    before = {
        path: (stamp_sandbox / path).read_bytes()
        for path in ("server.json", "pyproject.toml")
    }
    before[str(PLUGIN_JSON)] = (stamp_sandbox / PLUGIN_JSON).read_bytes()
    before[str(MCP_JSON)] = (stamp_sandbox / MCP_JSON).read_bytes()

    result = _run_stamper(stamp_sandbox, version)
    assert result.returncode == 0, result.stderr
    assert "pre-release" in result.stdout

    lock = _lock_text(stamp_sandbox)
    assert f'name = "sandbox-pkg"\nversion = "{canonical}"' in lock
    assert version not in lock, "the SemVer spelling must not reach uv.lock"
    for path, content in before.items():
        assert (stamp_sandbox / path).read_bytes() == content, f"{path} was touched"


def test_stable_restamps_a_legacy_spelled_lock_entry(stamp_sandbox: Path) -> None:
    """A lock entry in either spelling is simply restamped.

    Covers both the canonical spelling a ``uv lock`` run leaves behind
    (``1.2.3rc4``) and whatever a legacy SemVer-spelled entry carries — the
    rewrite matches the entry by name, not by its current version.
    """
    lock_path = stamp_sandbox / "uv.lock"
    lock_path.write_text(
        _lock_text(stamp_sandbox).replace('version = "1.2.3"', 'version = "1.2.3rc4"'),
        encoding="utf-8",
    )
    result = _run_stamper(stamp_sandbox, "9.9.9")
    assert result.returncode == 0, result.stderr
    assert 'name = "sandbox-pkg"\nversion = "9.9.9"' in _lock_text(stamp_sandbox)


def test_lockfile_without_the_self_entry_refuses_loudly(stamp_sandbox: Path) -> None:
    """A lockfile missing the self-package entry fails the release."""
    (stamp_sandbox / "uv.lock").write_text(
        '[[package]]\nname = "other-dep"\nversion = "0.5.0"\n', encoding="utf-8"
    )
    result = _run_stamper(stamp_sandbox, "9.9.9")
    assert result.returncode != 0
    assert "uv.lock" in result.stderr
    assert "sandbox-pkg" in result.stderr, "the refusal must name the missing entry"


def test_missing_manifest_refuses_loudly(stamp_sandbox: Path) -> None:
    """No warn-and-continue: a missing manifest fails the release."""
    (stamp_sandbox / "server.json").unlink()
    result = _run_stamper(stamp_sandbox, "9.9.9")
    assert result.returncode != 0
    assert "server.json" in result.stderr


def test_unstampable_oci_pin_refuses_and_names_the_file(stamp_sandbox: Path) -> None:
    """An OCI identifier without a ``:v<tag>`` suffix refuses, atomically."""
    server_path = stamp_sandbox / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    for pkg in server["packages"]:
        if pkg.get("registryType") == "oci":
            pkg["identifier"] = pkg["identifier"].rsplit(":", 1)[0]
    server_path.write_text(
        json.dumps(server, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    before = server_path.read_bytes()

    result = _run_stamper(stamp_sandbox, "9.9.9")
    assert result.returncode != 0
    assert "server.json" in result.stderr
    assert ":v" in result.stderr, "the refusal must name the missing pattern"
    # Atomic refusal: the file on disk is untouched by the failed run.
    assert server_path.read_bytes() == before


def test_stamper_without_a_version_argument_refuses(stamp_sandbox: Path) -> None:
    """The version is knope's ``$version``; running without one is an error."""
    result = _run_stamper(stamp_sandbox)
    assert result.returncode != 0
    assert "usage" in result.stderr


def _git_tags(*extra_args: str) -> list[str] | None:
    """``v*`` release tags in this repo, or ``None`` without git.

    A freshly generated project is not yet a git repository and has no tags,
    so callers skip there rather than failing.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "tag", "--list", *extra_args],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [t for t in completed.stdout.split() if re.match(r"^v\d", t)]


def _release_tags() -> list[str] | None:
    """All ``v*`` tags fetched into this checkout, reachable or not."""
    return _git_tags()


def _reachable_stable_tags() -> list[str] | None:
    """Stable ``vX.Y.Z`` tags reachable from HEAD (``--merged``).

    Reachability is the load-bearing scope for the pins invariant: on a
    ``release/X.Y`` backport branch the repo-global newest stable belongs to
    a LATER series, and pinning against it would turn CI red on a branch
    whose manifests correctly sit at its own series' stable.
    """
    tags = _git_tags("--merged", "HEAD")
    if tags is None:
        return None
    return [t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]


def _published_pins() -> dict[str, str]:
    """Version each committed published-manifest currently pins."""
    pins: dict[str, str] = {}
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    pins["server.json version"] = str(server["version"])
    for pkg in server.get("packages", []):
        if pkg.get("registryType") == "pypi":
            pins[f"server.json pypi {pkg.get('identifier', '')}"] = str(pkg["version"])
    plugin = json.loads((REPO_ROOT / PLUGIN_JSON).read_text(encoding="utf-8"))
    pins["plugin.json version"] = str(plugin["version"])
    mcp = json.loads((REPO_ROOT / MCP_JSON).read_text(encoding="utf-8"))
    for server_cfg in mcp.values():
        for arg in server_cfg.get("args", []):
            if isinstance(arg, str) and "==" in arg:
                pins[f".mcp.json pin {arg}"] = arg.split("==", 1)[1]
    return pins


def test_committed_pins_name_the_last_stable_or_the_prepared_version() -> None:
    """Pins equal the last stable release OR the version this diff prepares.

    The release-PR intermediate state is the load-bearing half: on a stable
    release PR every pin already names the version being prepared (which has
    no tag yet), and that PR must pass CI — merging it is the release.  A
    tag-coupled "pins must name a released tag" assert would reject exactly
    that state.  Outside a release PR, pins sit at the last stable and
    ``pyproject.toml`` agrees.

    "Last stable" is REACHABILITY-scoped (highest stable tag reachable from
    HEAD), not repo-global: on a ``release/X.Y`` backport branch older than
    the newest stable, the manifests correctly pin that series' own stable,
    and a repo-global comparison would make the branch unmergeable.

    ``prepared`` widens the allowed set ONLY when it is itself a stable
    ``X.Y.Z``: on an rc release PR ``pyproject.toml`` carries the rc
    version, and admitting it would let an rc pin in the published
    manifests pass CI — naming a version PyPI, the registry, and the
    marketplace never publish (the markdown-vault-mcp#1053 class the
    stamper's rc skip exists to prevent).
    """
    pins = _published_pins()
    if pins["server.json version"] in {"0.0.0", "0.1.0"}:
        pytest.skip("initial placeholder versions, before the first stable release")
    stable_tags = _reachable_stable_tags()
    if not stable_tags:
        # Tag visibility itself is guarded by the changelog-coupled test
        # below; without reachable stable tags there is no "last stable" to
        # pin (also the shallow-checkout case, where reachability cannot be
        # computed).
        pytest.skip("no stable release tags reachable from this checkout's HEAD")
    last_stable = max(
        (t.removeprefix("v") for t in stable_tags),
        key=lambda v: tuple(int(part) for part in v.split(".")),
    )
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        prepared = str(tomllib.load(fh)["project"]["version"])
    allowed = {last_stable}
    if re.fullmatch(r"\d+\.\d+\.\d+", prepared):
        allowed.add(prepared)
    bad = {where: ver for where, ver in pins.items() if ver not in allowed}
    assert not bad, (
        f"committed pins must name the last reachable stable ({last_stable}) "
        f"or the stable version this diff prepares ({prepared}), but these "
        "do neither: "
        + ", ".join(f"{where} = {ver}" for where, ver in sorted(bad.items()))
    )


def test_release_tags_are_visible_when_the_changelog_records_releases() -> None:
    """Fail loudly on a tag-less checkout of a repo that HAS releases.

    The template#387 failure mode: CI checks out without tags, every
    tag-coupled assert silently skips, and the suite green-lights states it
    never examined.  ``CHANGELOG.md`` version sections are the
    tag-independent witness that releases exist; when they do, at least one
    ``v*`` tag must be visible — ``ci.yml``'s checkouts fetch tags for
    exactly this.  A fresh render (no version sections) keeps its skip, so
    template-ci's smoke gate stays green.
    """
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(r"(?m)^## v?\d+\.\d+\.\d+", changelog):
        pytest.skip("CHANGELOG.md records no releases yet — fresh project")
    tags = _release_tags()
    assert tags, (
        "CHANGELOG.md records releases but no v* tags are visible — this "
        "checkout was made without tags (add fetch-tags: true, template#387), "
        "or the repository lost its release tags"
    )
