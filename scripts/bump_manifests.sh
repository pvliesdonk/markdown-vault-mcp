#!/usr/bin/env bash
# Bump versioned manifests to match the semantic-release version.
#
# Invoked by python-semantic-release via `[tool.semantic_release] build_command`.
# PSR sets NEW_VERSION in the environment and, because the three manifest
# paths are listed in `[tool.semantic_release] assets`, PSR stages and commits
# them together with pyproject.toml + CHANGELOG.md as the single release
# commit — which is the commit it then tags. This keeps the tag, the manifest
# bumps, and the pyproject version atomic (no force-retag, no race for
# release-triggered workflows).
set -euo pipefail

: "${NEW_VERSION:?NEW_VERSION must be set (python-semantic-release build_command env)}"

V="$NEW_VERSION"

# server.json — top-level version, PyPI package version, OCI tag identifier.
# Replace only the `:v<old>` suffix of the OCI identifier so forks/renames
# keep their own `ghcr.io/<owner>/<image>` base.
jq --arg v "$V" '
  .version = $v
  | .packages |= map(
      if .registryType == "pypi" then .version = $v
      elif .registryType == "oci" then .identifier |= sub(":v[^:]+$"; ":v" + $v)
      else . end
    )
' server.json > server.json.tmp
mv server.json.tmp server.json

# Claude Code plugin.json — plugin version (lockstep with package).
jq --arg v "$V" '.version = $v' \
  .claude-plugin/plugin/.claude-plugin/plugin.json > plugin.json.tmp
mv plugin.json.tmp .claude-plugin/plugin/.claude-plugin/plugin.json

# Claude Code .mcp.json — pin uvx --from spec to the released version.
jq --arg v "$V" '
  ."markdown-vault-mcp".args = [
    "--from", ("markdown-vault-mcp[all]==" + $v),
    "markdown-vault-mcp", "serve"
  ]
' .claude-plugin/plugin/.mcp.json > mcp.json.tmp
mv mcp.json.tmp .claude-plugin/plugin/.mcp.json
