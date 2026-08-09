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
import re
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

#: ``filters`` keys that carry OKF semantics on an active bundle (phase 2,
#: #961). ``type`` is deliberately absent: it is a plain indexed tag lookup
#: with no special semantics, so the ordinary ``document_tags`` path
#: handles it.
OKF_FILTER_KEYS: tuple[str, ...] = ("status", "stale", "trust_tier")

#: Accepted spellings for the boolean ``stale`` filter value.
_STALE_TRUE = frozenset({"true", "1", "yes"})
_STALE_FALSE = frozenset({"false", "0", "no"})

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


def parse_stale_filter(value: str) -> bool:
    """Parse the ``stale`` filter value into a boolean.

    Args:
        value: The raw filter value (``filters`` is ``dict[str, str]``).

    Returns:
        The requested staleness.

    Raises:
        ValueError: If *value* is not a recognized true/false spelling.
    """
    normalized = value.strip().lower()
    if normalized in _STALE_TRUE:
        return True
    if normalized in _STALE_FALSE:
        return False
    raise ValueError(f"stale filter must be true or false, got {value!r}")


def matches_okf_filters(
    metadata: dict[str, Any],
    okf_filters: dict[str, str],
    *,
    today: _dt.date | None = None,
) -> bool:
    """Evaluate the OKF filter dimensions against one note's frontmatter.

    Semantics (design §4): ``status`` matches the derived lifecycle value,
    so ``status=stable`` matches notes with *absent* ``status`` (the spec
    default); ``stale`` compares the derived staleness; ``trust_tier``
    compares the derived tier. Filters compose with AND.

    Args:
        metadata: The note's frontmatter dict (may be empty).
        okf_filters: The OKF-dimension subset of a ``filters`` dict —
            keys from :data:`OKF_FILTER_KEYS` only.
        today: Server-local date for staleness; defaults to today.

    Returns:
        ``True`` when every given dimension matches.

    Raises:
        ValueError: If a ``stale`` value is not a true/false spelling.
    """
    annotation = derive_annotation(metadata, today=today)
    for key, value in okf_filters.items():
        if key == "stale":
            if annotation["stale"] is not parse_stale_filter(value):
                return False
        elif annotation.get(key) != value:
            return False
    return True


@dataclass(frozen=True)
class OkfFinding:
    """One audit rule's result: a count plus capped example paths."""

    count: int
    examples: tuple[str, ...]


@dataclass(frozen=True)
class OkfAuditReport:
    """Bundle-conformance audit result (design §4; #962).

    Degrees, not verdicts: during a migration the audit is a progress
    meter, so partial conformance is first-class. ``conformance`` findings
    violate the spec's single hard rule (parseable frontmatter with a
    non-empty ``type``) or its placement rule for ``okf_version``;
    ``advisories`` are tolerated-by-spec deviations worth fixing;
    ``informational`` entries are not deviations at all (wikilinks only
    matter at export; recommended fields are optional).

    Attributes:
        mode: Configured OKF mode at audit time.
        declared_version: Declared ``okf_version``, or ``None``.
        active: Whether read semantics were active at audit time.
        total_notes: Non-reserved markdown files examined.
        conformant_notes: Notes with parseable frontmatter and a
            non-empty ``type``.
        missing_type: Notes lacking a non-empty ``type``.
        unparseable_frontmatter: Notes whose frontmatter fails to parse.
        misplaced_okf_version: Notes outside the bundle root carrying
            ``okf_version``.
        unknown_status: Notes with a ``status`` outside the spec
            vocabulary (tolerated; advisory).
        log_heading_shape: ``log.md`` files with ``##`` headings that are
            not ``YYYY-MM-DD`` dates (advisory).
        root_index_missing: Whether the bundle-root ``index.md`` is absent
            (advisory; consumers must tolerate it).
        wikilink_files: Notes containing wikilinks (informational; only
            matters at export).
        missing_recommended: Notes lacking a recommended ``title`` or
            ``description`` (informational).
    """

    mode: str
    declared_version: str | None
    active: bool
    total_notes: int
    conformant_notes: int
    missing_type: OkfFinding
    unparseable_frontmatter: OkfFinding
    misplaced_okf_version: OkfFinding
    unknown_status: OkfFinding
    log_heading_shape: OkfFinding
    root_index_missing: bool
    wikilink_files: OkfFinding
    missing_recommended: OkfFinding


