"""Git write-strategy configuration for a markdown vault."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastmcp_pvl_core import parse_bool

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

    @classmethod
    def from_env(cls, prefix: str) -> GitConfig:
        """Construct GitConfig by reading ``{prefix}_GIT_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated GitConfig with defaults for unset vars.
        """
        from markdown_vault_mcp.config_sections._helpers import env, parse_float_env

        push_delay_s = parse_float_env(prefix, "GIT_PUSH_DELAY_S", 30.0)

        raw_pull = (env(prefix, "GIT_PULL_INTERVAL_S") or "").strip()
        pull_interval_s = 600
        if raw_pull:
            try:
                pull_interval_s = int(raw_pull)
            except ValueError:
                logger.warning("invalid GIT_PULL_INTERVAL_S=%r, using 600", raw_pull)
            else:
                if pull_interval_s < 0:
                    logger.warning(
                        "negative GIT_PULL_INTERVAL_S=%r, clamping to 0", raw_pull
                    )
                    pull_interval_s = 0

        raw_lfs = env(prefix, "GIT_LFS")
        return cls(
            token=env(prefix, "GIT_TOKEN") or None,
            repo_url=env(prefix, "GIT_REPO_URL") or None,
            username=env(prefix, "GIT_USERNAME") or "x-access-token",
            push_delay_s=push_delay_s,
            commit_name=env(prefix, "GIT_COMMIT_NAME") or "markdown-vault-mcp",
            commit_email=env(prefix, "GIT_COMMIT_EMAIL")
            or "noreply@markdown-vault-mcp",
            commit_name_claim=env(prefix, "GIT_COMMIT_NAME_CLAIM") or None,
            commit_email_claim=env(prefix, "GIT_COMMIT_EMAIL_CLAIM") or None,
            lfs=parse_bool(raw_lfs) if raw_lfs is not None else True,
            pull_interval_s=pull_interval_s,
        )
