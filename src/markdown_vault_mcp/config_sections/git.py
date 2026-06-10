"""Git write-strategy configuration for a markdown vault."""

from __future__ import annotations

from dataclasses import dataclass

from fastmcp_pvl_core import parse_bool

from markdown_vault_mcp.exceptions import ConfigurationError


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

    @classmethod
    def from_env(cls, prefix: str) -> GitConfig:
        """Construct GitConfig by reading ``{prefix}_GIT_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated GitConfig with defaults for unset vars.

        Raises:
            ConfigurationError: If ``GIT_PUSH_DELAY_S``/``GIT_PULL_INTERVAL_S``
                is non-numeric or negative.
        """
        from markdown_vault_mcp.config_sections._helpers import env, env_float, env_int

        raw_lfs = env(prefix, "GIT_LFS")
        return cls(
            token=env(prefix, "GIT_TOKEN") or None,
            repo_url=env(prefix, "GIT_REPO_URL") or None,
            username=env(prefix, "GIT_USERNAME") or "x-access-token",
            push_delay_s=env_float(prefix, "GIT_PUSH_DELAY_S", 30.0),
            commit_name=env(prefix, "GIT_COMMIT_NAME") or "markdown-vault-mcp",
            commit_email=env(prefix, "GIT_COMMIT_EMAIL")
            or "noreply@markdown-vault-mcp",
            commit_name_claim=env(prefix, "GIT_COMMIT_NAME_CLAIM") or None,
            commit_email_claim=env(prefix, "GIT_COMMIT_EMAIL_CLAIM") or None,
            lfs=parse_bool(raw_lfs) if raw_lfs is not None else True,
            pull_interval_s=env_int(prefix, "GIT_PULL_INTERVAL_S", 600),
        )
