"""Configuration for the ``/transfer`` capability links.

Ported from ``fastmcp_pvl_core._transfer.config`` 10.2.0 (#2321).
``TransferConfig`` holds the link lifetimes, the post-success grace window, the
crashed-handler lease and the per-upload size cap.

Reads use the literal ``env_float(prefix, "LITERAL")`` / ``env_int`` form, so
the config-surface generator sees every variable by walking
:meth:`TransferConfig.from_env`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from math import isfinite

from markdown_vault_mcp._env import env_float, env_int
from markdown_vault_mcp.exceptions import ConfigurationError

_DEFAULT_TTL_DEFAULT_S = 3600.0  # 1 hour
_DEFAULT_TTL_MAX_S = 86_400.0  # 24 hours
_DEFAULT_GRACE_TTL_S = 60.0
_DEFAULT_LEASE_S = 60.0
_DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB


@dataclass(frozen=True)
class TransferConfig:
    """Operator settings for the transfer capability links.

    All durations are seconds and all values must be positive and finite; the
    default TTL must not exceed the max TTL. Construct via :meth:`from_env` (the
    operator path) or directly (tests). ``__post_init__`` validates both.

    Every field's wizard hint carries ``when: "server"``: the transfer route and
    tools mount only under an HTTP (server) deployment, so these settings are
    meaningful only there and a stdio/local config wizard gates them out.

    Attributes:
        ttl_default_s: Link lifetime when the caller omits one.
        ttl_max_s: Ceiling; a caller-requested TTL is clamped to this.
        grace_ttl_s: Post-success grace window — ``complete`` shrinks a token's
            TTL to ``min(remaining, grace_ttl_s)`` so a served-but-stalled
            transfer can retry within it.
        lease_s: Crashed-handler reclaim window for an ``in_flight`` reservation.
        max_upload_bytes: Per-upload size cap.
    """

    ttl_default_s: float = field(
        default=_DEFAULT_TTL_DEFAULT_S,
        metadata={
            "help": (
                "Link lifetime in seconds when the caller requests no explicit TTL."
            ),
            "tags": ("transfer",),
            "wizard": {"group": "Transfer", "when": "server"},
        },
    )
    ttl_max_s: float = field(
        default=_DEFAULT_TTL_MAX_S,
        metadata={
            "help": ("Ceiling in seconds a caller-requested link TTL is clamped to."),
            "tags": ("transfer",),
            "wizard": {"group": "Transfer", "when": "server"},
        },
    )
    grace_ttl_s: float = field(
        default=_DEFAULT_GRACE_TTL_S,
        metadata={
            "help": (
                "Post-success grace window in seconds: a served token's TTL "
                "shrinks to this so a stalled transfer can retry within it."
            ),
            "tags": ("transfer",),
            "wizard": {"group": "Transfer", "when": "server"},
        },
    )
    lease_s: float = field(
        default=_DEFAULT_LEASE_S,
        metadata={
            "help": (
                "Crashed-handler reclaim window in seconds for an in-flight "
                "reservation."
            ),
            "tags": ("transfer",),
            "wizard": {"group": "Transfer", "when": "server"},
        },
    )
    max_upload_bytes: int = field(
        default=_DEFAULT_MAX_UPLOAD_BYTES,
        metadata={
            "help": "Maximum size in bytes of a single upload.",
            "tags": ("transfer",),
            "wizard": {"group": "Transfer", "when": "server"},
        },
    )

    def __post_init__(self) -> None:
        """Reject a non-positive or non-finite value, or a default above the max.

        Raises:
            ConfigurationError: If a field is not a positive, finite number, or
                ``ttl_default_s`` exceeds ``ttl_max_s``.
        """
        # Iterate the declared fields (not a hand-maintained list) so a new
        # numeric field is covered without editing this loop. Every field is a
        # positive, finite number; a non-numeric field must not be added without
        # extending this check (``isfinite`` / ``> 0`` assume a real number).
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value <= 0:
                raise ConfigurationError(
                    f"TransferConfig.{f.name} must be a positive, finite "
                    f"number, got {value}"
                )
        if self.ttl_default_s > self.ttl_max_s:
            raise ConfigurationError(
                f"TransferConfig.ttl_default_s ({self.ttl_default_s}) must not "
                f"exceed ttl_max_s ({self.ttl_max_s})"
            )

    @classmethod
    def from_env(cls, env_prefix: str) -> TransferConfig:
        """Read the transfer env section under *env_prefix*.

        Each var is parsed strictly (a malformed value raises
        :class:`ConfigurationError` naming the env var) and falls back to a
        built-in default when unset; :meth:`__post_init__` then validates the
        result. A well-formed but out-of-range value (non-positive, or a default
        exceeding the max) is caught there and surfaces under the **dataclass
        field name**, not the env-var suffix.

        Args:
            env_prefix: Env var prefix, no trailing underscore needed.

        Returns:
            A populated :class:`TransferConfig`.

        Raises:
            ConfigurationError: If a variable is malformed or out of range.
        """
        return cls(
            ttl_default_s=env_float(
                env_prefix,
                "TRANSFER_TTL_DEFAULT_S",
                _DEFAULT_TTL_DEFAULT_S,
                strict=True,
            ),
            ttl_max_s=env_float(
                env_prefix, "TRANSFER_TTL_MAX_S", _DEFAULT_TTL_MAX_S, strict=True
            ),
            grace_ttl_s=env_float(
                env_prefix, "TRANSFER_GRACE_TTL_S", _DEFAULT_GRACE_TTL_S, strict=True
            ),
            lease_s=env_float(
                env_prefix, "TRANSFER_LEASE_S", _DEFAULT_LEASE_S, strict=True
            ),
            max_upload_bytes=env_int(
                env_prefix,
                "TRANSFER_MAX_UPLOAD_BYTES",
                _DEFAULT_MAX_UPLOAD_BYTES,
                strict=True,
            ),
        )
