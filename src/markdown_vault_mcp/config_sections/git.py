"""Git write-strategy configuration for a markdown vault."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
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
