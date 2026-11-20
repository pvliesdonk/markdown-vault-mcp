"""OKF v0.2 artifact conformance and export observations."""

import json
from dataclasses import asdict, dataclass, field
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


class PublicationRefused(ValueError):
    """Strict publication failed, with the completed artifact report."""

    def __init__(self, report: OkfExportReport) -> None:
        """Retain the report explaining why publication was refused.

        Args:
            report: Completed observations for the proposed artifact.
        """
        super().__init__("Strict publication refused; inspect the export report")
        self.report = report


class ExportCollisionError(ValueError):
    """An outside reference would address a different archive member."""


def refusal_document(exc: PublicationRefused | ExportCollisionError) -> bytes:
    """Serialize a publication refusal for a downloader or CLI caller.

    Args:
        exc: The strict or collision refusal.

    Returns:
        UTF-8 JSON; collision reports are null because no artifact was audited.
    """
    strict = isinstance(exc, PublicationRefused)
    return json.dumps(
        {
            "okf_export": "refused",
            "reason": "strict" if strict else "collision",
            "message": str(exc),
            "report": asdict(exc.report)
            if isinstance(exc, PublicationRefused)
            else None,
        },
        sort_keys=True,
    ).encode("utf-8")
