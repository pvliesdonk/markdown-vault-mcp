"""Partial OKF v0.2 export observations, not a conformance verdict."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FormatFinding:
    """A format observation for an exported concept candidate."""

    path: str
    reason: str


@dataclass
class LocalPolicy:
    """Local index admission observations, separate from format findings."""

    non_admitted: list[str] = field(default_factory=list)


@dataclass
class OkfExportReport:
    """Omissions and partial v0.2 format observations for an export.

    Counts overlap when a path has more than one omission reason. Unavailable
    enumeration and bounded metadata observations do not establish conformance.
    """

    omitted: dict[str, int] = field(default_factory=dict)
    local_policy: LocalPolicy = field(default_factory=LocalPolicy)
    format_findings: list[FormatFinding] = field(default_factory=list)
    uninspected: dict[str, str] = field(default_factory=dict)
    enumeration_incomplete: bool = False