class _FindingAccumulator:
    """Count occurrences and keep the first *cap* example paths."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self.count = 0
        self.examples: list[str] = []

    def add(self, path: str) -> None:
        self.count += 1
        if len(self.examples) < self._cap:
            self.examples.append(path)

    def finding(self) -> OkfFinding:
        return OkfFinding(count=self.count, examples=tuple(self.examples))


class _AuditCounters:
    """Mutable working state for one :func:`audit_bundle` run."""

    def __init__(self, cap: int) -> None:
        self.missing_type = _FindingAccumulator(cap)
        self.unparseable = _FindingAccumulator(cap)
        self.misplaced = _FindingAccumulator(cap)
        self.unknown_status = _FindingAccumulator(cap)
        self.log_shape = _FindingAccumulator(cap)
        self.wikilinks = _FindingAccumulator(cap)
        self.missing_recommended = _FindingAccumulator(cap)
        self.total = 0
        self.conformant = 0
        self.root_index_seen = False


_LOG_HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")


def _log_headings_conform(body: str) -> bool:
    """Whether every ``##`` heading in a ``log.md`` body is a date heading."""
    return all(
        _LOG_HEADING_RE.match(line)
        for line in body.splitlines()
        if line.startswith("## ")
    )


def _read_capped(file_path: Path, rel: str) -> str | None:
    """Read up to :data:`_MAX_READ_CHARS` of *file_path*, ``None`` on error."""
    try:
        with file_path.open(encoding="utf-8") as fh:
            return fh.read(_MAX_READ_CHARS)
    except (OSError, UnicodeDecodeError):
        logger.debug("okf_audit_read_failed path=%s", rel, exc_info=True)
        return None


def _evaluate_note(
    rel: str, metadata: dict[str, Any], body: str, acc: _AuditCounters
) -> None:
    """Apply the non-reserved-note rules to one parsed note."""
    note_type = metadata.get("type")
    if isinstance(note_type, str) and note_type.strip():
        acc.conformant += 1
    else:
        acc.missing_type.add(rel)
    status = metadata.get("status")
    if (
        isinstance(status, str)
        and status.strip()
        and status.strip() not in OKF_STATUS_VALUES
    ):
        acc.unknown_status.add(rel)
    if "[[" in body:
        acc.wikilinks.add(rel)
    if not metadata.get("title") or not metadata.get("description"):
        acc.missing_recommended.add(rel)


def _audit_file(rel: str, raw: str, acc: _AuditCounters) -> None:
    """Classify one markdown file and update the audit counters."""
    try:
        post = fm.loads(raw)
        metadata: dict[str, Any] = dict(post.metadata)
        body = post.content
        parse_ok = True
    except yaml.YAMLError:
        metadata, body, parse_ok = {}, raw, False

    name = rel.rsplit("/", 1)[-1]
    if rel == _ROOT_INDEX:
        acc.root_index_seen = True
    elif "okf_version" in metadata:
        acc.misplaced.add(rel)
    if name in OKF_RESERVED_FILENAMES:
        if name == "log.md" and not _log_headings_conform(body):
            acc.log_shape.add(rel)
        return

    acc.total += 1
    if not parse_ok:
        acc.unparseable.add(rel)
    else:
        _evaluate_note(rel, metadata, body, acc)


