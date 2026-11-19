"""OKF v0.2 artifact conformance and export observations."""

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["conformant", "nonconformant", "incomplete"]


@dataclass(frozen=True)
class FormatFinding:
    """A format finding for an archive member."""

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
class ExportSnapshot:
    """Snapshot identity and paths whose working state needs attention.

    Paths are relative to the vault root. A commit identifies a Git snapshot;
    uncommitted paths differ from that commit, including later commits;
    inconsistent paths identify files changed or unreadable after capture.
    """

    commit: str | None = None
    uncommitted_paths: list[str] = field(default_factory=list)
    inconsistent_paths: list[str] = field(default_factory=list)


@dataclass
class OkfExportReport:
    """Artifact format verdict, omissions and separate export observations.

    Counts overlap when a path has more than one omission reason. Uninspected
    paths include inventory observations such as aliases and inspection bounds,
    and headers the exporter could not parse or re-encode. A path can also be
    transformed; neither observation identifies which fields changed. Selected
    inventory failures refuse export.
    Transformed paths, dependency sources, optional metadata, broken links,
    format findings and unchecked checks use final archive paths. Inventory
    observations retain vault-relative paths. Findings and unchecked checks
    determine the format verdict. Snapshot records the export consistency basis.
    """

    transformed: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    ambiguous_path: list[Dependency] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)
    local_policy: LocalPolicy = field(default_factory=LocalPolicy)
    format_findings: list[FormatFinding] = field(default_factory=list)
    uninspected: dict[str, str] = field(default_factory=dict)
    verdict: Verdict = "incomplete"
    unchecked: dict[str, list[str]] = field(default_factory=dict)
    optional_metadata: list[str] = field(default_factory=list)
    broken_links: list[Dependency] = field(default_factory=list)
    snapshot: ExportSnapshot = field(default_factory=ExportSnapshot)
