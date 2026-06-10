"""One-time HTTP transfer-link configuration (#622)."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class TransferConfig:
    """Settings for one-time upload/download transfer links.

    Attributes:
        ttl_default_s: Token lifetime applied when the caller omits one.
        ttl_max_s: Ceiling; a caller-requested TTL is clamped to this.
        max_upload_bytes: Per-upload size cap in bytes.
    """

    ttl_default_s: int = 3600
    ttl_max_s: int = 86400
    max_upload_bytes: int = 104857600  # 100 MiB

    def __post_init__(self) -> None:
        """Validate the TTL ordering and positivity invariants at construction.

        Raises:
            ConfigurationError: If any value is out of range or the TTL ordering
                is violated.
        """
        if self.ttl_default_s < 1:
            raise ConfigurationError(
                f"ttl_default_s must be >= 1, got {self.ttl_default_s}"
            )
        if self.ttl_max_s < self.ttl_default_s:
            raise ConfigurationError(
                f"ttl_max_s must be >= ttl_default_s, got ttl_max_s={self.ttl_max_s} "
                f"ttl_default_s={self.ttl_default_s}"
            )
        if self.max_upload_bytes < 1:
            raise ConfigurationError(
                f"max_upload_bytes must be >= 1, got {self.max_upload_bytes}"
            )

    @classmethod
    def from_env(cls, prefix: str) -> TransferConfig:
        """Construct TransferConfig by reading ``{prefix}_TRANSFER_*`` env vars.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated TransferConfig with defaults for unset vars.

        Raises:
            ConfigurationError: If any ``TRANSFER_*`` value is non-numeric or
                out of range.
        """
        from markdown_vault_mcp.config_sections._helpers import env_int

        return cls(
            ttl_default_s=env_int(prefix, "TRANSFER_TTL_DEFAULT_S", 3600),
            ttl_max_s=env_int(prefix, "TRANSFER_TTL_MAX_S", 86400),
            max_upload_bytes=env_int(prefix, "TRANSFER_MAX_UPLOAD_BYTES", 104857600),
        )
