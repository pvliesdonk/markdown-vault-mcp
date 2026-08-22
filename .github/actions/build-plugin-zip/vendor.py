"""Vendor the built wheel into a staged plugin directory and repin `.mcp.json`.

Run by the `build-plugin-zip` composite action against a staging copy — never
against the tracked plugin directory.  Two rewrites, both of which must
succeed or the build fails rather than shipping a zip that installs and then
cannot start its server:

* ``plugin.json``'s ``version``.  The tracked file lags on rc and edge builds
  because ``scripts/stamp_manifests.py`` stamps it on stable releases only,
  so it sits at the last published stable the rest of the time.  Stamping the
  staged copy means the zip always states the version it actually carries.
* ``.mcp.json``'s ``--from`` pin, moved off PyPI and onto the vendored wheel.
  This is what makes the zip installable at a version PyPI has never seen,
  which is the whole reason the asset exists.

The bare-path spelling (``<path>.whl[extras]``) is deliberate.  The PEP 508
``pkg[extras] @ file://<path>`` form resolves too, but building a ``file://``
URL from a Windows plugin root is a trap this side-steps entirely.
``${CLAUDE_PLUGIN_ROOT}`` is substituted by Claude Code at launch.
"""

from __future__ import annotations

import json
import pathlib
import sys


class VendorError(RuntimeError):
    """A rewrite that cannot be performed. Never warn and continue."""


def servers_of(mcp: dict) -> dict:
    """Return the server map, tolerating both `.mcp.json` shapes.

    Claude Code loads servers written at the top level and servers wrapped in
    a ``mcpServers`` key — checked against the CLI, not assumed, because the
    plugin reference documents only the wrapped form.  Accepting both here
    keeps this script working whichever shape a project's scaffold carries.
    """
    inner = mcp.get("mcpServers")
    return inner if isinstance(inner, dict) else mcp


def _write_json(path: pathlib.Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def stamp_version(root: pathlib.Path, version: str) -> None:
    path = root / ".claude-plugin" / "plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise VendorError(f"{path}: top level must be a JSON object")
    manifest["version"] = version
    _write_json(path, manifest)


def repin(root: pathlib.Path, wheel: str) -> str:
    path = root / ".mcp.json"
    mcp = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(mcp, dict):
        raise VendorError(f"{path}: top level must be a JSON object")

    pinned = []
    for server in servers_of(mcp).values():
        if not isinstance(server, dict):
            continue
        args = server.get("args", [])
        for i, arg in enumerate(args):
            if arg != "--from" or i + 1 >= len(args):
                continue
            # `pkg[all]==1.2.3` -> extras `[all]`; `pkg==1.2.3` -> no extras.
            spec = args[i + 1].split("==", 1)[0]
            extras = spec[len(spec.split("[", 1)[0]) :]
            args[i + 1] = f"${{CLAUDE_PLUGIN_ROOT}}/wheels/{wheel}{extras}"
            pinned.append(args[i + 1])

    if len(pinned) != 1:
        raise VendorError(
            f"{path}: expected exactly one '--from' pin to repin, found {len(pinned)}"
        )
    _write_json(path, mcp)
    return pinned[0]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise VendorError(f"usage: {argv[0]} <staged-plugin-dir> <version>")
    root = pathlib.Path(argv[1])
    version = argv[2]

    wheels = sorted((root / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise VendorError(
            f"expected exactly one vendored wheel in {root / 'wheels'}, got "
            f"{[w.name for w in wheels]}"
        )

    stamp_version(root, version)
    spec = repin(root, wheels[0].name)
    print(f"vendor: plugin.json -> {version}; --from -> {spec}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except VendorError as exc:
        print(f"vendor: REFUSING to pack: {exc}", file=sys.stderr)
        sys.exit(1)
