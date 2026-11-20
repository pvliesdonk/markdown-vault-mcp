"""Configuration for the long-running tools' background jobs.

Ported from ``fastmcp_pvl_core._jobs.config`` 10.2.0 (#2308). ``JobsConfig``
holds the foreground soft deadline before a long-running call is promoted to
a background job, the retention TTL for job records, and the per-subject
live-job cap.

Reads use the literal ``env_float(prefix, "LITERAL")`` / ``env_int`` form, so
the config-surface generator sees every variable by walking
:meth:`JobsConfig.from_env`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from math import isfinite

from markdown_vault_mcp._env import env_float, env_int
from markdown_vault_mcp.exceptions import ConfigurationError

_DEFAULT_SOFT_DEADLINE_S = 25.0
_DEFAULT_RESULT_TTL_S = 3600.0
_DEFAULT_MAX_PER_SUBJECT = 256


@dataclass(frozen=True)
class JobsConfig:
    """Operator settings for the long-running tools' background jobs.

    All values must be positive and finite. Construct via :meth:`from_env`
    (the operator path) or directly (tests); ``__post_init__`` validates both.

    Attributes:
        soft_deadline_s: Foreground window before a long-running call is
            promoted to a background job. Keep it under the strictest client
            request timeout in play: the point is to answer before the client
            hangs up.
        result_ttl_s: How long a job record survives in the store, measured
            from record creation. A record past its TTL vanishes, and a later
            poll reports the id as unknown or expired.
        max_per_subject: Cap on live job records per calling subject, which
            bounds the store's growth per caller.
    """

    soft_deadline_s: float = field(
        default=_DEFAULT_SOFT_DEADLINE_S,
        metadata={
            "help": (
                "Seconds a long-running tool call may run in the foreground "
                "before it is promoted to a background job and a job handle "
                "is returned instead."
            ),
            "tags": ("jobs",),
            "wizard": {"group": "Jobs", "when": "server"},
        },
    )
    result_ttl_s: float = field(
        default=_DEFAULT_RESULT_TTL_S,
        metadata={
            "help": (
                "Seconds a background-job record (working or finished) is "
                "retained for polling before it expires from the store."
            ),
            "tags": ("jobs",),
            "wizard": {"group": "Jobs", "when": "server"},
        },
    )
    max_per_subject: int = field(
        default=_DEFAULT_MAX_PER_SUBJECT,
        metadata={
            "help": (
                "Maximum live background jobs per calling subject; further "
                "promotions are rejected until older records expire."
            ),
            "tags": ("jobs",),
            "wizard": {"group": "Jobs", "when": "server"},
        },
    )

    def __post_init__(self) -> None:
        """Reject a non-positive or non-finite value in any field.

        Raises:
            ConfigurationError: If a field is not a positive, finite number.
        """
        # Iterate the declared fields, not a hand-kept list, so a new numeric
        # field is covered without editing this loop.
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if not isfinite(value) or value <= 0:
                raise ConfigurationError(
                    f"JobsConfig.{f.name} must be a positive, finite "
                    f"number, got {value}"
                )

    @classmethod
    def from_env(cls, env_prefix: str) -> JobsConfig:
        """Load the jobs section from ``{env_prefix}_JOBS_*`` variables.

        Args:
            env_prefix: Env var prefix, no trailing underscore needed.

        Returns:
            A populated :class:`JobsConfig`.

        Raises:
            ConfigurationError: If a variable is set to a non-numeric,
                non-positive or out-of-range value; reads are strict, so an
                operator typo fails at load.
        """
        return cls(
            soft_deadline_s=env_float(
                env_prefix,
                "JOBS_SOFT_DEADLINE_S",
                _DEFAULT_SOFT_DEADLINE_S,
                strict=True,
            ),
            result_ttl_s=env_float(
                env_prefix,
                "JOBS_RESULT_TTL_S",
                _DEFAULT_RESULT_TTL_S,
                strict=True,
            ),
            max_per_subject=env_int(
                env_prefix,
                "JOBS_MAX_PER_SUBJECT",
                _DEFAULT_MAX_PER_SUBJECT,
                strict=True,
                minimum=1,
            ),
        )
