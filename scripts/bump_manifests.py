#!/usr/bin/env python3
"""Bump versioned manifests to match the semantic-release version.

Invoked by python-semantic-release via ``[tool.semantic_release] build_command``.
PSR sets ``NEW_VERSION`` in the environment and, because ``server.json`` and
``uv.lock`` are listed in ``[tool.semantic_release] assets``, PSR stages and
commits them together with ``pyproject.toml`` + ``CHANGELOG.md`` as the single
release commit — which is the commit it then tags.

The script runs inside PSR's Docker action container (python:3.14-slim), which
has Python but no ``jq`` — hence Python rather than a shell+jq wrapper.

Pre-releases are handled differently (#345): the release workflow's
``publish-pypi`` and ``publish-registry`` jobs (and the marketplace publish)
all skip pre-releases, so a pre-release version never exists on PyPI or the
MCP registry.  Manifests whose version fields name a *published* artifact —
``server.json`` and the Claude Code plugin pair — are therefore left at the
last published stable on pre-release runs, and the next stable release
re-bumps them.  Only ``uv.lock`` is refreshed unconditionally: it tracks
``pyproject.toml``, not PyPI, and letting it lag would break ``uv lock
--check`` on the release commit.

This file is TEMPLATE-OWNED: it re-renders on every ``copier update``, so a
fix to the shared bumpers (``server.json``, ``uv.lock``) arrives whole rather
than half — the ``assets`` half landing in the re-rendered ``pyproject.toml``
while the script half never arrives (#325).  Projects that ship additional
versioned manifests (e.g. a Claude Code ``plugin.json``, an ``.mcp.json``, or
other lockstep JSON/TOML files) put their code between the
``DOMAIN-MANIFESTS-HELPERS`` markers (module-level helpers) and the
``DOMAIN-MANIFESTS`` markers (the calls, inside ``main()``), and list each
extra path in ``pyproject.toml`` ``[tool.semantic_release] assets``.  Only
content inside those markers survives an update; anything added outside them
lands in template-owned code and is at the mercy of the next merge.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        # ensure_ascii=False preserves UTF-8 characters literally, matching
        # jq's default behavior and how a human editor would save the file.
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _is_prerelease(version: str) -> bool:
    """Return True unless ``version`` is a plain stable ``X.Y.Z``.

    PSR (``tag_format = "v{version}"``, ``prerelease_token: rc``) emits
    stable versions as ``X.Y.Z`` and pre-releases in SemVer form as
    ``X.Y.Z-rc.N``.  The check is deliberately conservative: anything that
    is not a plain three-component release (a pre-release, a build tag, an
    unexpected format) keeps the PyPI-referencing pins at the last published
    stable instead of pinning a version that may never exist on PyPI.
    Stdlib-only on purpose — PSR's container Python has no guaranteed
    ``packaging`` install.
    """
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is None


def _bump_server_json(version: str) -> int:
    """Bump ``server.json``: top-level version, PyPI package version, OCI tag.

    Called for stable releases only — both version fields name artifacts that
    are published exclusively for stable releases, so pre-release runs leave
    the file at the last published stable (see ``main()``).  Replace only the
    ``:v<old>`` suffix of the OCI identifier so forks/renames keep their own
    ``ghcr.io/<owner>/<image>`` base.  Returns a process exit code.
    """
    server_path = Path("server.json")
    if not server_path.exists():
        print(
            f"server.json not found in {Path.cwd()} — run from repo root",
            file=sys.stderr,
        )
        return 1
    server = _load(server_path)
    if not isinstance(server, dict):
        print(
            f"{server_path} must contain a JSON object (top-level), "
            f"got {type(server).__name__}",
            file=sys.stderr,
        )
        return 1
    server["version"] = version
    packages = server.get("packages", [])
    if not isinstance(packages, list):
        print(
            f"{server_path}: 'packages' must be a JSON array, got "
            f"{type(packages).__name__}",
            file=sys.stderr,
        )
        return 1
    for i, pkg in enumerate(packages):
        if not isinstance(pkg, dict):
            print(
                f"WARNING: packages[{i}] is not a JSON object "
                f"(got {type(pkg).__name__}) — skipped",
                file=sys.stderr,
            )
            continue
        if pkg.get("registryType") == "pypi":
            pkg["version"] = version
        elif pkg.get("registryType") == "oci":
            # ``or ""`` covers both the absent-key and the JSON-null cases;
            # ``dict.get(key, default)`` only returns default when the key
            # is absent, not when the value is None.
            identifier = pkg.get("identifier") or ""
            new_id, n = re.subn(r":v[^:]+$", f":v{version}", identifier)
            if n == 0:
                print(
                    f"WARNING: OCI identifier {identifier!r} has no ':v<tag>' "
                    "suffix to bump — left unchanged",
                    file=sys.stderr,
                )
            pkg["identifier"] = new_id
    _dump(server_path, server)
    print(f"bump_manifests: server.json → {version}")
    return 0


def _bump_lockfile(version: str) -> None:
    """Refresh ``uv.lock``'s self-referential ``[[package]]`` version.

    PSR bumps ``pyproject.toml:project.version`` but nothing re-locks, so
    without this the release commit ships a lockfile whose self entry still
    carries the previous version.  That drift then self-heals as a side
    effect of the next ``uv run`` rewriting ``uv.lock`` — tripping
    pre-commit's "files were modified by this hook" guard on an unrelated
    later commit.  PSR's container has no ``uv``, so rewrite the one
    version line textually instead of running ``uv lock``.
    """
    lock_path = Path("uv.lock")
    if not lock_path.exists():
        # A fresh scaffold has no lockfile until the first `uv sync`.
        print("bump_manifests: uv.lock not found — skipped", file=sys.stderr)
        return
    with Path("pyproject.toml").open("rb") as fh:
        name = tomllib.load(fh)["project"]["name"]
    # uv writes the PEP 503-normalized name into the lockfile.
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    text = lock_path.read_text(encoding="utf-8")
    pattern = (
        r'(\[\[package\]\]\nname = "'
        + re.escape(normalized)
        + r'"\nversion = ")[^"]*(")'
    )
    new_text, n = re.subn(
        pattern, lambda m: m.group(1) + version + m.group(2), text, count=1
    )
    if n == 0:
        print(
            f"WARNING: uv.lock has no [[package]] entry named {normalized!r} "
            "— left unchanged",
            file=sys.stderr,
        )
        return
    lock_path.write_text(new_text, encoding="utf-8")
    print(f"bump_manifests: uv.lock ({normalized}) → {version}")


def _bump_claude_plugin_manifests(version: str) -> None:
    """Bump the Claude Code plugin channel's two version-coupled manifests.

    ``plugin.json``'s ``version`` and ``.mcp.json``'s ``--from`` pin move in
    lockstep, so the marketplace entry the release workflow publishes always
    installs the version it points at.  Called for stable releases only: the
    pin references PyPI and pre-releases are never published there, so on
    pre-release runs both files stay at the last published stable (see
    ``main()``).  Only the version part after ``==`` is rewritten; the
    package spec (name and extras) stays whatever the project configured.
    """
    plugin_path = Path(".claude-plugin/plugin/.claude-plugin/plugin.json")
    manifest = _load(plugin_path)
    manifest["version"] = version
    _dump(plugin_path, manifest)

    mcp_path = Path(".claude-plugin/plugin/.mcp.json")
    mcp = _load(mcp_path)
    for server in mcp.values():
        args = server.get("args", [])
        for i, arg in enumerate(args):
            if arg == "--from" and i + 1 < len(args):
                spec = args[i + 1].split("==", 1)[0]
                args[i + 1] = f"{spec}=={version}"
    _dump(mcp_path, mcp)


# DOMAIN-MANIFESTS-HELPERS-START — module-level helpers for this project's own
# versioned manifests (a `_bump_plugin_json(version)`, a TOML rewriter, ...).
# `_load` / `_dump` above read and write JSON in the byte format the rest of
# the toolchain expects (indent=2, ensure_ascii=False, trailing newline) —
# scripts/gen_config_surface.py asserts that format, so prefer them over a
# bare `json.dump`.  Call what you define from the DOMAIN-MANIFESTS block in
# `main()` below.  Kept across copier update.
# DOMAIN-MANIFESTS-HELPERS-END


def main() -> int:
    version = os.environ.get("NEW_VERSION")
    if not version:
        print(
            "NEW_VERSION must be set (python-semantic-release build_command env)",
            file=sys.stderr,
        )
        return 1

    prerelease = _is_prerelease(version)
    if prerelease:
        # Pre-releases skip publish-pypi / publish-registry / the marketplace
        # publish, so rewriting the manifests that name a published artifact
        # would pin a version that never exists there (#345).  Leave them at
        # the last published stable; the next stable release re-bumps them.
        print(
            f"bump_manifests: {version} is a pre-release — "
            "server.json and the Claude plugin "
            "manifests left at the last published stable"
        )
    else:
        rc = _bump_server_json(version)
        if rc != 0:
            return rc
    _bump_lockfile(version)
    if not prerelease:
        _bump_claude_plugin_manifests(version)
    # DOMAIN-MANIFESTS-START — bump this project's extra versioned manifests
    # here; `version` is the new version string, `prerelease` says whether it
    # is a pre-release, and the repo root is the cwd.  A manifest that names a
    # published artifact (a PyPI pin, a registry entry) should follow the same
    # rule as the template's own bumps: skip the rewrite when `prerelease` is
    # true.  Every path touched here must also be listed in `pyproject.toml`
    # `[tool.semantic_release] assets`, or PSR leaves it out of the release
    # commit.  Kept across copier update; everything outside these markers is
    # template-owned and re-rendered, which is what keeps the bumpers above in
    # lockstep with the `assets` list they are declared against.
    # (The Claude Code plugin manifests are template-owned since v3.5.0's
    # include_claude_plugin scaffold — see _bump_claude_plugin_manifests
    # above — so nothing project-specific remains here.)
    # DOMAIN-MANIFESTS-END
    return 0


if __name__ == "__main__":
    sys.exit(main())
