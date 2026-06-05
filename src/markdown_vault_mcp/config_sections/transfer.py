"""One-time HTTP transfer-link configuration (#622)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
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
