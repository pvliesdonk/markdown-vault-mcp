"""External-change-detection configuration (file watcher + webhook)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncConfig:
    """File-watcher + GitHub-webhook settings for external changes."""

    file_watcher_enabled: bool = True
    file_watcher_debounce_s: float = 2.0
    github_webhook_secret: str | None = None

    @classmethod
    def from_env(cls, prefix: str) -> SyncConfig:
        """Construct SyncConfig by reading ``{prefix}_*`` env vars.

        Invalid ``FILE_WATCHER_DEBOUNCE_S`` values (non-numeric or ``<= 0``)
        warn and reset to the default ``2.0``.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SyncConfig with defaults for unset vars.
        """
        from fastmcp_pvl_core import parse_bool

        from markdown_vault_mcp.config_sections._helpers import env

        raw_fw = env(prefix, "FILE_WATCHER")
        raw_deb = (env(prefix, "FILE_WATCHER_DEBOUNCE_S") or "").strip()
        debounce = 2.0
        if raw_deb:
            try:
                debounce = float(raw_deb)
            except ValueError:
                logger.warning("invalid FILE_WATCHER_DEBOUNCE_S=%r, using 2.0", raw_deb)
            else:
                if debounce <= 0:
                    logger.warning(
                        "FILE_WATCHER_DEBOUNCE_S=%r <= 0, using 2.0", raw_deb
                    )
                    debounce = 2.0
        return cls(
            file_watcher_enabled=parse_bool(raw_fw) if raw_fw is not None else True,
            file_watcher_debounce_s=debounce,
            github_webhook_secret=env(prefix, "GITHUB_WEBHOOK_SECRET") or None,
        )
