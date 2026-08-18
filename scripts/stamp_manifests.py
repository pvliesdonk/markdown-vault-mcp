#!/usr/bin/env python3
"""Stamp uv.lock and the install-channel manifests with a release version.

Successor of the PSR-era ``scripts/bump_manifests.py``, which the Phase-2
swap deleted together with python-semantic-release
(fastmcp-server-template#406).  knope's ``prepare-release`` workflow
(knope.toml) invokes this script as a Command step with the computed
``$version`` after ``PrepareRelease`` has bumped ``pyproject.toml`` —
knope's only ``versioned_files`` entry, which this script deliberately
never touches.

Two stamping tiers:

- ``uv.lock``'s self-version entry moves on EVERY release, rc included, in
  the **PEP 440 canonical spelling** (``-rc.N`` becomes ``rcN``).  It is
  stamped here rather than as a knope versioned file because uv rewrites
  the entry to canonical form on any re-lock, while knope requires its
  versioned files to agree with pyproject.toml's SemVer spelling — a
  re-lock during an rc window would otherwise break every later
  ``PrepareRelease``.  Stamping canonical from the start means a re-lock
  changes nothing.
- The install-channel manifests are stable-only, per the per-surface
  resolvability rule (release-vision D12): ``server.json`` and the Claude
  Code plugin pair pin artifacts that are published exclusively for stable
  releases (PyPI, the MCP registry, the marketplace), so a pre-release
  version leaves them at the last published stable.

Fail-loud and atomic (the markdown-vault-mcp#1083 lesson): an expected pin
that cannot be found and stamped exits non-zero naming the file and the
pattern — never warn-and-continue — and every rewrite goes through a
temp-file-plus-rename in the target's directory, so a failure part-way
leaves no half-written manifest behind.

This file is TEMPLATE-OWNED: it re-renders on every ``copier update``.
Projects that ship additional versioned manifests put their code between the
``DOMAIN-MANIFESTS-HELPERS`` markers (module-level helpers) and the
``DOMAIN-MANIFESTS`` markers (the calls, inside ``main()``); only content
inside those markers survives an update.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


class StampError(RuntimeError):
    """A required pin was missing or unstampable — the release must refuse."""


def _load(path: Path) -> Any:
    if not path.is_file():
        raise StampError(f"{path}: not found — run from the repository root")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path: Path, data: Any) -> None:
    """Write JSON atomically, in the byte format the toolchain expects.

    ``indent=2, ensure_ascii=False`` plus a trailing newline matches
    ``scripts/gen_config_surface.py``'s asserted format.  The temp file is
    created in the target's own directory so the final ``rename`` is atomic
    on the same filesystem.
    """
    tmp = path.parent / f".{path.name}.stamp-tmp"
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _is_prerelease(version: str) -> bool:
    """Return True unless ``version`` is a plain stable ``X.Y.Z``.

    Deliberately conservative, exactly like the PSR-era bumper: anything that
    is not a plain three-component release keeps the published-manifest pins
    at the last published stable instead of pinning a version that may never
    exist on PyPI.
    """
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is None


def _pep440_canonical(version: str) -> str:
    """Return knope's SemVer spelling in PEP 440 canonical form.

    The release flow produces exactly two shapes: ``X.Y.Z`` (already
    canonical) and ``X.Y.Z-rc.N``, whose canonical spelling is
    ``X.Y.ZrcN``.  Anything else passes through unchanged — uv would
    canonicalize it on the next re-lock, and the stamp must not invent a
    normalization of its own beyond the shapes the flow can emit.
    """
    return re.sub(r"-rc\.(\d+)$", r"rc\1", version)


def _stamp_uv_lock(version: str) -> list[Path]:
    """Stamp ``uv.lock``'s self-version entry, on every release.

    The entry is located by the PEP 503-normalized distribution name from
    ``pyproject.toml`` and rewritten to the PEP 440 canonical spelling of
    ``version``, whatever spelling (SemVer or canonical) it currently
    carries — so a legacy-spelled entry is simply restamped, and a later
    ``uv lock`` run rewrites nothing.  A lockfile without the entry is
    broken and refuses the run.
    """
    with Path("pyproject.toml").open("rb") as fh:
        project_name = str(tomllib.load(fh)["project"]["name"])
    normalized = re.sub(r"[-_.]+", "-", project_name).lower()
    path = Path("uv.lock")
    if not path.is_file():
        raise StampError(f"{path}: not found — run from the repository root")
    text = path.read_text(encoding="utf-8")
    canonical = _pep440_canonical(version)
    new_text, n = re.subn(
        rf'(name = "{re.escape(normalized)}"\nversion = ")[^"]+(")',
        rf"\g<1>{canonical}\g<2>",
        text,
    )
    if n == 0:
        raise StampError(
            f"{path}: no 'name = \"{normalized}\"' entry with a version line to stamp"
        )
    tmp = path.parent / f".{path.name}.stamp-tmp"
    try:
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    print(f"stamp_manifests: uv.lock -> {canonical} (PEP 440 canonical)")
    return [path]


def _stamp_server_json(version: str) -> list[Path]:
    """Stamp ``server.json``: top-level version, PyPI versions, OCI tag.

    Every expected pin must stamp or the whole run refuses: a missing
    ``packages`` array, a non-object package entry, or an OCI identifier
    without a ``:v<tag>`` suffix is a broken manifest, not a skippable one.
    """
    path = Path("server.json")
    server = _load(path)
    if not isinstance(server, dict):
        raise StampError(f"{path}: top level must be a JSON object")
    server["version"] = version
    packages = server.get("packages")
    if not isinstance(packages, list):
        raise StampError(f"{path}: 'packages' must be a JSON array")
    for i, pkg in enumerate(packages):
        if not isinstance(pkg, dict):
            raise StampError(f"{path}: packages[{i}] must be a JSON object")
        if pkg.get("registryType") == "pypi":
            pkg["version"] = version
        elif pkg.get("registryType") == "oci":
            identifier = pkg.get("identifier") or ""
            new_id, n = re.subn(r":v[^:]+$", f":v{version}", identifier)
            if n == 0:
                raise StampError(
                    f"{path}: OCI identifier {identifier!r} has no "
                    "':v<tag>' suffix to stamp"
                )
            pkg["identifier"] = new_id
    _dump(path, server)
    print(f"stamp_manifests: server.json -> {version}")
    return [path]


def _stamp_claude_plugin_manifests(version: str) -> list[Path]:
    """Stamp the Claude Code plugin channel's two version-coupled manifests.

    ``plugin.json``'s ``version`` and ``.mcp.json``'s ``--from`` pin move in
    lockstep.  An ``.mcp.json`` without a single ``--from pkg==version`` pin
    is a broken manifest and refuses the run.
    """
    plugin_path = Path(".claude-plugin/plugin/.claude-plugin/plugin.json")
    manifest = _load(plugin_path)
    if not isinstance(manifest, dict) or "version" not in manifest:
        raise StampError(f"{plugin_path}: no top-level 'version' field to stamp")
    manifest["version"] = version
    _dump(plugin_path, manifest)

    mcp_path = Path(".claude-plugin/plugin/.mcp.json")
    mcp = _load(mcp_path)
    if not isinstance(mcp, dict):
        raise StampError(f"{mcp_path}: top level must be a JSON object")
    pins = 0
    for server in mcp.values():
        if not isinstance(server, dict):
            continue
        args = server.get("args", [])
        for i, arg in enumerate(args):
            if arg == "--from" and i + 1 < len(args) and "==" in args[i + 1]:
                spec = args[i + 1].split("==", 1)[0]
                args[i + 1] = f"{spec}=={version}"
                pins += 1
    if pins == 0:
        raise StampError(f"{mcp_path}: no '--from <pkg>==<version>' pin found to stamp")
    _dump(mcp_path, mcp)
    print(f"stamp_manifests: Claude plugin manifests -> {version}")
    return [plugin_path, mcp_path]


# DOMAIN-MANIFESTS-HELPERS-START — module-level helpers for this project's own
# versioned manifests (a `_stamp_plugin_json(version)`, a TOML rewriter, ...).
# `_load` / `_dump` above read and write JSON atomically in the byte format
# the rest of the toolchain expects (indent=2, ensure_ascii=False, trailing
# newline).  Raise StampError for a pin that cannot be stamped — never warn
# and continue.  Call what you define from the DOMAIN-MANIFESTS block in
# `main()` below.  Kept across copier update.
# DOMAIN-MANIFESTS-HELPERS-END


def _git_stage(paths: list[Path]) -> None:
    """Stage the stamped files so knope's follow-up commit picks them up.

    ``-f`` because ``.gitignore``'s any-depth ``.mcp.json`` pattern also
    matches the *tracked* plugin manifest; forcing an explicit path list is
    safe and keeps the stage from refusing on a fresh clone.
    """
    if not paths:
        return
    subprocess.run(
        ["git", "add", "-f", "--", *[str(p) for p in paths]],
        check=True,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1]:
        print(
            "usage: stamp_manifests.py <version> "
            "(knope passes $version from PrepareRelease)",
            file=sys.stderr,
        )
        return 1
    version = argv[1]

    # uv.lock tracks pyproject.toml, not a published artifact, so its
    # self-version entry moves on EVERY release — before the pre-release
    # skip below.
    stamped = _stamp_uv_lock(version)

    if _is_prerelease(version):
        # Pre-releases never reach PyPI, the MCP registry, or the
        # marketplace, so the manifests that pin published artifacts stay at
        # the last published stable; the next stable release re-stamps them.
        print(
            f"stamp_manifests: {version} is a pre-release — "
            "server.json and the Claude plugin "
            "manifests left at the last published stable"
        )
        _git_stage(stamped)
        return 0

    stamped += _stamp_server_json(version)
    stamped += _stamp_claude_plugin_manifests(version)
    # DOMAIN-MANIFESTS-START — stamp this project's extra versioned manifests
    # here; `version` is a stable version string (pre-releases returned above)
    # and the repo root is the cwd.  Extend `stamped` with every path you
    # rewrite so it is staged into the release commit.  Raise StampError to
    # refuse the release on a missing pin — never warn and continue.  Kept
    # across copier update; everything outside these markers is
    # template-owned and re-rendered.
    # DOMAIN-MANIFESTS-END
    _git_stage(stamped)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except StampError as exc:
        print(f"stamp_manifests: REFUSING to stamp: {exc}", file=sys.stderr)
        sys.exit(1)
