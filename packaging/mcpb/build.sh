#!/usr/bin/env bash
# Build a markdown-vault-mcp .mcpb bundle locally.
#
# Usage:
#   VERSION=1.0.0 ./packaging/mcpb/build.sh
#
# With no VERSION set, builds a "dev" bundle for validation only.
#
# Note on scaffold drift:
#   packaging/mcpb/{manifest.json.in,pyproject.toml.in,src/server.py,build.sh}
#   are in the template's _skip_if_exists list, so future ``copier update``
#   runs will NOT re-render them.  When mcpb bumps manifest_version, when
#   the project picks a license (starter is UNLICENSED — you MUST choose
#   one before publishing), or when the mcpb CLI version is bumped
#   upstream, update these files manually.
set -euo pipefail

VERSION="${VERSION:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${REPO_ROOT}/packaging/mcpb/build"
DIST_DIR="${REPO_ROOT}/packaging/mcpb/dist"

# renovate: datasource=npm depName=@anthropic-ai/mcpb
MCPB_VERSION="2.1.2"

command -v mcpb >/dev/null 2>&1 || {
  echo "error: mcpb CLI not found. Install with:" >&2
  echo "  npm install -g @anthropic-ai/mcpb@${MCPB_VERSION}" >&2
  exit 1
}
command -v envsubst >/dev/null 2>&1 || {
  echo "error: envsubst not found (part of gettext). Install with:" >&2
  echo "  brew install gettext     # macOS" >&2
  echo "  apt install gettext-base # Debian/Ubuntu" >&2
  exit 1
}

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/src" "${DIST_DIR}"

# Restrict substitution to ${VERSION} only — other ${...} tokens in the template
# (e.g. ${DOCUMENTS}, ${user_config.*}) are runtime placeholders for the host.
VERSION="${VERSION}" envsubst '${VERSION}' < "${SCRIPT_DIR}/manifest.json.in" \
  > "${BUILD_DIR}/manifest.json"
VERSION="${VERSION}" envsubst '${VERSION}' < "${SCRIPT_DIR}/pyproject.toml.in" \
  > "${BUILD_DIR}/pyproject.toml"
cp "${SCRIPT_DIR}/src/server.py" "${BUILD_DIR}/src/server.py"

mcpb validate "${BUILD_DIR}/manifest.json"
mcpb pack "${BUILD_DIR}" "${DIST_DIR}/markdown-vault-mcp-${VERSION}.mcpb"

echo "built ${DIST_DIR}/markdown-vault-mcp-${VERSION}.mcpb"
