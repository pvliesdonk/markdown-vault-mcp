"""Assert an unpacked plugin zip is loadable and self-contained.

Run by the `build-plugin-zip` composite action against a freshly unzipped
copy of the artifact it just packed, so the assertions read the bytes that
will actually ship rather than the staging tree that produced them.

The check that matters is the last one.  A `--from` pin that survived the
repin still names a PyPI version, which installs fine from a stable tag and
fails on every rc and edge build — the exact class of failure this asset
exists to remove, and one that no structural check on the archive would
otherwise catch.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from vendor import servers_of

PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}/wheels/"


class VerifyError(RuntimeError):
    """An artifact that must not ship."""


def launch_spec(mcp: dict) -> str:
    specs = []
    for server in servers_of(mcp).values():
        if not isinstance(server, dict):
            continue
        args = server.get("args", [])
        specs += [
            args[i + 1]
            for i, arg in enumerate(args)
            if arg == "--from" and i + 1 < len(args)
        ]
    if len(specs) != 1:
        raise VerifyError(
            f".mcp.json: expected exactly one '--from' spec, found {len(specs)}"
        )
    return specs[0]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise VerifyError(f"usage: {argv[0]} <unpacked-dir> <version>")
    root = pathlib.Path(argv[1])
    version = argv[2]

    # `.claude-plugin/plugin.json` at the archive root is what makes the zip
    # loadable at all — by `claude --plugin-url`, by `--plugin-dir`, and by a
    # marketplace `archive` source.
    manifest_path = root / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise VerifyError(".claude-plugin/plugin.json missing from the archive root")
    mcp_path = root / ".mcp.json"
    if not mcp_path.is_file():
        raise VerifyError(".mcp.json missing from the archive")

    packed = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
    if packed != version:
        raise VerifyError(f"plugin.json version {packed!r} != {version!r}")

    spec = launch_spec(json.loads(mcp_path.read_text(encoding="utf-8")))
    if not spec.startswith(PLUGIN_ROOT_VAR):
        raise VerifyError(f"--from is not pinned to a vendored wheel: {spec}")
    # Strip the leading ${CLAUDE_PLUGIN_ROOT}/ and any trailing [extras].
    rel = spec[len("${CLAUDE_PLUGIN_ROOT}/") :].split("[", 1)[0]
    if not (root / rel).is_file():
        raise VerifyError(f"--from names {rel}, which is not in the archive")

    print(f"verify: plugin {packed} launches from {rel}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except VerifyError as exc:
        print(f"verify: REFUSING to publish: {exc}", file=sys.stderr)
        sys.exit(1)
