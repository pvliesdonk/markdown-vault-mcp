"""External-change-detection configuration (file watcher + webhook)."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class SyncConfig:
    """File-watcher + GitHub-webhook settings for external changes."""

    file_watcher_enabled: bool = True
    file_watcher_debounce_s: float = 2.0
    github_webhook_secret: str | None = None

    def __post_init__(self) -> None:
        """Validate a positive debounce on every construction path (#638).

        Raises:
            ConfigurationError: If ``file_watcher_debounce_s`` is not > 0.
        """
        if self.file_watcher_debounce_s <= 0:
            raise ConfigurationError(
                "file_watcher_debounce_s must be > 0, got "
                f"{self.file_watcher_debounce_s}"
            )

    @classmethod
    def from_env(cls, prefix: str) -> SyncConfig:
        """Construct SyncConfig by reading ``{prefix}_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated SyncConfig with defaults for unset vars.

        Raises:
            ConfigurationError: If ``FILE_WATCHER_DEBOUNCE_S`` is non-numeric or
                ``<= 0``.
        """
        from fastmcp_pvl_core import parse_bool

        from markdown_vault_mcp.config_sections._helpers import env, env_float

        raw_fw = env(prefix, "FILE_WATCHER")
        return cls(
            file_watcher_enabled=parse_bool(raw_fw) if raw_fw is not None else True,
            file_watcher_debounce_s=env_float(prefix, "FILE_WATCHER_DEBOUNCE_S", 2.0),
            github_webhook_secret=env(prefix, "GITHUB_WEBHOOK_SECRET") or None,
        )
