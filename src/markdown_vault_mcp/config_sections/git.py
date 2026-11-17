"""Git write-strategy configuration for a markdown vault."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitConfig:
    """Git auth, identity, and sync cadence (``MARKDOWN_VAULT_MCP_GIT_*``)."""

    token: str | None = None
    repo_url: str | None = None
    username: str = "x-access-token"
    push_delay_s: float = 30.0
    commit_name: str = "markdown-vault-mcp"
    commit_email: str = "noreply@markdown-vault-mcp"
    commit_name_claim: str | None = None
    commit_email_claim: str | None = None
    lfs: bool = True
    pull_interval_s: int = 600

    def __post_init__(self) -> None:
        """Validate non-negative sync cadences on every construction path (#638).

        Raises:
            ConfigurationError: If ``push_delay_s`` or ``pull_interval_s`` is
                negative.
        """
        if self.push_delay_s < 0:
            raise ConfigurationError(
                f"push_delay_s must be >= 0, got {self.push_delay_s}"
            )
        if self.pull_interval_s < 0:
            raise ConfigurationError(
                f"pull_interval_s must be >= 0, got {self.pull_interval_s}"
            )
