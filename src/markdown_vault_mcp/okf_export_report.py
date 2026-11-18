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


@dataclass(frozen=True)
class Dependency:
    """A reported destination and its source archive path."""

    path: str
    target: str


@dataclass
class OkfExportReport:
    """Omissions and partial v0.2 format observations for an export.

    Counts overlap when a path has more than one omission reason. Uninspected
    paths can be included with metadata that exceeded an inspection bound.
    Selected inventory failures refuse export.
    Transformed paths and dependency sources are archive paths; other path
    observations retain vault-relative paths. These observations do not establish
    conformance.
    """

    transformed: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    ambiguous_path: list[Dependency] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)
    local_policy: LocalPolicy = field(default_factory=LocalPolicy)
    format_findings: list[FormatFinding] = field(default_factory=list)
    uninspected: dict[str, str] = field(default_factory=dict)