def audit_bundle(
    source_dir: Path,
    *,
    exclude_patterns: list[str] | None = None,
    detector: OkfDetector | None = None,
    example_cap: int = 20,
) -> OkfAuditReport:
    """Audit the vault's OKF conformance from disk (design §4; #962).

    Pure disk I/O, index-independent (the detection-probe pattern): each
    file is read up to :data:`_MAX_READ_CHARS` characters — frontmatter
    always fits; a wikilink beyond the cap in a pathological note goes
    uncounted. The index's effective exclude patterns double as the audit
    whitelist, so known-nonconforming zones (convention files, configured
    excludes) are skipped via existing config.

    Args:
        source_dir: Root directory of the markdown vault.
        exclude_patterns: Effective vault exclude patterns (audit
            whitelist).
        detector: Optional detection probe for reporting mode/version
            state; ``None`` reports an inactive, mode-``"off"``-like state.
        example_cap: Maximum example paths kept per finding.

    Returns:
        The audit report. Empty or missing vault directories yield an
        all-zero report rather than an error.
    """
    from markdown_vault_mcp.utils import is_path_excluded
    from markdown_vault_mcp.utils.fs import iter_markdown_files

    state = (
        detector.state()
        if detector is not None
        else OkfState(mode="off", declared_version=None, active=False)
    )
    excludes = list(exclude_patterns or [])
    acc = _AuditCounters(example_cap)
    paths = (
        sorted(
            p.relative_to(source_dir).as_posix()
            for p in iter_markdown_files(source_dir, excludes)
        )
        if source_dir.is_dir()
        else []
    )
    for rel in paths:
        if is_path_excluded(rel, excludes):
            continue
        raw = _read_capped(source_dir / rel, rel)
        if raw is not None:
            _audit_file(rel, raw, acc)

    return OkfAuditReport(
        mode=state.mode,
        declared_version=state.declared_version,
        active=state.active,
        total_notes=acc.total,
        conformant_notes=acc.conformant,
        missing_type=acc.missing_type.finding(),
        unparseable_frontmatter=acc.unparseable.finding(),
        misplaced_okf_version=acc.misplaced.finding(),
        unknown_status=acc.unknown_status.finding(),
        log_heading_shape=acc.log_shape.finding(),
        root_index_missing=not acc.root_index_seen,
        wikilink_files=acc.wikilinks.finding(),
        missing_recommended=acc.missing_recommended.finding(),
    )


# --- Phase 4 migration transforms (#963): pure content builders -------------


@dataclass(frozen=True)
class OkfConvertResult:
    """Result of the wikilink-to-markdown-link conversion (#963).

    Attributes:
        files_changed: Notes whose body was rewritten.
        links_converted: Wikilink occurrences turned into markdown links.
        links_skipped: Unresolvable wikilink occurrences left untouched
            (their target is not indexed, so no root-absolute path exists).
        notes_scanned: Notes examined.
    """

    files_changed: int
    links_converted: int
    links_skipped: int
    notes_scanned: int


@dataclass(frozen=True)
class OkfIndexResult:
    """Result of generating a reserved ``index.md`` listing (#963)."""

    path: str
    entries: int
    frontmatter_preserved: bool


@dataclass(frozen=True)
class OkfLogResult:
    """Result of seeding a reserved ``log.md`` from git history (#963)."""

    path: str
    commits: int
    dates: int


def convert_wikilinks_to_markdown(content: str, outlinks: Any) -> tuple[str, int, int]:
    """Rewrite resolvable wikilinks in *content* as root-absolute md links.

    Only wikilinks whose target is indexed (``exists``) are converted, so
    the link graph is preserved edge-for-edge — a converted link points at
    the same resolved ``target_path`` the wikilink already resolved to.
    Unresolvable wikilinks are left as-is and counted as skipped. Interior
    whitespace and the table-cell ``\\|`` escape are not matched (the same
    limitation as the rename/move link-rewrite engine).

    Args:
        content: Raw note text (frontmatter included).
        outlinks: The note's outlinks (objects with ``link_type``,
            ``raw_target``, ``target_path``, ``link_text``, ``fragment``,
            ``exists``).

    Returns:
        ``(new_content, converted, skipped)`` — occurrence counts, not
        distinct-target counts.
    """
    converted = 0
    skipped = 0
    # Group resolvable wikilinks by raw_target: every occurrence of one
    # raw_target resolves to the same path/fragment, but each occurrence may
    # carry a different alias, so the display text is computed per match.
    by_target: dict[str, tuple[str, str | None]] = {}
    for link in outlinks:
        if link.link_type != "wikilink":
            continue
        if not link.exists:
            skipped += 1
            continue
        by_target.setdefault(link.raw_target, (link.target_path, link.fragment))
    for raw_target, (target_path, fragment) in by_target.items():
        frag_suffix = f"#{fragment}" if fragment else ""
        default_display = raw_target
        if fragment and default_display.endswith("#" + fragment):
            default_display = default_display[: -(len(fragment) + 1)]

        def _replace(
            match: re.Match[str],
            *,
            tp: str = target_path,
            fs: str = frag_suffix,
            fallback: str = default_display,
        ) -> str:
            alias = match.group(1)
            display = alias.strip() if alias and alias.strip() else fallback
            return f"[{display}](/{tp}{fs})"

        pattern = re.compile(r"\[\[" + re.escape(raw_target) + r"(?:\|([^\]]*))?\]\]")
        content, n = pattern.subn(_replace, content)
        converted += n
    return content, converted, skipped


