#!/usr/bin/env bash
# SessionStart doctor for the markdown-vault-mcp plugin (#1042): when the
# server cannot start for configuration reasons, say so at session start —
# in-session, with an offer of help — instead of leaving a silent dead
# `Failed to connect` entry in /mcp. Healthy configs produce no output at
# all, so the hook is invisible unless something is actually wrong.
set -u

emit() {
  # $1 = one-line user-visible message, $2 = context for Claude
  python3 - "$1" "$2" <<'PYEOF' 2>/dev/null || printf '%s\n' "$1"
import json
import sys

print(
    json.dumps(
        {
            "systemMessage": sys.argv[1],
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": sys.argv[2],
            },
        }
    )
)
PYEOF
}

# Resolve the source dir the way the SERVER will. Since the userConfig
# screen (#1040), .mcp.json wires MARKDOWN_VAULT_MCP_SOURCE_DIR to
# ${user_config.source_dir}, so the plugin's stored config is the
# authoritative source: hook processes receive it directly as
# CLAUDE_PLUGIN_OPTION_SOURCE_DIR, with the persisted copy living under
# pluginConfigs[<plugin-id>].options in the user-scope settings file.
# The settings env block and the shell environment remain as legacy
# fallbacks for installs that predate the screen.
src="${CLAUDE_PLUGIN_OPTION_SOURCE_DIR:-}"
if [ -z "$src" ] && [ -f "$HOME/.claude/settings.json" ]; then
  src=$(python3 - <<'PYEOF' 2>/dev/null
import json
import os

path = os.path.expanduser("~/.claude/settings.json")
try:
    settings = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    settings = {}
value = ""
for key, cfg in (settings.get("pluginConfigs") or {}).items():
    if "markdown-vault-mcp" in key and isinstance(cfg, dict):
        value = (cfg.get("options") or {}).get("source_dir") or ""
        if value:
            break
if not value:
    value = (settings.get("env") or {}).get("MARKDOWN_VAULT_MCP_SOURCE_DIR", "")
print(value)
PYEOF
)
fi
if [ -z "$src" ]; then
  src="${MARKDOWN_VAULT_MCP_SOURCE_DIR:-}"
fi

if [ -z "$src" ]; then
  emit \
    "markdown-vault-mcp: no vault configured — the MCP server cannot start. Ask Claude to set up your vault to fix this." \
    "The markdown-vault-mcp plugin's MCP server is down because no vault directory is configured: the plugin's source_dir option is unset (no CLAUDE_PLUGIN_OPTION_SOURCE_DIR, nothing under pluginConfigs in ~/.claude/settings.json) and the legacy MARKDOWN_VAULT_MCP_SOURCE_DIR env fallbacks are empty too. If the user asks about the vault or wants it working, use the vault-setup skill to run the guided setup."
  exit 0
fi

if [ ! -d "$src" ]; then
  emit \
    "markdown-vault-mcp: configured vault directory does not exist ($src) — the MCP server cannot start. Ask Claude to repair the vault setup." \
    "The markdown-vault-mcp plugin's MCP server is down because its configured vault directory does not exist: MARKDOWN_VAULT_MCP_SOURCE_DIR=$src. The vault was likely moved or renamed. If the user asks about the vault or wants it working, use the vault-setup skill to re-point the configuration."
  exit 0
fi

# Healthy: stay silent.
exit 0
