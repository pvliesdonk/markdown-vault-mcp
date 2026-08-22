#!/bin/bash
# Post-install script: create venv and install markdown-vault-mcp from PyPI.
set -eu

INSTALL_DIR="/opt/markdown-vault-mcp"
VENV_DIR="${INSTALL_DIR}/venv"
SERVICE_USER="markdown-vault-mcp"

# Determine package version — set by nfpm via VERSION env var, or read
# from the installed package metadata as fallback.
PKG_VERSION="${VERSION:-}"

# Create install directory
mkdir -p "$INSTALL_DIR"

# Create or update the virtual environment
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

# DOMAIN-POSTINSTALL-EXTRAS-START — set EXTRAS to this project's extras
# specifier (e.g. `[all]`) so the Linux packages install the same dependency
# set as the container; keep it in step with the `--extra` flags inside the
# Dockerfile's DOCKERFILE-UV-EXTRAS block. Kept across copier update.
#
# One variable rather than three edits, for the reason the Dockerfile block
# gives about its two layers: the three installs below are the rc, stable and
# unversioned paths of the same package, and a project that added an extra to
# only some of them would ship a different dependency set depending on which
# branch its install took.
EXTRAS="[all]"
# DOMAIN-POSTINSTALL-EXTRAS-END

# Upgrade pip and install the package
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

if [ -n "$PKG_VERSION" ]; then
    case "$PKG_VERSION" in
        *-rc.*)
            # Install the wheel attached to this version's own GitHub
            # release. rc wheels do reach PyPI, but the .deb/.rpm shipped
            # in that release then installs from the same immutable
            # per-version channel it came from, so a candidate package
            # stays installable independently of its PyPI publish. The
            # wheel filename carries the PEP 440 canonical spelling
            # (-rc.N -> rcN).
            canonical="$(printf '%s' "$PKG_VERSION" | sed 's/-rc\./rc/')"
            "${VENV_DIR}/bin/pip" install --quiet \
                "markdown-vault-mcp${EXTRAS} @ https://github.com/pvliesdonk/markdown-vault-mcp/releases/download/v${PKG_VERSION}/markdown_vault_mcp-${canonical}-py3-none-any.whl"
            ;;
        *)
            "${VENV_DIR}/bin/pip" install --quiet "markdown-vault-mcp${EXTRAS}==${PKG_VERSION}"
            ;;
    esac
else
    "${VENV_DIR}/bin/pip" install --quiet "markdown-vault-mcp${EXTRAS}"
fi

# Ensure config directory exists
mkdir -p /etc/markdown-vault-mcp

# Copy example env if no config exists yet
if [ ! -f /etc/markdown-vault-mcp/env ]; then
    if [ -f /etc/markdown-vault-mcp/env.example ]; then
        cp /etc/markdown-vault-mcp/env.example /etc/markdown-vault-mcp/env
    fi
fi

# Restrict env file permissions — it may contain secrets (tokens, API keys).
if [ -f /etc/markdown-vault-mcp/env ]; then
    chmod 600 /etc/markdown-vault-mcp/env
fi

# Reload systemd to pick up the unit file.
# Note: the service is intentionally NOT enabled here — start-on-boot requires
# explicit opt-in by the administrator via: systemctl enable markdown-vault-mcp
systemctl daemon-reload 2>/dev/null || true

# On upgrade, restart the service if it's already running so the new version is loaded.
if systemctl is-active --quiet markdown-vault-mcp 2>/dev/null; then
    systemctl restart markdown-vault-mcp
fi
