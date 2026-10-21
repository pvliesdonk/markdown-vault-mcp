#!/bin/sh
# Post-remove script: clean up venv and optionally remove system user.
# POSIX sh, not bash: rpm runs every scriptlet under /bin/sh whatever its
# shebang (nfpm's rpmpack sets the interpreter), and dpkg honours this line,
# so one dialect behaves the same in both packages.
# State data in /var/lib/markdown-vault-mcp is intentionally preserved.
set -eu

INSTALL_DIR="/opt/markdown-vault-mcp"
SERVICE_USER="markdown-vault-mcp"

# Remove the venv and install directory (not state data)
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
fi

# Reload systemd after unit file removal
systemctl daemon-reload 2>/dev/null || true

# Remove system user only on purge (.deb) or full remove (.rpm).
# $1 is "purge" on dpkg, or "0" (final remove) on rpm.
case "${1:-}" in
    purge|0)
        if getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
            userdel "$SERVICE_USER" 2>/dev/null || true
        fi
        if getent group "$SERVICE_USER" >/dev/null 2>&1; then
            groupdel "$SERVICE_USER" 2>/dev/null || true
        fi
        ;;
esac
