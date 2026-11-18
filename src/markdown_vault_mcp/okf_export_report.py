"""Partial OKF v0.2 export observations, not a conformance verdict."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FormatFinding:
    """A format observation for an exported concept candidate."""

    path: str
    reason: str


@dataclass
class LocalPolicy:
    """Last-observed index admission, not an assessment of exported bytes.

    For exported concept candidates, absence from both lists means the last
    observed index state was searchable, not that the current bytes are admitted.
    Unknown includes candidates whose index admission could not be determined.
    """

    basis: str = "last_observed_index"
    non_admitted: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


@dataclass
class OkfExportReport:
    """Omissions and partial v0.2 format observations for an export.

    Counts overlap when a path has more than one omission reason. Uninspected
    paths can be omitted when inventory identifies a symlink, hidden_symlink or nonregular
    entry, or included with metadata that exceeded an inspection bound. Other
    selected inventory failures refuse export.
    These observations do not establish conformance.
    """

    omitted: dict[str, int] = field(default_factory=dict)
    local_policy: LocalPolicy = field(default_factory=LocalPolicy)
    format_findings: list[FormatFinding] = field(default_factory=list)
    uninspected: dict[str, str] = field(default_factory=dict)
