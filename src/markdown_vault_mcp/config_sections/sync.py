"""External-change-detection configuration (file watcher + webhook)."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class SyncConfig:
    """File-watcher + GitHub-webhook settings for external changes.

    ``file_watcher_root_floor`` (default True) controls the non-recursive
    ``source_dir`` floor watch. Disable it (env
    ``MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR=false``) on deployments that
    need zero ``source_dir``-rooted FSEvents registration (macOS TCC), accepting
    that root-level files are then only picked up by scans.
    """

    file_watcher_enabled: bool = True
    file_watcher_debounce_s: float = 2.0
    file_watcher_root_floor: bool = True
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
