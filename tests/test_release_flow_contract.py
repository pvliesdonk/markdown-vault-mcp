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
NOTES_PUBLISH_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "release-notes-publish.yml"
)
DOCS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs.yml"
UNSTABLE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unstable.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COVERAGE_STATUS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "coverage-status.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
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


def test_prepare_promotes_notes_before_release_commit() -> None:
    text = KNOPE_TOML.read_text(encoding="utf-8")
    prepare = text[
        text.index('name = "prepare-release"') : text.index('name = "tag-release"')
    ]
    assert (
        prepare.index("scripts/stamp_manifests.py $version")
        < prepare.index("scripts/promote_release_notes.py $version")
        < prepare.index('git commit -m \\"chore: prepare release $version\\"')
    )


def test_release_prepare_has_no_model_or_notes_bypass() -> None:
    text = PREPARE_WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "anthropics/",
        "skip_notes",
        "full_redraft",
        "draft-notes",
        "gh pr ready --undo",
        "release-notes-draft",
    )
    assert not {token for token in forbidden if token in text}


def test_hosted_release_notes_workflow_is_absent() -> None:
    assert not (REPO_ROOT / ".github" / "workflows" / "release-notes.yml").exists()


def test_release_pr_body_assigns_freshness_to_reviewers() -> None:
    text = KNOPE_TOML.read_text(encoding="utf-8")
    assert "notes-range-end" in text
    assert "meaningful" in text
    assert "re-dispatch" in text
    assert "held as a DRAFT" not in text


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


