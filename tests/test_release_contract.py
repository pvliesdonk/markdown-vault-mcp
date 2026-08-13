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
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUMPER = REPO_ROOT / "scripts" / "bump_manifests.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"

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
