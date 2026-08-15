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

# Resolve the source dir the way the SERVER will: the settings-file env
# block reaches the server process and wins over the shell environment, so
# it is checked FIRST, with the shell value only as fallback — checking the
# shell first would let the doctor validate a path the server never uses
# when both are set and disagree.
src=""
if [ -f "$HOME/.claude/settings.json" ]; then
  src=$(python3 - <<'PYEOF' 2>/dev/null
import json
import os

path = os.path.expanduser("~/.claude/settings.json")
try:
    settings = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    settings = {}
print(settings.get("env", {}).get("MARKDOWN_VAULT_MCP_SOURCE_DIR", ""))
PYEOF
)
fi
if [ -z "$src" ]; then
  src="${MARKDOWN_VAULT_MCP_SOURCE_DIR:-}"
fi

if [ -z "$src" ]; then
  emit \
    "markdown-vault-mcp: no vault configured — the MCP server cannot start. Ask Claude to set up your vault to fix this." \
    "The markdown-vault-mcp plugin's MCP server is down because MARKDOWN_VAULT_MCP_SOURCE_DIR is not set (neither in the environment nor in ~/.claude/settings.json's env block). If the user asks about the vault or wants it working, use the vault-setup skill to run the guided setup."
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
