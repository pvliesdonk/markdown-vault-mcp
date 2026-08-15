#!/usr/bin/env bash
# Project-specific pre-release artifact assertions, run by the template's
# `Pre-release check` workflow after the shared mcpb build/smoke steps
# (see CLAUDE.md "Pre-release artifact smoke test"). VERSION holds the
# rendered version string and BUNDLE the packed .mcpb path (#351).
#
# Covers the Claude Code plugin channel, which is domain-owned in this
# repo: the two version-lockstep manifests (CLAUDE.md gate #7) and the
# shipped skill, plus the vault-specific mcpb screen invariants the
# generic smoke test cannot know about.
set -euo pipefail

fail() { echo "::error::$1"; exit 1; }

PLUGIN_JSON=".claude-plugin/plugin/.claude-plugin/plugin.json"
MCP_JSON=".claude-plugin/plugin/.mcp.json"

test -f "$PLUGIN_JSON" || fail "$PLUGIN_JSON missing"
test -f "$MCP_JSON" || fail "$MCP_JSON missing"
test -f ".claude-plugin/plugin/skills/vault-workflow/SKILL.md" \
  || fail "vault-workflow skill missing from plugin"
test -f ".claude-plugin/plugin/skills/vault-summarize/SKILL.md" \
  || fail "vault-summarize skill missing from plugin"
test -f ".claude-plugin/plugin/agents/vault-mapper.md" \
  || fail "vault-mapper agent missing from plugin"
test -f ".claude-plugin/plugin/skills/vault-setup/SKILL.md" \
  || fail "vault-setup skill missing from plugin"
test -x ".claude-plugin/plugin/scripts/doctor.sh" \
  || fail "doctor.sh missing or not executable in plugin"
jq -e '.hooks.SessionStart' ".claude-plugin/plugin/hooks/hooks.json" > /dev/null \
  || fail "hooks.json missing its SessionStart doctor entry"

jq -e '.name == "markdown-vault-mcp" and (.version | length > 0)' "$PLUGIN_JSON" > /dev/null \
  || fail "$PLUGIN_JSON missing name/version"

# The two plugin manifests move in lockstep: .mcp.json's uvx --from pin
# must name the same version plugin.json declares.
plugin_version=$(jq -r .version "$PLUGIN_JSON")
jq -e --arg v "$plugin_version" '
  .["markdown-vault-mcp"].args | index("--from") as $i
  | .[$i + 1] | contains("==" + $v)
' "$MCP_JSON" > /dev/null \
  || fail ".mcp.json --from pin does not match plugin.json version ${plugin_version}"

# Vault-specific mcpb screen invariants on the rendered manifest inside
# the packed bundle: the required vault picker is wired, and the
# ${DOCUMENTS} host placeholder survived the render.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
unzip -q "$BUNDLE" -d "$work"
jq -e '.user_config.source_dir.required == true and .user_config.source_dir.type == "directory"' \
  "$work/manifest.json" > /dev/null \
  || fail "bundle manifest lost the required source_dir directory picker"
jq -e '.server.mcp_config.env.MARKDOWN_VAULT_MCP_SOURCE_DIR == "${user_config.source_dir}"' \
  "$work/manifest.json" > /dev/null \
  || fail "bundle manifest env is not wired to user_config.source_dir"
grep -qF '${DOCUMENTS}' "$work/manifest.json" \
  || fail "\${DOCUMENTS} placeholder was expanded away in the bundle manifest"

echo "project-specific pre-release checks passed (plugin manifests at v${plugin_version}, mcpb screen wired)"
