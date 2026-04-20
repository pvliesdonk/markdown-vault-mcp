"""Smoke tests for the Claude Desktop .mcpb bundle and Claude Code plugin.

These tests do not run the packaged server — they assert that the packaging
files are syntactically valid and that invariants the release workflow
depends on (version strings, import paths) stay consistent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCPB_DIR = REPO_ROOT / "packaging" / "mcpb"
PLUGIN_DIR = REPO_ROOT / ".claude-plugin" / "plugin"

# Stable `X.Y.Z` or python-semantic-release prerelease format `X.Y.Z-rc.N`.
# The release workflow bumps the manifests for every release including RCs
# (via PSR's build_command), so main may carry an RC version between stable
# releases.
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-rc\.\d+)?")


def test_cli_main_import_target_exists() -> None:
    """The mcpb shim imports markdown_vault_mcp.cli.main — make sure it exists."""
    from markdown_vault_mcp.cli import main

    assert callable(main)


def test_mcpb_server_shim_calls_main_serve() -> None:
    """The shim must import cli.main and ensure 'serve' is in sys.argv."""
    shim = MCPB_DIR / "src" / "server.py"
    assert shim.exists(), f"missing shim at {shim}"
    content = shim.read_text(encoding="utf-8")
    assert "from markdown_vault_mcp.cli import main" in content
    # cli.main() parses sys.argv; the shim must inject "serve" rather than
    # passing it as a positional argument (main takes no positional args).
    assert "serve" in content
    assert "sys.argv" in content


def _load_manifest_template() -> dict:
    """Load the mcpb manifest template with ${VERSION} replaced by a literal."""
    template = (MCPB_DIR / "manifest.json.in").read_text(encoding="utf-8")
    # Guard: ${DOCUMENTS} is a runtime placeholder for the host's document
    # directory. It must NOT be consumed by envsubst during the build (which
    # only substitutes ${VERSION}). If someone accidentally adds envsubst
    # without the variable-list argument, ${DOCUMENTS} becomes empty and
    # the default vault path in every released bundle becomes "/Vault".
    assert "${DOCUMENTS}" in template, (
        "manifest template must contain ${DOCUMENTS} as a runtime placeholder; "
        "if it was removed, restore it or this test is no longer needed"
    )
    rendered = template.replace("${VERSION}", "0.0.0-test")
    # After the VERSION substitution, ${DOCUMENTS} must still be present.
    assert "${DOCUMENTS}" in rendered, (
        "${DOCUMENTS} was lost during template rendering — "
        "envsubst must be called with '${VERSION}' argument to restrict substitution"
    )
    return json.loads(rendered)


def test_mcpb_manifest_template_valid_and_complete() -> None:
    """The mcpb manifest must parse and carry the fields the spec requires."""
    manifest = _load_manifest_template()

    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "markdown-vault-mcp"
    assert manifest["version"] == "0.0.0-test"

    server = manifest["server"]
    assert server["type"] == "uv"
    assert server["entry_point"] == "src/server.py"

    # mcp_config must NOT use --from . (local source dir) — that would fail at runtime in
    # an installed bundle.  If command is present it must reference the PyPI package by name.
    mcp_config = server["mcp_config"]
    if "args" in mcp_config:
        assert "--from" not in mcp_config["args"] or "." not in mcp_config["args"], (
            "mcp_config.args must not use '--from .' (local source); "
            "use '--from markdown-vault-mcp[all]==${VERSION}' instead"
        )
    env = server["mcp_config"]["env"]
    # The one truly required env var must be wired to the form.
    assert env["MARKDOWN_VAULT_MCP_SOURCE_DIR"] == "${user_config.source_dir}"

    user_config = manifest["user_config"]
    assert user_config["source_dir"]["required"] is True
    assert user_config["source_dir"]["type"] == "directory"
    # Sensitive fields must be marked so the host stores them in the keychain.
    assert user_config["openai_api_key"]["sensitive"] is True
    assert user_config["git_token"]["sensitive"] is True


def test_mcpb_pyproject_template_pins_versioned_package() -> None:
    """The bundle pyproject must pin markdown-vault-mcp[all] to the same VERSION."""
    template = (MCPB_DIR / "pyproject.toml.in").read_text(encoding="utf-8")
    assert "${VERSION}" in template, "template must use ${VERSION} placeholder"
    # The dep line should pin [all] extras to the same version.
    assert "markdown-vault-mcp[all]==${VERSION}" in template
    assert 'requires-python = ">=3.10"' in template


def _load_plugin_json() -> dict:
    """Load the Claude Code plugin.json metadata file."""
    path = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_claude_code_plugin_json_shape() -> None:
    """plugin.json must carry the expected name, repo, and a concrete version."""
    plugin = _load_plugin_json()
    assert plugin["name"] == "markdown-vault-mcp"
    assert plugin["repository"] == "https://github.com/pvliesdonk/markdown-vault-mcp"
    assert plugin["license"] == "MIT"

    # Version must look like a real semver (stable or RC) — not a template literal.
    version = plugin["version"]
    assert version != "${VERSION}"
    assert _VERSION_RE.fullmatch(version), (
        f"expected X.Y.Z or X.Y.Z-rc.N, got {version!r}"
    )


def _load_plugin_mcp_json() -> dict:
    """Load the Claude Code .mcp.json server launch config."""
    path = PLUGIN_DIR / ".mcp.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_mcp_json_pinned_and_matches_plugin_version() -> None:
    """.mcp.json must pin --from markdown-vault-mcp[all]==<X.Y.Z[-rc.N]> and match plugin.json."""
    mcp_cfg = _load_plugin_mcp_json()
    entry = mcp_cfg["markdown-vault-mcp"]
    assert entry["command"] == "uvx"

    args = entry["args"]
    assert "--from" in args, f"args must include --from, got {args}"
    from_index = args.index("--from")
    spec = args[from_index + 1]
    match = re.fullmatch(rf"markdown-vault-mcp\[all\]==({_VERSION_RE.pattern})", spec)
    assert match, f"unexpected --from spec: {spec!r}"

    plugin_version = _load_plugin_json()["version"]
    assert match.group(1) == plugin_version, (
        f".mcp.json pinned to {match.group(1)} but plugin.json is {plugin_version}"
    )

    env = entry["env"]
    assert "MARKDOWN_VAULT_MCP_SOURCE_DIR" in env
