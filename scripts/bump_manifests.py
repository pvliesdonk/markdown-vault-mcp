#!/usr/bin/env python3
"""Bump versioned manifests to match the semantic-release version.

Invoked by python-semantic-release via `[tool.semantic_release] build_command`.
PSR sets ``NEW_VERSION`` in the environment and, because the three manifest
paths are listed in ``[tool.semantic_release] assets``, PSR stages and commits
them together with ``pyproject.toml`` + ``CHANGELOG.md`` as the single release
commit — which is the commit it then tags.

The script runs inside PSR's Docker action container (python:3.14-slim), which
has Python but no ``jq`` — hence Python rather than a shell+jq wrapper.
"""

from __future__ import annotations

import json
import os
import re
import sys
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


def main() -> int:
    version = os.environ.get("NEW_VERSION")
    if not version:
        print(
            "NEW_VERSION must be set (python-semantic-release build_command env)",
            file=sys.stderr,
        )
        return 1

    # server.json: top-level version, PyPI package version, OCI tag suffix.
    # Replace only the ``:v<old>`` suffix of the OCI identifier so forks/renames
    # keep their own ``ghcr.io/<owner>/<image>`` base.
    server_path = Path("server.json")
    server = _load(server_path)
    server["version"] = version
    for pkg in server.get("packages", []):
        if pkg.get("registryType") == "pypi":
            pkg["version"] = version
        elif pkg.get("registryType") == "oci":
            pkg["identifier"] = re.sub(r":v[^:]+$", f":v{version}", pkg["identifier"])
    _dump(server_path, server)

    # Claude Code plugin.json: plugin version, lockstep with the package.
    plugin_path = Path(".claude-plugin/plugin/.claude-plugin/plugin.json")
    plugin = _load(plugin_path)
    plugin["version"] = version
    _dump(plugin_path, plugin)

    # Claude Code .mcp.json: pin uvx --from spec to the released version.
    mcp_path = Path(".claude-plugin/plugin/.mcp.json")
    mcp = _load(mcp_path)
    mcp["markdown-vault-mcp"]["args"] = [
        "--from",
        f"markdown-vault-mcp[all]=={version}",
        "markdown-vault-mcp",
        "serve",
    ]
    _dump(mcp_path, mcp)

    return 0


if __name__ == "__main__":
    sys.exit(main())
