#!/bin/sh
# Pre-remove script: stop and disable the service.
# POSIX sh, not bash: rpm runs every scriptlet under /bin/sh whatever its
# shebang (nfpm's rpmpack sets the interpreter), and dpkg honours this line,
# so one dialect behaves the same in both packages.
set -eu

SERVICE_NAME="markdown-vault-mcp"

# On upgrade the package manager runs the old prerm before installing the new
# version.  Skip stop/disable so the postinstall restart logic can handle it.
# Debian passes "upgrade", RPM passes "1" (packages remaining after removal).
case "${1:-}" in
    upgrade|1)
        exit 0
        ;;
esac

# Stop the service if running or in failed state
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null || systemctl is-failed --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi

# Disable the service
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl disable "$SERVICE_NAME"
fi
