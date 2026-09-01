"""External-change-detection configuration (file watcher + webhooks)."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class SyncConfig:
    """File-watcher + push-webhook settings for external changes.

    ``file_watcher_root_floor`` (default True) controls the non-recursive
    ``source_dir`` floor watch. Disable it (env
    ``MARKDOWN_VAULT_MCP_FILE_WATCHER_ROOT_FLOOR=false``) on deployments that
    need zero ``source_dir``-rooted FSEvents registration (macOS TCC), accepting
    that root-level files are then only picked up by scans.

    GitLab carries two credential fields because it has two authentication
    mechanisms with different security properties, not one spelled two ways:
    ``gitlab_webhook_signing_token`` is the HMAC form (GitLab 19.0+) and
    ``gitlab_webhook_secret_token`` the plain-text one older versions offer.
    Setting both is what makes a migration possible — see
    :func:`markdown_vault_mcp._webhooks.gitlab_provider`.
    """

    file_watcher_enabled: bool = True
    file_watcher_debounce_s: float = 2.0
    file_watcher_root_floor: bool = True
    github_webhook_secret: str | None = None
    gitlab_webhook_signing_token: str | None = None
    gitlab_webhook_secret_token: str | None = None

    @property
    def webhook_configured(self) -> bool:
        """Whether any push webhook has credentials, so a host can drive pulls.

        The file watcher is mutually exclusive with webhook-driven pulls
        regardless of which host sends them, so the gate asks this rather than
        naming a provider.
        """
        return bool(
            self.github_webhook_secret
            or self.gitlab_webhook_signing_token
            or self.gitlab_webhook_secret_token
        )

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
