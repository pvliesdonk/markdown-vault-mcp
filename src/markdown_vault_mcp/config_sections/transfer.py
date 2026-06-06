"""One-time HTTP transfer-link configuration (#622)."""

from __future__ import annotations

from dataclasses import dataclass


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
        """Validate the TTL ordering and positivity invariants at construction."""
        if self.ttl_default_s < 1:
            raise ValueError("ttl_default_s must be >= 1")
        if self.ttl_max_s < self.ttl_default_s:
            raise ValueError("ttl_max_s must be >= ttl_default_s")
        if self.max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be >= 1")

    @classmethod
    def from_env(cls, prefix: str) -> TransferConfig:
        """Construct TransferConfig by reading ``{prefix}_TRANSFER_*`` env vars.

        Invalid values warn and fall back to the default; out-of-range values
        are validated by ``__post_init__`` (raises ``ValueError``).

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated TransferConfig with defaults for unset vars.
        """
        from markdown_vault_mcp.config_sections._helpers import parse_int_env

        return cls(
            ttl_default_s=parse_int_env(prefix, "TRANSFER_TTL_DEFAULT_S", 3600),
            ttl_max_s=parse_int_env(prefix, "TRANSFER_TTL_MAX_S", 86400),
            max_upload_bytes=parse_int_env(
                prefix, "TRANSFER_MAX_UPLOAD_BYTES", 104857600
            ),
        )