def test_release_workflow_binds_the_prep_head_to_the_base() -> None:
    """A merged prep PR only releases when its head matches its base.

    The job-level ``if`` can only prefix-match the head, so a step must
    compute the exact prep branch for the PR's base
    (``knope/prepare/<base with slashes as dashes>``) and hard-fail on a
    mismatch — a prep PR retargeted at a different base must never be
    treated as that base's release decision.  It runs before knope is
    installed, like the interlock.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    step_name = "Refuse a prep head that does not match the PR's base"
    assert step_name in text, "head-to-base binding step missing"
    idx = text.index(step_name)
    assert 'expected="knope/prepare/${BASE_REF//\\//-}"' in text, (
        "the step must derive the expected head from the PR's base with "
        "release-prepare.yml's exact slash-to-dash mapping"
    )
    assert "exit 1" in text[idx:], "the binding step does not fail on mismatch"
    assert idx < text.index("knope-dev/action"), (
        "the binding step must run before knope is even installed"
    )


def test_prepare_collision_step_covers_tags_and_open_release_prs() -> None:
    """The prepare-time version check refuses tags AND open-PR reservations.

    A version is claimed by a repo-global tag or by another base's open
    release PR (merging it creates the tag) — the step must read each open
    ``knope/prepare/*`` PR's stamped ``pyproject.toml`` version (the same
    file release.yml derives the tag from — never the mutable PR title,
    whose retitling would silently drop a reservation), skip its own prep
    branch, and name ``override_version`` as a remedy.  The gh calls
    degrade to the tag-only check on API failure rather than failing the
    prepare.
    """
    text = PREPARE_WORKFLOW.read_text(encoding="utf-8")
    step_name = "Refuse a version that is already tagged or reserved"
    assert step_name in text, "version tag/reservation step missing"
    step = text[text.index(step_name) :]
    step = step[: step.index("- name:")] if "- name:" in step else step
    assert "gh pr list" in step, "no open-release-PR reservation query"
    assert 'startswith("knope/prepare/")' in step, (
        "the reservation query must filter to prep-branch heads"
    )
    assert "contents/pyproject.toml?ref=" in step, (
        "the reserved version must be read from each prep head's committed "
        "pyproject.toml, not from the mutable PR title"
    )
    assert "chore: prepare release " not in step, (
        "reservation parsing must not key on the mutable release-PR title"
    )
    assert '"$head" = "$PREP_BRANCH"' in step, (
        "the step must skip this dispatch's own prep branch"
    )
    assert "override_version" in step, "the refusals must name the override remedy"
    assert "|| true" in step.split("gh pr list", 1)[1], (
        "the gh call must degrade to the tag-only check on API failure"
    )


def test_prepare_concurrency_group_is_global() -> None:
    """The prepare job serializes globally — the reservation must be atomic.

    The version-reservation step reads the open release PRs BEFORE
    ``CreatePullRequest`` writes this run's own: with per-base buckets, two
    concurrent prepares on different bases computing the same version both
    pass the check and open two mergeable release PRs racing for one tag.
    One global bucket makes check-through-create atomic — the second
    prepare always sees the first's open PR.  release.yml's release job
    deliberately KEEPS per-base buckets (a backport must not serialise
    behind a trunk release); its mutable rolling channels are protected by
    their own publish-time rechecks, asserted below.
    """
    prepare = PREPARE_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"concurrency:\n\s+group: (.+)\n", prepare)
    assert match, "release-prepare.yml declares no concurrency group"
    assert match.group(1).strip() == "release-prepare", (
        "the prepare concurrency group must be the fixed string "
        f"'release-prepare' (atomic cross-base reservation), got: {match.group(1)!r}"
    )
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "group: release-${{ github.event.pull_request.base.ref }}" in release, (
        "release.yml's release job must keep its per-base concurrency bucket"
    )


def _release_job_block(name: str, next_name: str | None) -> str:
    """The text of one top-level job in release.yml, by its neighbours."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"\n  {name}:")
    end = text.index(f"\n  {next_name}:") if next_name else len(text)
    return text[start:end]


@pytest.mark.parametrize(
    ("job", "next_job", "group"),
    [
        ("publish-docker", "publish-linux-packages", "release-rolling-docker"),
        ("publish-claude-plugin", "publish-registry", "release-rolling-marketplace"),
        ("publish-registry", "port-bookkeeping", "release-rolling-registry"),
    ],
)
def test_mutable_channels_recheck_ordering_at_publish_time(
    job: str, next_job: str, group: str
) -> None:
    """Every mutable rolling channel rechecks tag ordering before writing.

    The release job's ordering outputs go stale the moment a concurrent
    stable on another base tags (release buckets are per base), and a stale
    publish finishing last would repoint a rolling channel at older
    content.  Each mutable channel therefore re-derives the answer inside
    its own publish job, serialized by a per-channel global concurrency
    group — the last writer always sees every earlier release's tag, so the
    channel converges to the true newest regardless of finish order.  The
    per-version channels (PyPI, GitHub-release assets, mcpb) stay
    mutex-free: their writes are immutable and unordered.
    """
    block = _release_job_block(job, next_job)
    assert f"group: {group}" in block, f"{job} lost its per-channel mutex"
    assert "cancel-in-progress: false" in block
    assert "id: recheck" in block, f"{job} lost its publish-time ordering recheck"
    assert "sort -V" in block, f"{job}'s recheck lost the version-ordered comparison"


def test_docker_rolling_tags_gate_on_the_recheck_not_the_release_job() -> None:
    """The rolling-tag enables read the publish-time recheck's outputs.

    Wiring them back to ``needs.release.outputs`` would resurrect the stale
    ordering race the recheck exists to close, while leaving the recheck
    step green — exactly the silent-drift shape this suite guards against.
    """
    block = _release_job_block("publish-docker", "publish-linux-packages")
    for output in ("is_latest", "is_latest_minor", "is_latest_major", "is_newest_rc"):
        needle = "enable=${{ steps.recheck.outputs." + output + " == 'true' }}"
        assert needle in block, (
            f"the rolling-tag enable for {output} must read the recheck step"
        )
    assert "enable=${{ needs.release.outputs" not in block, (
        "no rolling-tag enable may read the release job's stale-able outputs"
    )


def test_docker_rc_tag_is_ordering_gated_and_disjoint_from_latest() -> None:
    """An rc release repoints the rolling ``rc`` tag, and only that one.

    Before template#360 the rc-fed rolling tag was ``unstable`` and its
    enable was the bare ``prerelease`` flag — no ordering at all, so a
    backport rc repointed it backwards.  #360 retired the tag rather than
    fixing the gate, which left rcs with no rolling pointer whatsoever:
    ``edge`` follows ``main``, not a stabilisation branch, so "run the
    current candidate" had no answer.  ``rc`` restores the pointer *with*
    the ordering discipline ``latest`` already had.

    Two properties are asserted, both of which have been wrong in the past:

    1. The ``rc`` enable exists and reads the publish-time recheck.
    2. The two ordering families are disjoint — no stable rolling tag reads
       ``is_newest_rc`` and the ``rc`` tag reads nothing else — so a stable
       release can never move ``rc`` nor an rc move ``latest``.
    """
    block = _release_job_block("publish-docker", "publish-linux-packages")
    tag_lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("type=raw,")
    ]
    rc_lines = [line for line in tag_lines if line.startswith("type=raw,value=rc,")]
    assert len(rc_lines) == 1, (
        f"publish-docker must push exactly one rolling `rc` tag line, got: {rc_lines}"
    )
    assert "is_newest_rc" in rc_lines[0], (
        "the `rc` tag must be gated on the rc ordering output, not on the "
        f"bare prerelease flag (template#360's regression): {rc_lines[0]}"
    )
    for line in tag_lines:
        if line in rc_lines:
            continue
        assert "is_newest_rc" not in line, (
            f"a stable rolling tag must never follow the rc ordering: {line}"
        )
    for stable_output in ("is_latest", "is_latest_minor", "is_latest_major"):
        assert stable_output not in rc_lines[0], (
            f"the `rc` tag must not read {stable_output}: {rc_lines[0]}"
        )


def test_rc_ordering_compares_base_versions_not_rc_tags() -> None:
    """The rc gate never version-sorts an rc tag against a stable tag.

    ``sort -V`` does not implement semver prerelease precedence: it orders
    ``v4.0.0`` BEFORE ``v4.0.0-rc.1``.  Ranking rcs among themselves is
    fine, but comparing an rc tag directly against a stable one silently
    inverts, and would let a leftover candidate of an already-released
    version keep ``rc`` ahead of ``latest``.  The gate therefore strips
    ``-rc.N`` and compares the BASE version — this asserts that stripping
    survives, because losing it produces no error, just a wrong tag.
    """
    block = _release_job_block("publish-docker", "publish-linux-packages")
    assert "${TAG%-rc.*}" in block, (
        "the rc ordering must compare the rc's base version against the "
        "newest stable; `sort -V` inverts if given the rc tag itself"
    )


def test_marketplace_bump_targets_the_only_loadable_manifest_path() -> None:
    """The catalog bump writes ``.claude-plugin/marketplace.json``.

    Claude Code reads the marketplace manifest from that path and nowhere
    else, so a bump written to a root-level ``marketplace.json`` succeeds,
    commits, and publishes nothing anyone can install — which is what this
    job did until template#383.  The failure is silent by construction, so
    the path is asserted rather than trusted.
    """
    block = _release_job_block("publish-claude-plugin", "publish-registry")
    assert "MANIFEST: .claude-plugin/marketplace.json" in block, (
        "publish-claude-plugin must bump .claude-plugin/marketplace.json"
    )
    stray = [
        line
        for line in block.splitlines()
        if "marketplace.json" in line
        and ".claude-plugin/marketplace.json" not in line
        and not line.lstrip().startswith("#")
        and not line.lstrip().startswith("- name:")
    ]
    assert not stray, (
        f"no step may still operate on a bare root-level marketplace.json: {stray}"
    )


def test_marketplace_bump_feeds_the_catalog_readme() -> None:
    """The bump refreshes the entry's prose and regenerates the catalog README.

    The catalog's README plugin list is generated from the manifest, so the
    ``description`` is not decoration — it is the generator's input, and this
    project is where its own blurb lives.  Two ways that silently degrades:
    trimming the upsert back to version+ref, which freezes every existing
    entry's prose at whatever it was when first appended; and dropping the
    generator call, which leaves the catalog's default branch describing a
    plugin set it no longer serves (the ``scholar-mcp`` class, invisible
    because nothing there fails).  Neither produces an error, so both are
    asserted here.
    """
    block = _release_job_block("publish-claude-plugin", "publish-registry")
    assert ".description = $desc" in block, (
        "the upsert must refresh the entry's description, not only its pin — "
        "the catalog README is generated from it"
    )
    assert "scripts/gen_readme.py" in block, (
        "the bump must run the catalog's README generator, or the catalog's "
        "default branch goes stale after every release"
    )


def test_publish_workflow_skips_release_pr_merges() -> None:
    """The release owns its own docs deploy (template#421).

    A release PR's merge changes ``docs/releases/`` and would trigger this
    workflow's redeploy concurrently with the release — which resolves the
    series' newest tag BEFORE the new tag exists and could overwrite the
    fresh deploy with the previous tag's content.  Pushes whose head is a
    release-PR merge (the pinned squash subject) must be skipped wholesale.
    """
    publish = NOTES_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "/pulls" in publish and 'startswith("knope/prepare/")' in publish, (
        "release-merge detection must be merge-strategy independent — the "
        "commits API associates the pushed head with its PR under squash, "
        "merge commit, and rebase alike"
    )
    assert "^chore: prepare release " in publish, (
        "the squash-subject check must remain as the degraded fallback for "
        "an API failure"
    )
    assert "pull-requests: read" in publish, (
        "the commits-to-PRs lookup needs the pull-requests read permission — "
        "a job-level permissions block zeroes everything unspecified, and "
        "without it the gate always degrades to the squash-only fallback"
    )
    gate_idx = publish.index("^chore: prepare release ")
    pages_idx = publish.index("Find changed release pages")
    assert gate_idx < pages_idx, "the gate must run before page detection"
    assert "steps.gate.outputs.skip != 'true'" in publish, (
        "page detection must be conditioned on the gate"
    )


def test_notes_publish_ignores_next_only_changes() -> None:
    text = NOTES_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    pages = text[text.index("Find changed release pages") :]
    assert "next\\.md" in pages


def test_next_notes_are_excluded_from_published_docs() -> None:
    text = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "releases/next.md" in text
    assert "releases/*.md" not in text
    assert "releases/[0-9]*.[0-9]*.md" in text


def test_pending_marker_machinery_is_gone() -> None:
    """No deferred body upgrade, no default-branch overlay at release time.

    The page lives in the tagged tree, so release.yml composes the body —
    summary block and deep link — from the local checkout at publish time,
    docs deploys build the tag as-is, and the publish workflow only
    redeploys docs when a released page is edited later.  Any reappearance
    of the marker or the overlay means the post-hoc model is creeping
    back.
    """
    marker = "release-notes-pending"
    for workflow in (RELEASE_WORKFLOW, NOTES_PUBLISH_WORKFLOW, DOCS_WORKFLOW):
        assert marker not in workflow.read_text(encoding="utf-8"), (
            f"{workflow.name} resurrects the {marker} marker"
        )
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "RELEASE-SUMMARY" in release, (
        "the release body must take its summary from the page in the tag"
    )
    docs = DOCS_WORKFLOW.read_text(encoding="utf-8")
    assert "Overlay release-notes pages" not in docs, (
        "the release-deploy overlay is dead — the tag is the source of truth"
    )
    publish = NOTES_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "--notes-file" not in publish, (
        "the publish workflow must not rewrite release bodies any more — "
        "its jobs are the release-merge gate and the docs redeploy for "
        "later page edits"
    )


def test_rc_release_body_reads_the_stable_summary_marker() -> None:
    """RC bodies find the stable marker written into the tagged page."""
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'marker_tag="v${ver%%-*}"' in release, (
        "the body step must normalise the tag to the stable version before "
        "the marker lookup — the skill keys the summary block on vX.Y.Z"
    )
    assert 'awk -v tag="$marker_tag"' in release, (
        "the summary extraction must search with the normalised marker tag, "
        "not the literal (possibly -rc.N) release tag"
    )


def test_port_bookkeeping_carries_the_notes_page() -> None:
    """A branch-cut stable's page reaches the default branch like the changelog.

    The pages for all releases accumulate on the default branch (the docs
    site reads them there); a release cut from release/X.Y lands its page
    on that branch only, so the port PR must carry it over alongside the
    changelog section.
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    port = text[text.index("port-bookkeeping:") :]
    assert 'page="docs/releases/${minor}.md"' in port, (
        "the port step no longer stages the release-notes page"
    )
    assert "released-page.md" in port
    assert "RELEASE-PAGES-START" in port and "index-entry.md" in port, (
        "a first-of-minor branch cut must port its index ENTRY too — by "
        "insertion, never wholesale copy (a backport branch's index "
        "predates newer minors on the default branch)"
    )


def _run_guard(workdir: Path) -> subprocess.CompletedProcess[str]:
    """Run the real promotion guard against ``workdir``."""
    return subprocess.run(
        ["bash", str(GUARD)],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def guard_sandbox(tmp_path: Path) -> Path:
    """A tagged repo promoting stable 9.9.9 over its rc.1.

    One commit holding ``pyproject.toml`` (already stamped stable — the
    guard reads the version being promoted from it) plus a source file,
    tagged as the rc; tests then land post-rc commits and run the guard.
    """

    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "guard@test")
    run("git", "config", "user.name", "guard")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.9.9"\n', encoding="utf-8"
    )
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "rc state")
    run("git", "tag", "v9.9.9-rc.1")
    return tmp_path


def test_promotion_guard_admits_a_notes_page_after_the_rc(
    guard_sandbox: Path,
) -> None:
    """A ``docs/releases/`` page landing between rc and stable passes (M6)."""
    notes = guard_sandbox / "docs" / "releases" / "9.9.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# 9.9\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"], cwd=guard_sandbox, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "docs: notes"],
        cwd=guard_sandbox,
        check=True,
        capture_output=True,
    )
    result = _run_guard(guard_sandbox)
    assert result.returncode == 0, result.stderr


def test_promotion_guard_sees_both_sides_of_a_rename(guard_sandbox: Path) -> None:
    """A file MOVED into an allowed subtree still refuses the promotion.

    With git's default rename detection, ``git diff --name-only`` reports
    only the destination path — so relocating source content into
    ``docs/releases/`` would read as an allowed notes change while the
    promotion silently deletes the source path.  The guard disables rename
    detection so the delete side is judged on its own.
    """
    releases = guard_sandbox / "docs" / "releases"
    releases.mkdir(parents=True)
    subprocess.run(
        ["git", "mv", "module.py", "docs/releases/module.py"],
        cwd=guard_sandbox,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "sneaky move"],
        cwd=guard_sandbox,
        check=True,
        capture_output=True,
    )
    result = _run_guard(guard_sandbox)
    assert result.returncode != 0, (
        "the guard admitted a rename out of the source tree: "
        f"{result.stdout}{result.stderr}"
    )
    assert "module.py" in result.stderr, "the refusal must name the vanished path"


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


def test_docker_cache_export_cannot_fail_a_build() -> None:
    """Every gha cache export is best-effort, never a build gate.

    The Release workflow runs on pull_request closed, whose Actions cache
    token has no writable scopes — a plain ``cache-to: type=gha`` then
    hard-fails an otherwise successful image build (the v4.0.0-rc.2
    regression).  The unstable channel gets the same treatment: the cache
    is an optimization, never a correctness input.
    """
    for workflow in (RELEASE_WORKFLOW, UNSTABLE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        assert "cache-to: type=gha,mode=max,ignore-error=true" in text, (
            f"{workflow.name} lost the best-effort cache export"
        )
        assert "cache-to: type=gha,mode=max\n" not in text, (
            f"{workflow.name} carries a cache export that can fail the build"
        )


def test_pypi_publishes_prereleases_too() -> None:
    """publish-pypi runs for rcs, gated on ``released`` alone.

    The .mcpb bundle is a pointer to PyPI, not a self-contained bundle:
    ``packaging/mcpb/pyproject.toml.in`` pins ``<pkg>[all]==<version>`` and
    the manifest launches ``uvx --from`` that same spec.  While rcs were
    held back from PyPI, every rc bundle packed, validated and uploaded
    green and then failed at install time in front of the tester —
    ``no version of <pkg>[all]==X.Y.ZrcN`` — so the candidate could not be
    tested through the channel the candidate exists to test.

    Re-gating this job on ``is_prerelease`` would restore that, silently:
    the release stays green and only a human installing the bundle finds
    out.  Hence the assertion on the gate rather than on an artifact.

    Exposure is bounded by the resolver, not by this gate: PEP 440 keeps
    pre-releases out of a resolve unless the requirement pins one or
    ``--pre`` is passed.  The surfaces that would show a candidate to
    everyone are the rolling ones, and those are asserted stable-gated by
    the marketplace/registry/docs tests elsewhere in this suite.
    """
    block = _release_job_block("publish-pypi", "publish-docker")
    assert "if: needs.release.outputs.released == 'true'\n" in block, (
        "publish-pypi must gate on released alone"
    )
    assert "is_prerelease" not in block, (
        "publish-pypi must not skip prereleases — an rc that never reaches "
        "PyPI ships an uninstallable .mcpb bundle"
    )


# The publisher runs `twine check` before upload, and twine learned
# `Metadata-Version: 2.5` in 7.0.0.  v1.14.2 is the first release bundling
# it; v1.14.0 and v1.14.1 both ship twine 6.1.0.
_PUBLISHER_TWINE7_FLOOR = (1, 14, 2)


def _hatchling_requirement() -> str:
    """The single hatchling entry in ``[build-system] requires``."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires = data["build-system"]["requires"]
    hatchling = [
        r for r in requires if r.split("[")[0].strip().lower().startswith("hatchling")
    ]
    assert len(hatchling) == 1, (
        f"expected exactly one hatchling requirement, got: {requires!r}"
    )
    return str(hatchling[0])


def test_build_backend_is_bounded_at_the_minor() -> None:
    """hatchling carries a floor AND a ceiling that binds at the next minor.

    The metadata version hatchling emits and the twine the publisher bundles
    are one invariant across two files.  Left unpinned, this side moves on
    hatchling's release schedule rather than on anyone's decision: 1.32
    started emitting ``Metadata-Version: 2.5``, the pinned publisher's twine
    refused it, and `publish-pypi` broke for every project at once, at
    release time, stable and candidate alike (template#479).

    The ceiling has to bind at the MINOR, because that is where the metadata
    version moves — 1.27 took it to 2.4, 1.32 took it to 2.5.  A ``<2``
    ceiling would have stopped neither; it reads like a guard and holds
    nothing.  Patch releases stay open so bug fixes flow; crossing a minor
    is a deliberate edit, paired with a publisher that accepts what the new
    minor emits.
    """
    requirement = _hatchling_requirement()
    floor = re.search(r">=\s*(\d+)\.(\d+)", requirement)
    ceiling = re.search(r"<\s*(\d+)\.(\d+)", requirement)
    assert floor is not None, (
        f"hatchling needs a >= floor, got: {requirement!r} — an unbounded "
        "backend picks up a new metadata version on its own schedule"
    )
    assert ceiling is not None, (
        f"hatchling needs a < ceiling, got: {requirement!r} — without one, "
        "the next metadata bump breaks publish-pypi in every project at once"
    )
    major, minor = int(floor.group(1)), int(floor.group(2))
    expected = (major, minor + 1)
    actual = (int(ceiling.group(1)), int(ceiling.group(2)))
    assert actual == expected, (
        f"hatchling ceiling must bind at the next minor "
        f"(<{expected[0]}.{expected[1]}), got: {requirement!r}. "
        "Metadata versions move on hatchling minors, so a looser ceiling "
        "lets the next bump through unnoticed."
    )


def test_pypi_publisher_understands_current_metadata() -> None:
    """The pinned publisher is new enough to accept what the backend emits.

    gh-action-pypi-publish runs ``twine check`` on every file before upload,
    using the twine it bundles.  Below v1.14.2 that is twine 6.1.0, which
    rejects a 2.5 wheel with "'2.5' is not a valid metadata version" — the
    upload never starts, so nothing reaches PyPI and no version is burned,
    but no release publishes either.

    Asserted on the version comment beside the SHA because that is what a
    human reads when bumping.  Upstream states the floor in its own
    requirements: "v7 is needed to support metadata v2.5 including PEP 794".
    """
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    pinned = re.search(
        r"pypa/gh-action-pypi-publish@([0-9a-f]{40})\s*#\s*v(\d+)\.(\d+)\.(\d+)",
        text,
    )
    assert pinned is not None, (
        "release.yml must pin gh-action-pypi-publish to a 40-char SHA with a "
        "trailing version comment — the comment is the only readable record "
        "of which twine the publisher bundles"
    )
    version = (int(pinned.group(2)), int(pinned.group(3)), int(pinned.group(4)))
    assert version >= _PUBLISHER_TWINE7_FLOOR, (
        f"gh-action-pypi-publish v{version[0]}.{version[1]}.{version[2]} bundles "
        "twine < 7, which rejects Metadata-Version 2.5; v1.14.2 or newer is "
        "required or publish-pypi fails on every release"
    )


def test_plugin_zip_attaches_to_prereleases_too() -> None:
    """build-plugin-zip runs for rcs, gated on ``released`` alone.

    The zip is the plugin channel's only marketplace-free install path:
    ``claude plugin install`` resolves names through marketplaces and takes
    no path, URL or archive, so ``claude --plugin-url <asset>`` is how a
    candidate's plugin gets exercised before it ships.  An asset that only
    exists on stable tags cannot serve that purpose.

    The rolling marketplace entry is the surface that stays stable-only, and
    ``test_marketplace_bump_targets_the_only_loadable_manifest_path`` and its
    neighbours own that assertion.
    """
    block = _release_job_block("build-plugin-zip", "publish-plugin-zip")
    assert "if: needs.release.outputs.released == 'true'\n" in block, (
        "build-plugin-zip must gate on released alone"
    )
    assert "is_prerelease" not in block, (
        "build-plugin-zip must not skip prereleases — an rc plugin that is "
        "never packed cannot be installed for testing"
    )


def test_plugin_zip_is_built_by_the_shared_composite_everywhere() -> None:
    """Every caller packs the zip through .github/actions/build-plugin-zip.

    Three workflows produce this artifact — the release, the rolling edge
    channel, and the pre-release dispatch — and the whole point of the
    composite is that the two rehearsal paths cannot drift from what a real
    release executes.  An inlined `zip -r` in any of them would pack an
    archive nothing had vendored or verified.
    """
    workflows = REPO_ROOT / ".github" / "workflows"
    for name in ("release.yml", "unstable.yml", "pre-release-check.yml"):
        text = (workflows / name).read_text(encoding="utf-8")
        assert "uses: ./.github/actions/build-plugin-zip" in text, (
            f"{name} must build the plugin zip through the shared composite"
        )


def test_plugin_zip_vendors_the_wheel_rather_than_pinning_pypi() -> None:
    """The packed zip launches from ${CLAUDE_PLUGIN_ROOT}, not from an index.

    This is the property that makes the asset worth having.  The marketplace
    entry is a thin PyPI pointer and stays that way; the zip is thick, so it
    installs at a version PyPI has never seen — every rc, and the constant
    ``0.0.0-dev`` edge build that no publishing policy could ever serve.

    Asserted on the scripts rather than on a built artifact because the
    failure is silent: a zip whose ``--from`` still names a PyPI version
    packs, uploads and installs fine from a stable tag, and fails only for
    whoever tries the candidate.
    """
    action = (REPO_ROOT / ".github" / "actions" / "build-plugin-zip").resolve()
    vendor = (action / "vendor.py").read_text(encoding="utf-8")
    verify = (action / "verify.py").read_text(encoding="utf-8")
    assert "CLAUDE_PLUGIN_ROOT" in vendor, (
        "vendor.py must repin --from onto the vendored wheel"
    )
    assert "CLAUDE_PLUGIN_ROOT" in verify, "verify.py must assert the repin happened"
    # Both scripts refuse rather than warn: a half-vendored zip is worse than
    # no zip, because it looks installable right up until launch.
    assert "VendorError" in vendor and "raise" in vendor
    assert "VerifyError" in verify and "raise" in verify


def test_linux_packages_attach_to_prereleases_too() -> None:
    """publish-linux-packages runs for rcs, gated on ``released`` alone.

    It only attaches deb/rpm to the version's own GitHub release — a
    per-version immutable channel like the wheel and mcpb assets — so an
    rc gets its artifacts; external surfaces (PyPI, registry, marketplace)
    stay stable-gated elsewhere.
    """
    block = _release_job_block("publish-linux-packages", "build-mcpb")
    assert "if: needs.release.outputs.released == 'true'\n" in block, (
        "publish-linux-packages must gate on released alone"
    )
    assert "is_prerelease" not in block, (
        "publish-linux-packages must not skip prereleases"
    )
    postinstall = (REPO_ROOT / "packaging" / "scripts" / "postinstall.sh").read_text(
        encoding="utf-8"
    )
    assert "*-rc.*" in postinstall and "releases/download" in postinstall, (
        "an rc package's postinstall must install the wheel from the"
        " version's own GitHub release: the .deb/.rpm candidate then"
        " installs from the same immutable per-version channel it shipped"
        " in, independently of whether that rc's PyPI publish landed"
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


# A freshly rendered project, before its first release, carries seed
# versions rather than a coherent one: `server.json` is seeded 0.1.0 (to
# match `pyproject.toml`) while the plugin pair
# is seeded 0.0.0. They are placeholders, not a claim about a
# release, so the pin assertions below do not apply to that state.
_SEED_PINS = frozenset({"0.0.0", "0.1.0"})


def _is_pre_release_state(pins: dict[str, str]) -> bool:
    """Whether *pins* are all still placeholders, so no pin rule applies.

    Keyed on **every** pin being a seed value, not on `server.json`'s
    top-level one alone (#472).  Keying it on a single pin meant a mixed
    state — one manifest reseeded to its placeholder by a `copier update`
    while the rest sit at a real released version — skipped the whole
    assertion instead of failing it.  That mixed state is the one most worth
    catching: these are published surfaces, so a placeholder among them
    tells the MCP registry or the marketplace a version that was never
    released.

    Pure, and separate from the IO, so the decision is exercised by the unit
    tests below even in a freshly rendered project where the assertions that
    consume it skip.
    """
    return bool(pins) and all(ver in _SEED_PINS for ver in pins.values())


def _lockstep_violation(pins: dict[str, str]) -> str | None:
    """A message naming the disagreeing pins, or `None` when they agree."""
    distinct = sorted(set(pins.values()))
    if len(distinct) <= 1:
        return None
    return (
        "committed manifests must all pin the same version, but they "
        f"disagree ({', '.join(distinct)}): "
        + ", ".join(f"{where} = {ver}" for where, ver in sorted(pins.items()))
    )


def _pins_or_skip() -> dict[str, str]:
    """The committed pins, skipping a project that has not released yet."""
    pins = _published_pins()
    if _is_pre_release_state(pins):
        pytest.skip("initial placeholder versions, before the first stable release")
    return pins


def test_committed_pins_agree_with_each_other() -> None:
    """Every committed manifest pins the same version.

    `AGENTS.md`'s "Manifest version lockstep" rule is that `server.json`,
    the Claude plugin `plugin.json` and its `.mcp.json` all carry
    one version, and `scripts/stamp_manifests.py` moves them together.  The
    neighbouring test checks each pin against the *release history*, which
    is the stronger check in most states but admits two values during a
    release PR — the last stable and the version being prepared — so a
    half-stamped tree, some manifests moved and some not, satisfies it while
    violating lockstep (#472).

    This asserts the rule directly and needs no tag visibility to do it, so
    it still bites on the shallow checkouts where the history-scoped test
    skips.
    """
    violation = _lockstep_violation(_pins_or_skip())
    assert violation is None, violation


class TestPinRuleLogic:
    """The pin rules on synthetic pin maps.

    The two assertions above read this repo's real manifests, so in a
    freshly rendered project they skip — correctly, but that would leave the
    logic they encode shipping untested in every downstream until its first
    release. These exercise the decisions directly.
    """

    def test_a_fresh_render_is_a_pre_release_state(self) -> None:
        """Seeds deliberately differ from each other: `server.json` is
        seeded to match `pyproject.toml` (0.1.0),
        the plugin pair to 0.0.0. All are placeholders, so this
        must read as pre-release rather than as a lockstep violation."""
        assert _is_pre_release_state(
            {"server.json version": "0.1.0", "plugin.json version": "0.0.0"}
        )

    def test_a_reseeded_manifest_beside_a_released_one_is_not(self) -> None:
        """The #472 gap. A `copier update` reseeds one manifest while the
        project sits at a real release; keying the skip on `server.json`
        alone let this pass unexamined."""
        assert not _is_pre_release_state(
            {"server.json version": "0.1.0", "plugin.json version": "1.9.0"}
        )
        # ...and in the other direction, which is the reported instance:
        # server.json released, its own pypi package pin left at the seed.
        assert not _is_pre_release_state(
            {"server.json version": "1.9.0", "server.json pypi x": "0.1.0"}
        )

    def test_no_pins_at_all_is_not_a_pre_release_state(self) -> None:
        """An empty map means the collector found nothing to check, which is
        a broken collector rather than a project before its first release —
        `all()` over an empty iterable would call it pre-release and skip."""
        assert not _is_pre_release_state({})

    def test_agreeing_pins_pass(self) -> None:
        assert (
            _lockstep_violation(
                {"server.json version": "1.9.0", "plugin.json version": "1.9.0"}
            )
            is None
        )

    def test_disagreeing_pins_are_named_in_the_message(self) -> None:
        """A half-stamped release PR: some manifests moved to the prepared
        version, some still at the last stable. Both values are admissible
        to the history-scoped test, so only lockstep catches it."""
        violation = _lockstep_violation(
            {"server.json version": "1.9.0", "plugin.json version": "2.0.0"}
        )
        assert violation is not None
        # The message has to name which pin is which — a bare "they
        # disagree" leaves the reader diffing four files by hand.
        assert "server.json version = 1.9.0" in violation
        assert "plugin.json version = 2.0.0" in violation


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
    pins = _pins_or_skip()
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


@pytest.mark.parametrize(
    ("workflow", "label"),
    [
        (CI_WORKFLOW, "ci.yml (own-branch path)"),
        (COVERAGE_STATUS_WORKFLOW, "coverage-status.yml (fork-PR fallback)"),
    ],
)
def test_every_codecov_patch_poster_posts_under_all_outcomes(
    workflow: Path, label: str
) -> None:
    """Both posters of ``codecov/patch`` report something, always.

    `extra_required_checks` lets a project make ``codecov/patch`` a required
    context.  A required check that never reports does not turn a pull request
    red — it leaves it waiting forever on a status that is not coming, and the
    only exits are an admin bypass or a ruleset edit.  So "post an ``error``"
    and "post nothing" are not equivalent failure modes, and the difference is
    invisible in a green run.

    Two workflows can post this context: `ci.yml` for own-branch pull requests
    and `coverage-status.yml` for fork ones, where the read-only fork token
    forces the `workflow_run` detour.  They drifted once (#476): `ci.yml`
    treated always-report as an invariant while the fallback skipped its
    posting step whenever the artifact download failed.  Asserted rather than
    documented, because the two halves live in different files and nothing
    else couples them.
    """
    text = workflow.read_text(encoding="utf-8")
    poster = text.index("name: Post codecov/patch status")
    # Look only at the posting step, not the whole workflow: `always()`
    # elsewhere in the file would satisfy a naive substring check.
    step = text[poster : poster + 2000]
    assert "always()" in step, (
        f"{label}: the codecov/patch posting step must run under all outcomes"
    )
    # ...and it must carry a fallback state, or `always()` only guarantees the
    # step runs, not that it posts a usable status.
    assert "'error'" in step or '"error"' in step, (
        f"{label}: the posting step must default to an `error` state when the "
        "coverage result is missing"
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


# --- server.json registry constraints ------------------------------------
#
# `publish-registry` is the LAST job of a release and the only place the MCP
# Registry's limits are enforced.  A description over 100 characters passes
# every earlier gate, publishes PyPI / Docker / the plugin, and then fails the
# registry entry alone (scholar-mcp v1.9.1).  The template's `copier copy`
# validator guards the initial answer; this guards later hand edits to
# `server.json`, which copier never re-checks.

MCP_REGISTRY_DESCRIPTION_MAX = 100


def test_server_json_description_fits_the_registry_cap() -> None:
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    description = server["description"]
    assert description.strip(), "server.json description is empty"
    assert len(description) <= MCP_REGISTRY_DESCRIPTION_MAX, (
        f"server.json description is {len(description)} chars; the MCP registry "
        f"caps it at {MCP_REGISTRY_DESCRIPTION_MAX} and publish-registry — the "
        f"last job of a release — would fail with 422 (#481). Shorten it here "
        f"and in .copier-answers.yml (domain_description)."
    )


# ---------------------------------------------------------------------------
# historical breaking-change content (#1123)
# ---------------------------------------------------------------------------


def _changelog_sections() -> list[tuple[str, str]]:
    """Return ``(heading, body)`` for every ``## <version>`` section."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (?=\d)", text, flags=re.MULTILINE)[1:]
    out = []
    for part in parts:
        head, _, body = part.partition("\n")
        out.append((head.strip(), body))
    return out


def test_changelog_keeps_the_3_0_0_breaking_block() -> None:
    """The 3.0.0 section still carries its restored breaking changes (#1123).

    The 2026-08-16 reconstruction (#1067) generated pre-4.0 sections with
    only Features / Bug Fixes / Chores headings, flattening sixteen `!`
    markers and dropping the operator migration instructions that had
    shipped in the tagged changelogs — leaving 3.0.0, a major release,
    presenting an empty section. A support question then arrived from
    someone who had set the renamed env var and found it inert, which is
    exactly the failure the deleted entry predicted.

    This pins the recovered content rather than the whole file: knope never
    rewrites a historical section, so nothing but a hand edit can remove it.
    """
    sections = dict(_changelog_sections())
    body = sections.get("3.0.0 (2026-06-17)")
    assert body is not None, "the 3.0.0 section disappeared from CHANGELOG.md"

    assert "### Breaking Changes" in body
    for marker in (
        "MARKDOWN_VAULT_MCP_READY_TIMEOUT_S",
        "MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S",
        "IndexNotReadyError",
        "IndexUnavailableError",
        "is_index_ready",
        "wait_for_index_ready",
        "needs_queryable",
        "GroupedResult",
        "MAX_ATTACHMENT_SIZE_MB",
    ):
        assert marker in body, f"3.0.0 breaking block lost {marker}"


def test_changelog_preamble_explains_the_empty_promotion_sections() -> None:
    """Empty sections are documented, not mysterious (#1123).

    A stable release whose whole range is the promotion commit has nothing
    for knope to count — its work sits in the ``-rc`` sections beneath it.
    That is by design, but a reader hitting a blank heading cannot tell it
    apart from lost content, so the preamble above the insertion flag names
    the shape and lists the sections that have it.
    """
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    preamble, flag, _ = text.partition("<!-- version list -->")
    assert flag, "insertion flag missing"

    empty = [head for head, body in _changelog_sections() if not body.strip()]
    for head in empty:
        version = head.split(" ", 1)[0]
        assert version in preamble, (
            f"release section {version} is empty and unexplained — either give "
            f"it content or name it in the CHANGELOG.md preamble"
        )