def build_index_markdown(
    heading: str, entries: list[tuple[str, str, str | None]]
) -> str:
    """Build an OKF ``index.md`` progressive-disclosure listing.

    Args:
        heading: The H1 heading (bundle or folder name).
        entries: ``(title, root_absolute_path, description)`` per note,
            already ordered; ``root_absolute_path`` is emitted verbatim in
            the link target.

    Returns:
        The markdown body (no frontmatter).
    """
    lines = [f"# {heading}", ""]
    for title, path, description in entries:
        line = f"- [{title}]({path})"
        if description:
            line += f" - {description}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def build_log_markdown(entries: Any) -> tuple[str, int, int]:
    """Build an OKF ``log.md`` from newest-first history entries.

    Args:
        entries: History entries (objects with ``timestamp`` ISO string,
            ``message``, ``short_sha``), newest first.

    Returns:
        ``(markdown, commit_count, date_count)``.
    """
    by_date: dict[str, list[Any]] = {}
    order: list[str] = []
    for entry in entries:
        date = str(entry.timestamp)[:10]
        if date not in by_date:
            by_date[date] = []
            order.append(date)
        by_date[date].append(entry)
    sections: list[str] = []
    for date in order:
        block = [f"## {date}", ""]
        block.extend(f"- **{c.message}** ({c.short_sha})" for c in by_date[date])
        sections.append("\n".join(block))
    body = "# Log\n\n" + "\n\n".join(sections) + "\n" if sections else "# Log\n"
    return body, sum(len(v) for v in by_date.values()), len(order)


def apply_okf_write_stamp(text: str, *, actor: str, today: _dt.date) -> str:
    """Stamp ``generated: {by, at}`` and clear ``verified`` on a note's frontmatter.

    The enforced-write layer's core transform (design §6). Operates on the final
    file text (frontmatter plus body), so it is uniform across the ``write``
    (frontmatter-param) and ``edit`` (raw-splice) paths. ``generated`` describes
    the current bytes and is overwritten; ``verified`` is dropped because a
    content change invalidates any prior attestation; ``sources`` and every other
    field are untouched.

    Args:
        text: The full note file text (frontmatter and body).
        actor: The provenance actor — ``human:<subject>`` when authenticated,
            else a tool actor such as ``markdown-vault-mcp/<version>``.
        today: The stamp date (server-local; ISO-formatted into ``at``).

    Returns:
        The stamped file text, or *text* unchanged when the resulting frontmatter
        is identical (a same-day, same-actor re-write of a note with no
        ``verified``) — so an unchanged note keeps its exact bytes and YAML
        formatting.
    """
    post = fm.loads(text)
    meta: dict[str, Any] = dict(post.metadata)
    new_meta: dict[str, Any] = dict(meta)
    new_meta["generated"] = {"by": actor, "at": today.isoformat()}
    new_meta.pop("verified", None)
    if new_meta == meta:
        return text
    return fm.dumps(fm.Post(post.content, **new_meta))


def append_okf_verification(text: str, *, subject: str, today: _dt.date) -> str:
    """Append a ``human:<subject>`` entry to a note's ``verified`` list.

    The ``okf_verify`` tool's core transform (design §6): records an attributable
    human attestation, promoting the note's trust tier to ``human-reviewed``.
    Preserves all other frontmatter and the body verbatim; the entry keeps the
    ``{by, at}`` shape :func:`derive_trust_tier` reads.

    Args:
        text: The full note file text (frontmatter and body).
        subject: The authenticated subject (without the ``human:`` prefix).
        today: The verification date (server-local; ISO-formatted into ``at``).

    Returns:
        The note text with the appended verification.
    """
    post = fm.loads(text)
    meta: dict[str, Any] = dict(post.metadata)
    existing = meta.get("verified")
    verified = list(existing) if isinstance(existing, list) else []
    verified.append({"by": f"{_HUMAN_ACTOR_PREFIX}{subject}", "at": today.isoformat()})
    meta["verified"] = verified
    return fm.dumps(fm.Post(post.content, **meta))
