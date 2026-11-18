"""Git write-strategy configuration for a markdown vault."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


def validate_group_timeouts(group_idle_s: float, group_max_age_s: float) -> None:
    """Reject deadlines that cannot be represented by a timer."""
    for name, value in (
        ("group_idle_s", group_idle_s),
        ("group_max_age_s", group_max_age_s),
    ):
        if not 0 < value <= threading.TIMEOUT_MAX:
            raise ConfigurationError(
                f"{name} must be positive and at most {threading.TIMEOUT_MAX}"
            )


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
    group_idle_s: float = 300
    group_max_age_s: float = 3600

    def __post_init__(self) -> None:
        """Validate sync cadences and group deadlines on construction (#638).

        Raises:
            ConfigurationError: If a sync cadence is negative or a group
                deadline is nonpositive or nonfinite.
        """
        validate_group_timeouts(self.group_idle_s, self.group_max_age_s)
        if self.push_delay_s < 0:
            raise ConfigurationError(
                f"push_delay_s must be >= 0, got {self.push_delay_s}"
            )
        if self.pull_interval_s < 0:
            raise ConfigurationError(
                f"pull_interval_s must be >= 0, got {self.pull_interval_s}"
            )
