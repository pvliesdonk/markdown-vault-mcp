"""OKF (Open Knowledge Format) support: detection and read-side annotations.

OKF (`GoogleCloudPlatform/knowledge-catalog`, ``okf/SPEC.md``) is a bundle
convention over markdown-with-frontmatter. This module implements phase 1 of
`docs/design/okf.md`: the vault-side detection probe (``okf_version`` declared
in the bundle-root ``index.md``) and the pure derivation of per-note read
annotations (``type`` / ``status`` / staleness / trust tier).

Trust model (design §2): the vault-side declaration only ever enables
*read* semantics and advisory guidance — never write behavior. Detection is
pure disk I/O with zero index coupling (the :class:`OkfDetector` mirrors
``conventions.ConventionsResolver``), so it works before the index is built
and reflects a mid-session declaration on the next call.

The OKF field vocabulary is deliberately kept as *data* (module constants)
rather than spread through code: the spec is pre-1.0 with one breaking
rename already behind it, so a future revision should be a table edit.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import frontmatter as fm
import yaml

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# --- OKF v0.2 field-mapping table (data, not code) --------------------------

#: Spec revisions this implementation knows; anything else logs a warning
#: and is still treated as detected (permissive-consumer behavior extends
#: to the marker itself).
KNOWN_OKF_VERSIONS: tuple[str, ...] = ("0.1", "0.2")

#: Scalar frontmatter keys promoted into ``document_tags`` when detection
#: is on (feeds the phase-2 filters; harmless to index ahead of them).
OKF_INDEXED_FIELDS: tuple[str, ...] = ("type", "status", "stale_after")

#: Lifecycle vocabulary (``status``); absent means ``stable`` per spec.
OKF_STATUS_DEFAULT = "stable"
OKF_STATUS_VALUES: tuple[str, ...] = ("draft", "stable", "deprecated")

#: Trust tiers, least to most trusted.
TRUST_UNVERIFIED = "unverified"
TRUST_MACHINE = "machine-confirmed"
TRUST_HUMAN = "human-reviewed"

#: ``verified[].by`` prefix that marks a human verifier.
_HUMAN_ACTOR_PREFIX = "human:"

#: Reserved bundle filenames (navigation, not concepts). Not consumed in
#: phase 1 — staged for the conformance audit (#962: reserved files are
#: exempt from the ``type`` rule) and ranking downweights (#965).
OKF_RESERVED_FILENAMES: tuple[str, ...] = ("index.md", "log.md")

#: Bundle-root file allowed to carry ``okf_version``.
_ROOT_INDEX = "index.md"

#: Read cap for the detection probe, mirroring the conventions resolver:
#: bounds I/O and parsing on a pathological root ``index.md``.
_MAX_READ_CHARS = 65536


@dataclass(frozen=True)
class OkfState:
    """Snapshot of OKF detection for one probe.

    Attributes:
        mode: Configured mode (``"auto"`` / ``"off"`` / ``"on"``).
        declared_version: ``okf_version`` value from the root ``index.md``
            frontmatter, or ``None`` when absent/unreadable.
        active: Whether read semantics are in effect — declared under
            ``auto``, always under ``on``, never under ``off``.
    """

    mode: str
    declared_version: str | None
    active: bool


class OkfDetector:
    """Probe the vault's OKF declaration from disk on demand.

    Pure disk I/O with zero index coupling (the ``ConventionsResolver``
    pattern): each :meth:`state` call reads at most the first
    :data:`_MAX_READ_CHARS` characters of the root ``index.md``, so
    detection works before the index exists (managed-git mode) and a
    mid-session declaration takes effect on the next call.

    Args:
        source_dir: Root directory of the markdown vault.
        mode: Configured OKF mode — ``"auto"`` (follow the declaration),
            ``"off"`` (never active), or ``"on"`` (active regardless).
    """

    def __init__(self, source_dir: Path, mode: str = "auto") -> None:
        self._source_dir = source_dir
        self._mode = mode
        self._warned_versions: set[str] = set()

    @property
    def mode(self) -> str:
        """The configured OKF mode."""
        return self._mode

    def state(self) -> OkfState:
        """Return the current detection snapshot.

        Returns:
            The mode, declared version (probed unless mode is ``"off"``),
            and whether read semantics are active.
        """
        if self._mode == "off":
            return OkfState(mode=self._mode, declared_version=None, active=False)
        version = self._probe_declared_version()
        # Warn-once dedup is best-effort: concurrent first probes may each
        # log the warning (set membership is checked without a lock). The
        # worst case is a duplicate log line, which does not justify
        # synchronizing a read path.
        if (
            version is not None
            and version not in KNOWN_OKF_VERSIONS
            and version not in self._warned_versions
        ):
            self._warned_versions.add(version)
            logger.warning(
                "okf_unknown_version declared=%s known=%s",
                version,
                ",".join(KNOWN_OKF_VERSIONS),
            )
        active = self._mode == "on" or version is not None
        return OkfState(mode=self._mode, declared_version=version, active=active)

    def _probe_declared_version(self) -> str | None:
        """Read ``okf_version`` from the root ``index.md`` frontmatter.

        Returns:
            The declared version as a string (YAML scalars are normalized,
            so an unquoted ``0.2`` still reads as ``"0.2"``), or ``None``
            when the file is missing, unreadable, has no parseable
            frontmatter, or carries no usable ``okf_version``.
        """
        file_path = self._source_dir / _ROOT_INDEX
        try:
            with file_path.open(encoding="utf-8") as fh:
                raw = fh.read(_MAX_READ_CHARS)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            logger.debug("okf_probe_read_failed path=%s", _ROOT_INDEX, exc_info=True)
            return None
        try:
            metadata = fm.loads(raw).metadata
        except yaml.YAMLError:
            logger.debug(
                "okf_probe_frontmatter_invalid path=%s", _ROOT_INDEX, exc_info=True
            )
            return None
        value = metadata.get("okf_version")
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (int, float)):
            return str(value)
        return None


def derive_trust_tier(metadata: dict[str, Any]) -> str:
    """Derive a note's trust tier from its ``verified`` frontmatter.

    Per the design (§3): ``human-reviewed`` when any well-formed
    ``verified[].by`` carries the ``human:`` prefix; ``machine-confirmed``
    when ``verified`` is non-empty (all verifiers non-human); ``unverified``
    otherwise. Malformed entries are ignored (permissive consumer).

    Args:
        metadata: The note's frontmatter dict.

    Returns:
        One of :data:`TRUST_UNVERIFIED`, :data:`TRUST_MACHINE`,
        :data:`TRUST_HUMAN`.
    """
    verified = metadata.get("verified")
    if not isinstance(verified, list):
        return TRUST_UNVERIFIED
    entries = [entry for entry in verified if isinstance(entry, dict)]
    if any(
        str(entry.get("by", "")).startswith(_HUMAN_ACTOR_PREFIX) for entry in entries
    ):
        return TRUST_HUMAN
    if entries:
        return TRUST_MACHINE
    return TRUST_UNVERIFIED


def derive_stale(metadata: dict[str, Any], *, today: _dt.date) -> bool:
    """Derive staleness from ``stale_after`` (date-only comparison).

    Args:
        metadata: The note's frontmatter dict. ``stale_after`` may be a
            ``date`` (YAML parses bare ISO dates) or a ``YYYY-MM-DD``
            string; anything else is treated as absent.
        today: The server-local date to compare against.

    Returns:
        ``True`` iff ``stale_after`` parses and is strictly before *today*.
    """
    raw = metadata.get("stale_after")
    if isinstance(raw, _dt.datetime):
        raw = raw.date()
    if isinstance(raw, _dt.date):
        return raw < today
    if isinstance(raw, str):
        try:
            return _dt.date.fromisoformat(raw.strip()) < today
        except ValueError:
            logger.debug("okf_stale_after_invalid value=%r", raw)
            return False
    return False


def derive_annotation(
    metadata: dict[str, Any],
    *,
    today: _dt.date | None = None,
    include_sources: bool = False,
) -> dict[str, Any]:
    """Derive the ``okf`` read-annotation payload for one note.

    Args:
        metadata: The note's frontmatter dict (may be empty).
        today: Server-local date for staleness; defaults to today.
        include_sources: When true (``read`` payloads), include the raw
            ``sources`` list; otherwise (search hits) include only
            ``sources_count``, and only when non-zero.

    Returns:
        The annotation dict: ``status`` (defaulted to ``"stable"``),
        ``stale``, and ``trust_tier`` always; ``type`` only when present
        and non-empty; sources per *include_sources*.
    """
    today = today or _dt.date.today()
    annotation: dict[str, Any] = {}
    note_type = metadata.get("type")
    if isinstance(note_type, str) and note_type.strip():
        annotation["type"] = note_type.strip()
    status = metadata.get("status")
    annotation["status"] = (
        status.strip()
        if isinstance(status, str) and status.strip()
        else OKF_STATUS_DEFAULT
    )
    annotation["stale"] = derive_stale(metadata, today=today)
    annotation["trust_tier"] = derive_trust_tier(metadata)
    sources = metadata.get("sources")
    source_list = sources if isinstance(sources, list) else []
    if include_sources:
        if source_list:
            annotation["sources"] = source_list
    elif source_list:
        annotation["sources_count"] = len(source_list)
    return annotation
