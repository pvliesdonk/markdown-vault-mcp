"""Guards for the release-commit contract between PSR and the manifest bumper.

`pyproject.toml`'s `[tool.semantic_release] assets` list and
`scripts/bump_manifests.py` are two halves of one mechanism: PSR stages every
path in `assets` into the release commit, and the bumper is what actually
rewrites the version inside those paths.  Declare a path in one half only and
the release still succeeds — it just ships a file whose version lags the tag.

That is not hypothetical.  v3.1.0 (#298) added `uv.lock` to `assets` and a
`_bump_lockfile()` to the bumper in one change, but the script was
`_skip_if_exists` at the time while `pyproject.toml` re-renders, so instances
with a pre-existing extended copy took the `assets` half alone.  Their release
commits shipped a lockfile whose self entry lagged `pyproject.toml`, which made
`uv lock --check` fail on `main` and turned every `uv sync` into a workspace
mutation (#325, #326).  The script is template-owned now, with
`DOMAIN-MANIFESTS` seams for a project's own manifests; these tests are what
keeps the two halves honest for anything added through those seams.

A second invariant joined in template#345: the bumper must never pin a version
the release will not publish.  Pre-releases skip PyPI, the MCP registry, and
the marketplace publish, so on a pre-release run the manifests that name a
published artifact (`server.json` and the Claude Code plugin pair) must stay
at the last published stable, while `uv.lock` — which tracks `pyproject.toml`,
not PyPI — must still move.  The behavioral tests below run the real script
against a sandbox repo root and assert both directions: a stable version moves
everything, a pre-release version moves only the lockfile.

A third invariant joined in template#350: PSR's changelog writing has two
halves that fail silently when either is missing.  `update` mode (the v10
default) inserts version sections only at the insertion flag, so the config
must name the flag and `CHANGELOG.md` must carry it — a flag-less changelog is
never written, with no error.  And `changelog_file` must sit in its supported
location (`changelog.default_templates`), not the deprecated bare
`changelog.changelog_file` key.  The changelog tests below pin both halves.

A fourth invariant joined in template#375: the two behavioral tests above
prove the *bumper* never writes a pre-release pin, but nothing read the
committed manifests themselves.  The stable-pin test below does, so a bad
pin that arrives by hand edit or a half-applied update — outside the bumper
— still fails.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUMPER = REPO_ROOT / "scripts" / "bump_manifests.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_JSON = Path(".claude-plugin/plugin/.claude-plugin/plugin.json")
MCP_JSON = Path(".claude-plugin/plugin/.mcp.json")

# Manifests the template itself bumps, mapped to the marker that proves the
# bumper still handles them.  A domain manifest added through the
# DOMAIN-MANIFESTS seam is covered by the generic path-mention test below
# instead — the template cannot know its function names.
TEMPLATE_ASSETS = {
    "server.json": "_dump(server_path, server)",
    "uv.lock": "_bump_lockfile(version)",
}


def _bumper_text() -> str:
    return BUMPER.read_text(encoding="utf-8")


def _semantic_release_config() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    section: dict[str, Any] = data["tool"]["semantic_release"]
    return section


def _assets() -> list[str]:
    assets = _semantic_release_config()["assets"]
    assert isinstance(assets, list), "[tool.semantic_release] assets must be a list"
    return [str(asset) for asset in assets]


def test_build_command_invokes_the_bumper() -> None:
    """PSR must actually run the script, or none of the rest matters."""
    build_command = _semantic_release_config().get("build_command")
    assert build_command is not None, "[tool.semantic_release] build_command is unset"
    assert "scripts/bump_manifests.py" in str(build_command)
    assert BUMPER.is_file(), f"{BUMPER} is missing"


@pytest.mark.parametrize(("asset", "marker"), sorted(TEMPLATE_ASSETS.items()))
def test_template_asset_is_still_bumped(asset: str, marker: str) -> None:
    """Each template-owned asset is declared AND rewritten, or neither.

    Both directions fail here: dropping the `assets` entry leaves the bumper
    rewriting a file PSR never commits, and dropping the bumper call leaves PSR
    committing a file nobody rewrote.
    """
    declared = asset in _assets()
    bumped = marker in _bumper_text()
    assert declared == bumped, (
        f"{asset} is {'declared in assets' if declared else 'absent from assets'} "
        f"but {'handled' if bumped else 'not handled'} by scripts/bump_manifests.py "
        "— the release commit would carry a stale version for it"
    )


def test_every_declared_asset_is_mentioned_by_the_bumper() -> None:
    """Anything in `assets` must at least appear in the bumper's source.

    Deliberately a weak check — it cannot prove a domain manifest is rewritten
    correctly, only that adding a path to `assets` without touching the script
    (the #325 failure shape) does not pass silently.  `CHANGELOG.md` and
    `pyproject.toml` are PSR's own outputs and never appear here.
    """
    text = _bumper_text()
    psr_owned = {"CHANGELOG.md", "pyproject.toml"}
    missing = [
        asset for asset in _assets() if asset not in psr_owned and asset not in text
    ]
    assert not missing, (
        f"declared in [tool.semantic_release] assets but never named in "
        f"scripts/bump_manifests.py: {missing} — add the bump inside the "
        "DOMAIN-MANIFESTS markers, or drop the asset"
    )


def test_domain_manifest_sentinels_are_present() -> None:
    """Both seams survive as matched pairs, exactly once.

    The script is template-owned, so these markers are the only copier-safe
    place for a project's own bumps.  A dropped fence means the next update
    silently overwrites whatever a downstream put there.
    """
    text = _bumper_text()
    for name in ("DOMAIN-MANIFESTS-HELPERS", "DOMAIN-MANIFESTS"):
        assert text.count(f"# {name}-START") == 1, f"{name}-START fence missing"
        assert text.count(f"# {name}-END") == 1, f"{name}-END fence missing"


def test_domain_manifest_calls_sentinel_lives_inside_main() -> None:
    """The call seam sits in `main()`, after the template's own bumps.

    Placement is the point: `version` is only in scope there, and running after
    the shipped bumpers means a domain manifest cannot be skipped by an early
    `return 1` on a malformed `server.json`.
    """
    text = _bumper_text()
    main_def = text.index("def main()")
    start = text.index("# DOMAIN-MANIFESTS-START")
    end = text.index("# DOMAIN-MANIFESTS-END")
    assert main_def < start < end
    assert text.index("_bump_lockfile(version)") < start


def test_changelog_config_lives_in_the_supported_location() -> None:
    """`changelog_file` sits under `default_templates`; `mode` is pinned.

    The bare `changelog.changelog_file` key is the pre-v9.11 deprecated
    location (template#350) — PSR may stop honouring it in a future major,
    and while it is honoured it silently rewires `output_format`.  `mode` is
    asserted so the update-at-flag contract the seeded CHANGELOG.md relies on
    stays explicit rather than riding on PSR's default.
    """
    changelog_cfg = _semantic_release_config()["changelog"]
    assert "changelog_file" not in changelog_cfg, (
        "changelog.changelog_file is the deprecated pre-v9.11 location — move "
        "it to [tool.semantic_release.changelog.default_templates]"
    )
    assert changelog_cfg["default_templates"]["changelog_file"] == "CHANGELOG.md"
    assert changelog_cfg["mode"] == "update"


def test_changelog_carries_the_insertion_flag() -> None:
    """`CHANGELOG.md` contains the exact insertion flag the config names.

    In `update` mode PSR inserts each release's version section at this flag
    and preserves the rest of the file; without the flag it writes nothing —
    silently, on every release (template#350).  If this project's
    CHANGELOG.md predates the flag, add the line once by hand, anywhere a
    machine-written version list should begin (typically after the intro
    prose).
    """
    flag = _semantic_release_config()["changelog"]["insertion_flag"]
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert flag in changelog, (
        f"CHANGELOG.md lacks the PSR insertion flag — add this exact line "
        f"once, where version sections should be inserted: {flag}"
    )


_PRERELEASE_SUFFIX = re.compile(r"-(?:alpha|beta|rc|dev|pre|a|b)\b", re.IGNORECASE)


def _published_manifest_versions() -> dict[str, str]:
    """Version each committed published-manifest currently pins.

    These are the files the "Manifest version lockstep" rule keeps at the
    latest *stable* release: ``server.json``'s own version and its pypi
    package versions, plus the Claude plugin pair. The oci image identifier is
    excluded — it legitimately ends in the tag form ``:vX.Y.Z``.
    """
    versions: dict[str, str] = {}
    server = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    versions["server.json version"] = str(server["version"])
    for pkg in server.get("packages", []):
        if pkg.get("registryType") == "pypi":
            versions[f"server.json pypi {pkg.get('identifier', '')}"] = str(
                pkg["version"]
            )
    plugin = json.loads((REPO_ROOT / PLUGIN_JSON).read_text(encoding="utf-8"))
    versions["plugin.json version"] = str(plugin["version"])
    mcp = json.loads((REPO_ROOT / MCP_JSON).read_text(encoding="utf-8"))
    for server_cfg in mcp.values():
        for arg in server_cfg.get("args", []):
            if isinstance(arg, str) and "==" in arg:
                versions[f".mcp.json pin {arg}"] = arg.split("==", 1)[1]
    return versions


def test_committed_manifest_pins_are_stable_versions() -> None:
    """No committed published-manifest pins a pre-release version.

    The bumper leaves these files at the last published stable on a
    pre-release run (proven by the sandbox tests below), but a value can
    reach the committed file another way — a hand edit, a bad merge, a
    half-applied copier update.  A pre-release pin such as ``X.Y.Z-rc.N``
    names a version PyPI, the MCP registry, and the marketplace never
    publish, so ``uvx --from pkg==X.Y.Z-rc.N`` cannot resolve it (the failure
    tracked at markdown-vault-mcp#1053).  This reads the committed files
    themselves, which the sandbox tests never touch.
    """
    bad = {
        where: ver
        for where, ver in _published_manifest_versions().items()
        if _PRERELEASE_SUFFIX.search(ver)
    }
    assert not bad, (
        "committed manifests must pin the latest stable release, but these "
        "carry a pre-release version: "
        + ", ".join(f"{where} = {ver}" for where, ver in sorted(bad.items()))
    )


def _stable_release_tags() -> list[str] | None:
    """Stable ``vX.Y.Z`` tags in this repo, or ``None`` when git is absent.

    A freshly generated project is not yet a git repository and has no tags,
    so the caller skips there rather than failing.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "tag", "--list"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [t for t in completed.stdout.split() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]


def test_committed_server_version_matches_a_released_tag() -> None:
    """When the repo has stable tags, ``server.json`` names one of them.

    Best-effort and self-skipping: it asserts nothing before a project's
    first stable, but once stables exist a committed version with no matching
    ``vX.Y.Z`` tag means the pin drifted from what was actually released.
    """
    tags = _stable_release_tags()
    if not tags:
        pytest.skip("no stable release tags in this repository yet")
    version = str(
        json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))["version"]
    )
    if version in {"0.0.0", "0.1.0"}:
        pytest.skip("initial placeholder version, before the first release")
    assert f"v{version}" in tags, (
        f"server.json pins {version}, but no matching stable tag v{version} "
        "exists — the manifest must name a released stable version"
    )


def _run_bumper(workdir: Path, version: str) -> subprocess.CompletedProcess[str]:
    """Run the real bumper against ``workdir`` with ``NEW_VERSION`` set."""
    return subprocess.run(
        [sys.executable, str(BUMPER)],
        cwd=workdir,
        env={**os.environ, "NEW_VERSION": version},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def release_sandbox(tmp_path: Path) -> Path:
    """A minimal repo root the bumper can rewrite without touching this repo.

    `server.json` and the plugin manifests are copied verbatim so the test exercises the
    real manifest shapes; `pyproject.toml` and `uv.lock` are minimal stand-ins
    carrying only what `_bump_lockfile` reads.
    """
    shutil.copy(REPO_ROOT / "server.json", tmp_path / "server.json")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sandbox-pkg"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "sandbox-pkg"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    for manifest in (PLUGIN_JSON, MCP_JSON):
        (tmp_path / manifest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / manifest, tmp_path / manifest)
    return tmp_path


def test_stable_release_bumps_every_published_version_field(
    release_sandbox: Path,
) -> None:
    """A stable version moves every manifest the release publishes."""
    result = _run_bumper(release_sandbox, "9.9.9")
    assert result.returncode == 0, result.stderr

    server = json.loads((release_sandbox / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == "9.9.9"
    pypi = [p for p in server["packages"] if p.get("registryType") == "pypi"]
    assert pypi and all(p["version"] == "9.9.9" for p in pypi)
    oci = [p for p in server["packages"] if p.get("registryType") == "oci"]
    assert all(p["identifier"].endswith(":v9.9.9") for p in oci)

    lock = (release_sandbox / "uv.lock").read_text(encoding="utf-8")
    assert 'version = "9.9.9"' in lock

    plugin = json.loads((release_sandbox / PLUGIN_JSON).read_text(encoding="utf-8"))
    assert plugin["version"] == "9.9.9"
    mcp = json.loads((release_sandbox / MCP_JSON).read_text(encoding="utf-8"))
    pins = [
        arg
        for server_cfg in mcp.values()
        for arg in server_cfg.get("args", [])
        if "==" in arg
    ]
    assert pins and all(pin.endswith("==9.9.9") for pin in pins)


@pytest.mark.parametrize("version", ["9.9.9-rc.1", "9.9.9-rc.12"])
def test_prerelease_leaves_published_version_fields_untouched(
    release_sandbox: Path, version: str
) -> None:
    """A pre-release run must not pin versions PyPI/the registry never get.

    `publish-pypi`, `publish-registry`, and the marketplace publish are all
    gated on PSR's `is_prerelease` output, so a pre-release version never
    exists on those channels — pinning it would leave the branch naming an
    uninstallable version between stables (template#345).  `uv.lock` still
    moves: it tracks `pyproject.toml`, and lagging there breaks
    `uv lock --check` on `main`.
    """
    before_server = (release_sandbox / "server.json").read_bytes()
    before_plugin = (release_sandbox / PLUGIN_JSON).read_bytes()
    before_mcp = (release_sandbox / MCP_JSON).read_bytes()

    result = _run_bumper(release_sandbox, version)
    assert result.returncode == 0, result.stderr
    assert "pre-release" in result.stdout

    assert (release_sandbox / "server.json").read_bytes() == before_server
    assert (release_sandbox / PLUGIN_JSON).read_bytes() == before_plugin
    assert (release_sandbox / MCP_JSON).read_bytes() == before_mcp
    lock = (release_sandbox / "uv.lock").read_text(encoding="utf-8")
    assert f'version = "{version}"' in lock
