"""OKF (Open Knowledge Format) support: detection and read-side annotations.

OKF (``open-knowledge-format/open-knowledge-format``, ``SPEC.md``) is a bundle
convention over markdown-with-frontmatter. What the current spec, v0.3, says
is recorded with sources and dates in ``docs/design/reference/okf-v0.3.md``,
together with every place this server departs from it; read it before
changing a rule here. The server follows one OKF version only, so the
deprecated ``okf-v0.2.md`` explains the shipped 0.2-era rules, not a target.
This module implements phase 1 of
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

Write serialization: ``docs/design/reference/python-frontmatter-pyyaml.md``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

# Both halves of the library are used here, deliberately: `fm` serialises the
# write stamps (`fm.dumps` / `fm.Post`), while every *read* goes through
# `parse_frontmatter`, the package's single parse entry point, so a block no
# handler can read raises one error rather than the handler's own (#1408).
import frontmatter as fm
import yaml

from markdown_vault_mcp._frontmatter_splice import REMOVE, splice_keys
from markdown_vault_mcp._yaml12 import YAML12_HANDLER
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block
from markdown_vault_mcp.utils.fs import is_directory
from markdown_vault_mcp.utils.links import build_plain_destination, escape_link_text
from markdown_vault_mcp.utils.text import read_text_utf8

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# --- OKF field-mapping table (data, not code) -------------------------------

#: Declared versions that log no warning: 0.3, the one version every bundle
#: is read as (#2422), plus the earlier versions the 0.3 text names and says
#: how to read (§12.1, §13). A declared value only turns detection on; it
#: selects no rules, so a ``0.2`` bundle is read by 0.3's rules. Anything
#: else logs ``okf_unknown_version`` and is still treated as detected (§12:
#: best-effort, never refused).
KNOWN_OKF_VERSIONS: tuple[str, ...] = ("0.1", "0.2", "0.3")

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


def is_okf_reserved_file(path: str) -> bool:
    """Classify an already scoped vault-relative POSIX path by its basename.

    POSIX basename rules ignore trailing separators and dot components;
    backslashes remain literal. This lexical check grants no detection,
    admission, authorship, or maintenance authority.

    Args:
        path: A vault-relative POSIX path.

    Returns:
        Whether its basename is an exact-case reserved OKF filename.
    """
    return PurePosixPath(path).name in OKF_RESERVED_FILENAMES


#: H1 heading, and title, of a generated ``log.md``. Shared by the two body
#: builders and the reserved-frontmatter policy so the heading a reader sees
#: and the title the index resolves cannot drift apart.
OKF_LOG_TITLE = "Log"

#: H1 heading, and title, of a generated bundle-root ``index.md``. A folder's
#: index uses the folder name instead.
OKF_ROOT_INDEX_TITLE = "Index"

#: Ranking downweight factors (design §5, #965), applied only under detection.
#: Multiplicative on a hit's score and composed so the ordering
#: deprecated < stale < normal holds (a note that is both sinks below either
#: alone). Reserved navigation files (``index.md`` / ``log.md``) are demoted so
#: they don't compete with real notes. Trust-tier *boosting* is deliberately
#: not here — the spec keeps tiers annotation-only.
OKF_DEPRECATED_WEIGHT = 0.5
OKF_STALE_WEIGHT = 0.75
OKF_RESERVED_WEIGHT = 0.5

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
            so an unquoted ``0.3`` still reads as ``"0.3"``), or ``None``
            when the file is missing, unreadable, has no parseable
            frontmatter, or carries no usable ``okf_version``.
        """
        file_path = self._source_dir / _ROOT_INDEX
        try:
            raw = read_text_utf8(file_path, limit=_MAX_READ_CHARS)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            logger.debug("okf_probe_read_failed path=%s", _ROOT_INDEX, exc_info=True)
            return None
        try:
            metadata = parse_frontmatter(raw).metadata
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


def _is_absent(value: Any) -> bool:
    """Whether a value counts as an absent key (OKF 0.3 §11.2 rule 1).

    "A null value, an empty string, and an empty list are the same as an
    absent key." An empty mapping is none of these and stays a value.
    """
    return value is None or (isinstance(value, (str, list)) and not value)


def _has_value(metadata: Mapping[str, Any], key: str) -> bool:
    """Whether *key* carries a value not read as absent (§11.2 rule 1)."""
    return key in metadata and not _is_absent(metadata[key])


def _as_list(value: Any) -> list[Any]:
    """Read a list field's value as a list (OKF 0.3 §11.2 rules 1 and 2).

    An absent value is the empty list, a list is itself, and any other value
    is "one item as a list with one item".
    """
    if _is_absent(value):
        return []
    return value if isinstance(value, list) else [value]


def verified_entries(metadata: dict[str, Any]) -> list[Any]:
    """Return a note's ``verified`` field as the list the spec defines.

    OKF 0.3 §5.2 lets a producer write one verifier as a bare ``{by, at}``
    mapping, and §11.2 rule 2 makes consumers "accept one item as a list
    with one item" for every list field. A null, empty-string or empty-list
    value is the same as an absent key (§11.2 rule 1). Every reader of
    ``verified`` goes through here so these readings hold at each of them.

    Args:
        metadata: The note's frontmatter dict.

    Returns:
        The list as written, any other present value wrapped as one item,
        or an empty list when the field is absent, null, ``""`` or ``[]``.
    """
    return _as_list(metadata.get("verified"))


def source_entries(metadata: dict[str, Any]) -> list[Any]:
    """Return a note's ``sources`` field as a list (OKF 0.3 §11.2).

    Read like :func:`verified_entries`: one mapping (or any other single
    value) is a one-item list, and a null, ``""`` or ``[]`` value is absent.

    Args:
        metadata: The note's frontmatter dict.

    Returns:
        The ``sources`` entries, empty when the field is absent.
    """
    return _as_list(metadata.get("sources"))


def derive_status(metadata: dict[str, Any]) -> str:
    """Return a note's lifecycle ``status`` as OKF 0.3 §5.4 reads it.

    Absent, null, ``""`` and ``[]`` are the same (§11.2 rule 1) and read as
    ``stable``. Any other value is kept as written (§11.2 rule 4), and only
    the exact tokens ``draft``, ``stable`` and ``deprecated`` are the
    vocabulary: ``Stable`` or a quoted ``" stable "`` is outside it, so it
    is not ``stable``. A value that is not a string is shown as its JSON
    text (``3`` → ``"3"``, ``true`` → ``"true"``, a list as a JSON array; a
    date as its ISO form) so the annotation stays a JSON-safe string a
    ``status`` filter can match.

    Args:
        metadata: The note's frontmatter dict.

    Returns:
        The derived status string.
    """
    raw = metadata.get("status")
    if _is_absent(raw):
        return OKF_STATUS_DEFAULT
    if isinstance(raw, str):
        return raw
    if isinstance(raw, _dt.date):
        return raw.isoformat()
    return json.dumps(raw, default=str, ensure_ascii=False)


def derive_trust_tier(metadata: dict[str, Any]) -> str:
    """Derive a note's trust tier from its current ``verified`` events.

    This compatibility label describes recorded claims, not certification.
    Only entries with a recognized actor shape and an OKF §5 timestamp (a
    date-time with an offset, or a date) count, and of those only the events OKF 0.3 §5.3 calls current: an
    ``at`` equal to or later than ``generated.at``, or any event when
    ``generated`` is absent. When ``generated`` is present without a usable
    ``at``, no event is current (the recorded interpretation in
    ``docs/design/okf.md``). Earlier events stay in the file as history and
    do not count. A malformed sibling does not discard a usable recorded
    claim. A bare mapping remains one entry (:func:`verified_entries`).

    Args:
        metadata: The note's frontmatter dict.

    Returns:
        One of :data:`TRUST_UNVERIFIED`, :data:`TRUST_MACHINE`,
        :data:`TRUST_HUMAN`.
    """
    currency = _currency(metadata)
    entries = [
        entry
        for entry in verified_entries(metadata)
        if _review_recorded(entry) and currency.admits(entry)
    ]
    if any(_is_human_actor(entry["by"]) for entry in entries):
        return TRUST_HUMAN
    if entries:
        return TRUST_MACHINE
    return TRUST_UNVERIFIED


_DATE_ONLY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: RFC 3339 §5.6 ``date-time``: ``full-date "T" full-time``, seconds required,
#: a ``.`` fraction, and ``Z`` or a ``+hh:mm`` / ``-hh:mm`` offset. "T" and
#: "Z" may be lower case (§5.6 NOTE). Ranges are checked when the instant is
#: built. OKF 0.3 §5 admits no other form (#2428); the grammar, its open
#: erratum and PyYAML's looser native timestamps are recorded in
#: ``docs/design/reference/rfc3339-timestamps.md``.
_DATE_TIME_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))"
)


def _is_actor(value: Any) -> bool:
    """Whether a ``by`` value is an actor in the OKF convention (§7).

    OKF 0.3 §7 gives two forms, ``<producer>/<version>`` and
    ``<scheme>:<id>`` with an opaque id, and says consumers "MUST accept
    other schemes". It defines no grammar for a scheme, producer or version,
    so any string with a ``:`` or ``/`` is an actor (#2407); only a value
    with neither separator fits no form. An empty string is the same as an
    absent key (§11.2), and it has no separator either.
    """
    return isinstance(value, str) and (":" in value or "/" in value)


def _is_human_actor(value: Any) -> bool:
    """Whether an actor gives the human-reviewed tier (OKF 0.3 §7).

    Only the ``human:`` scheme does, matched exactly: every other scheme,
    ``team:`` included, and the ``<producer>/<version>`` form are non-human.
    """
    return _is_actor(value) and value.startswith(_HUMAN_ACTOR_PREFIX)


def _is_instant(value: Any) -> bool:
    """Whether a value is an OKF §5 timestamp: a date-time with an offset or a date."""
    return _parse_timestamp(value) is not None


#: Currency bases (OKF 0.3 §5.3) reported in ``review.currency``.
_CURRENCY_NO_GENERATION = "no_generation"
_CURRENCY_GENERATED_AT = "generated_at"
_CURRENCY_NO_GENERATION_TIME = "no_generation_time"


@dataclass(frozen=True)
class _Currency:
    """How a note's verification events are judged current (OKF 0.3 §5.3).

    Attributes:
        basis: ``no_generation`` (``generated`` absent, or null or empty
            per §11.2: every event is current), ``generated_at`` (compared with *since*), or
            ``no_generation_time`` (``generated`` present without a usable
            ``at``: §5.3 leaves this unstated, and this server reads it as
            "no event is current").
        since: The ``generated.at`` instant under ``generated_at``.
    """

    basis: str
    since: _dt.datetime | None = None

    def admits(self, entry: Any) -> bool:
        """Whether a recorded event's ``at`` makes it current."""
        if self.basis == _CURRENCY_NO_GENERATION:
            return True
        if self.since is None or not isinstance(entry, Mapping):
            return False
        at = _parse_timestamp(entry.get("at"))
        return at is not None and at >= self.since


def _currency(metadata: dict[str, Any]) -> _Currency:
    """Read the currency threshold a note's ``generated`` sets (§5.3).

    ``generated`` is absent when the key is missing or, by OKF 0.3 §11.2
    rule 1, holds a null, an empty string or an empty list.
    """
    if not _has_value(metadata, "generated"):
        return _Currency(_CURRENCY_NO_GENERATION)
    generated = metadata["generated"]
    since = (
        _parse_timestamp(generated.get("at"))
        if isinstance(generated, Mapping)
        else None
    )
    if since is None:
        return _Currency(_CURRENCY_NO_GENERATION_TIME)
    return _Currency(_CURRENCY_GENERATED_AT, since)


def _review_recorded(entry: Any) -> bool:
    """Whether a ``verified`` entry is a verification event (OKF §5.2).

    The spec gives each event a ``by`` (an actor) and an ``at`` (an OKF §5
    timestamp), so both are required.
    """
    return (
        isinstance(entry, dict)
        and _is_actor(entry.get("by"))
        and _is_instant(entry.get("at"))
    )


def _generation_recorded(value: Any) -> bool:
    """Whether ``generated`` is well formed under OKF §5.2 (#2386).

    The spec requires ``generated.by``, an actor, and describes ``at`` without
    requiring it, so an absent ``at`` is valid, and a null or empty one is
    absent (§11.2 rule 1); a present ``at`` must be an OKF §5 timestamp: a
    date-time with an offset, or a date (#2400).
    """
    if not isinstance(value, dict) or not _is_actor(value.get("by")):
        return False
    return _is_absent(value.get("at")) or _is_instant(value["at"])


def _aware_or_none(value: _dt.datetime, raw: Any) -> _dt.datetime | None:
    """Keep an instant that carries an offset; drop one that does not."""
    if value.utcoffset() is None:
        logger.debug("okf_timestamp_naive value=%r", raw)
        return None
    return value


def _full_date(raw: Any) -> _dt.date | None:
    """Read an RFC 3339 ``full-date`` (a YAML date or a ``YYYY-MM-DD`` string)."""
    if isinstance(raw, _dt.datetime):
        return None
    if isinstance(raw, _dt.date):
        return raw
    if not isinstance(raw, str) or not _DATE_ONLY_RE.fullmatch(raw):
        return None
    try:
        return _dt.date.fromisoformat(raw)
    except ValueError:
        logger.debug("okf_timestamp_invalid value=%r", raw)
        return None


def _parse_timestamp(raw: Any) -> _dt.datetime | None:
    """Read an OKF §5 timestamp value into the instant it names.

    Used for every timestamp key the server reads (``stale_after``,
    ``generated.at``, ``verified[].at``). A date-time with an offset is that
    instant; a date only, quoted or YAML-native, is "The start of that day
    in UTC" (#2404). A string is read by the RFC 3339 grammar alone
    (#2428): seconds required, a ``.`` fraction, a ``T`` separator.
    Anything else is ``None``, including a date-time without an offset,
    which OKF 0.3 says "stays invalid" (§13.2). A YAML-native datetime is a
    value, accepted when it carries an offset (§5 "MUST accept ... a
    YAML-native timestamp").
    """
    day = _full_date(raw)
    if day is not None:
        return _dt.datetime.combine(day, _dt.time.min, tzinfo=_dt.UTC)
    if isinstance(raw, _dt.datetime):
        return _aware_or_none(raw, raw)
    if not isinstance(raw, str):
        return None
    parsed = _rfc3339_date_time(raw)
    if parsed is None:
        logger.debug("okf_timestamp_invalid value=%r", raw)
    return parsed


def _rfc3339_date_time(text: str) -> _dt.datetime | None:
    """Read an RFC 3339 §5.6 ``date-time`` string as an aware instant.

    ``None`` for any other text, a value out of range included. A
    ``time-second`` of 60, which the grammar allows at a leap second, names
    the second after ``:59``; fraction digits past microseconds are dropped.
    """
    match = _DATE_TIME_RE.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    fraction, zulu, sign, off_hour, off_minute = match.groups()[6:]
    if second > 60 or int(off_hour or 0) > 23 or int(off_minute or 0) > 59:
        return None
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    offset = _dt.timedelta(hours=int(off_hour or 0), minutes=int(off_minute or 0))
    zone = _dt.UTC if zulu else _dt.timezone(offset if sign == "+" else -offset)
    try:
        base = _dt.datetime(
            year, month, day, hour, minute, min(second, 59), micro, zone
        )
    except ValueError:
        return None
    return base + _dt.timedelta(seconds=1) if second == 60 else base


def derive_stale(metadata: dict[str, Any], *, now: _dt.datetime) -> bool:
    """Derive staleness from ``stale_after``.

    OKF 0.3 §5.5: a concept is stale when ``now >= stale_after``. A date
    only means the start of that day in UTC (§5), so ``2026-12-31`` is stale
    from ``2026-12-31T00:00:00Z`` on, whatever the server's own zone
    (#2404); a date-time with an offset is compared as that instant. A
    value that does not parse, a date-time without an offset included, is
    stale: OKF 0.3 §5.5 says a consumer "MUST NOT treat a ``stale_after``
    value that it cannot parse as fresh" (#2410). An absent, null or empty
    value, or an empty list, is not stale (§11.2).

    Args:
        metadata: The note's frontmatter dict. ``stale_after`` may be a
            ``date`` or ``datetime`` (YAML parses bare timestamps) or their
            supported extended-calendar spellings (see ``okf.md`` §3);
            any other present value is unparseable and yields true.
        now: The current instant, timezone-aware.

    Returns:
        ``True`` when *now* has reached a parsed ``stale_after``, or when a
        present ``stale_after`` does not parse.
    """
    raw = metadata.get("stale_after")
    boundary = _parse_timestamp(raw)
    if boundary is None:
        return not _is_absent(raw)
    return now >= boundary


def _attribution_evidence(metadata: dict[str, Any]) -> dict[str, Any]:
    """Count usable ``sources`` entries apart from malformed ones.

    OKF §5.1 makes ``resource`` "REQUIRED in an entry", so an entry is
    usable only when it is a mapping whose ``resource`` has a value. §11.2
    rule 3 ignores any other entry; its malformed count is the warning.
    """
    return _entry_evidence(
        source_entries(metadata),
        lambda entry: isinstance(entry, Mapping) and _has_value(entry, "resource"),
    )


def _generation_evidence(metadata: dict[str, Any]) -> dict[str, str]:
    """Judge ``generated`` by OKF §5.2: an actor ``by``, an optional instant ``at``."""
    if not _has_value(metadata, "generated"):
        state = "missing"
    elif _generation_recorded(metadata["generated"]):
        state = "recorded"
    else:
        state = "malformed"
    return {"state": state, "basis": "supplied_metadata_only"}


def _freshness_evidence(metadata: dict[str, Any], now: _dt.datetime) -> dict[str, str]:
    """Describe the supplied expiry without implying knowledge is true."""
    if not _has_value(metadata, "stale_after"):
        return {"state": "missing", "basis": "no_expiry_supplied"}
    raw = metadata["stale_after"]
    boundary = _parse_timestamp(raw)
    if boundary is None:
        return {"state": "malformed", "basis": "unsupported_expiry"}
    return {
        "state": "expired" if now >= boundary else "unexpired",
        "basis": "offset_instant" if _full_date(raw) is None else "utc_day_start",
    }


def _review_evidence(metadata: dict[str, Any], entries: list[Any]) -> dict[str, Any]:
    """Count usable recorded claims separately from malformed input.

    Recorded events are further split by OKF 0.3 §5.3 currency:
    ``current_entries`` feed the trust tier, ``earlier_entries`` have an
    ``at`` before ``generated.at`` and are history, and ``currency`` names
    the basis. Under ``no_generation_time`` neither count holds an event.
    """
    evidence = _entry_evidence(entries, _review_recorded)
    currency = _currency(metadata)
    recorded = [entry for entry in entries if _review_recorded(entry)]
    current = sum(1 for entry in recorded if currency.admits(entry))
    evidence["current_entries"] = current
    evidence["earlier_entries"] = (
        len(recorded) - current if currency.basis == _CURRENCY_GENERATED_AT else 0
    )
    evidence["currency"] = currency.basis
    return evidence


def _entry_evidence(
    entries: list[Any], usable: Callable[[Any], bool]
) -> dict[str, Any]:
    """Report a list-valued field's entries as recorded or malformed.

    *entries* is the field read as a list (:func:`_as_list`), so a field
    with no entries is ``missing`` whether its key is absent, null, ``""``
    or ``[]`` (OKF 0.3 §11.2 rule 1), and a single non-list value is one
    entry (rule 2).
    """
    recorded = sum(1 for entry in entries if usable(entry))
    malformed = len(entries) - recorded
    state = "recorded" if recorded else "malformed"
    if not entries:
        state = "missing"
    return {
        "state": state,
        "recorded_entries": recorded,
        "malformed_entries": malformed,
        "basis": "supplied_metadata_only",
    }


def _review_applicability(entries: list[Any]) -> dict[str, str]:
    """Report whether a supplied review applies to the current content.

    No review is bound to a content snapshot, so a supplied review's
    applicability is always ``unknown``; ``basis`` says why. Only a
    ``verified`` with no entries (absent, null, ``""`` or ``[]``) reports
    ``none``.
    """
    if entries:
        return {"state": "unknown", "basis": "no_snapshot_binding"}
    return {"state": "none", "basis": "no_review_supplied"}


def derive_annotation(
    metadata: dict[str, Any],
    *,
    now: _dt.datetime | None = None,
    include_sources: bool = False,
) -> dict[str, Any]:
    """Derive the ``okf`` read-annotation payload for one note.

    Args:
        metadata: The note's frontmatter dict (may be empty).
        now: The current instant for staleness, timezone-aware; defaults to
            the server's local now.
        include_sources: When true (``read`` payloads), include the raw
            ``sources`` list; otherwise (search hits) include only
            ``sources_count``, and only when non-zero.

    Returns:
        The annotation dict: ``status`` (per :func:`derive_status`:
        ``"stable"`` when absent, otherwise the raw value as a string),
        ``stale``, and ``trust_tier`` always; ``type`` only when present
        and non-empty; sources per *include_sources*. ``freshness`` and
        ``review`` describe the supplied evidence and its parsing basis, not
        truth or applicability to this revision. ``review_applicability``
        reports that separately as ``{state, basis}``: ``unknown`` when
        ``verified`` has entries, ``none`` when it has none. A null, ``""``
        or ``[]`` field reads as absent and a single ``verified`` or
        ``sources`` value as a one-item list (OKF 0.3 §11.2). Full reads additionally carry ``recorded`` with supplied
        expiry/reviews as parsed, including malformed values; omitted keys
        remain absent. ``attribution`` reports ``sources`` in ``review``'s
        shape, counting entries that carry ``resource`` as recorded (OKF
        §5.1); ``generation`` reports ``generated`` as ``{state, basis}``
        under OKF §5.2 (an actor ``by``; an optional ``at`` that, when present,
        is an OKF §5 timestamp). Both
        leave the source projections unchanged.
    """
    now = now or _dt.datetime.now().astimezone()
    annotation: dict[str, Any] = {}
    note_type = metadata.get("type")
    if isinstance(note_type, str) and note_type.strip():
        annotation["type"] = note_type.strip()
    annotation["status"] = derive_status(metadata)
    annotation["stale"] = derive_stale(metadata, now=now)
    annotation["trust_tier"] = derive_trust_tier(metadata)
    annotation["freshness"] = _freshness_evidence(metadata, now)
    entries = verified_entries(metadata)
    annotation["review"] = _review_evidence(metadata, entries)
    annotation["review_applicability"] = _review_applicability(entries)
    annotation["attribution"] = _attribution_evidence(metadata)
    annotation["generation"] = _generation_evidence(metadata)
    source_list = source_entries(metadata)
    if include_sources:
        annotation["recorded"] = {
            key: metadata[key]
            for key in ("stale_after", "verified")
            if _has_value(metadata, key)
        }
        if source_list:
            annotation["sources"] = source_list
    elif source_list:
        annotation["sources_count"] = len(source_list)
    return annotation


def okf_downweight_factor(annotation: dict[str, Any], path: str) -> float:
    """Return the multiplicative ranking factor for one OKF hit (design §5).

    The curated-pipeline downweight for phase 6 (#965). Composes the
    deprecated / stale / reserved-file factors so deprecated < stale < normal,
    and a note that is both deprecated and stale sinks below either alone.
    ``1.0`` means no change.

    Args:
        annotation: The note's :func:`derive_annotation` result (reads
            ``status`` and ``stale``).
        path: The note's vault-relative path (reserved-file check, no I/O).

    Returns:
        A factor in ``(0, 1]``.
    """
    factor = 1.0
    if annotation.get("status") == "deprecated":
        factor *= OKF_DEPRECATED_WEIGHT
    if annotation.get("stale"):
        factor *= OKF_STALE_WEIGHT
    if is_okf_reserved_file(path):
        factor *= OKF_RESERVED_WEIGHT
    return factor


def parse_stale_filter(value: str) -> bool:
    """Parse the ``stale`` filter value into a boolean.

    Args:
        value: The raw filter value (``filters`` is ``dict[str, str]``).

    Returns:
        The requested staleness.

    Raises:
        InvalidRequestError: If *value* is not a recognized true/false spelling.
    """
    normalized = value.strip().lower()
    if normalized in _STALE_TRUE:
        return True
    if normalized in _STALE_FALSE:
        return False
    raise InvalidRequestError(f"stale filter must be true or false, got {value!r}")


def matches_okf_filters(
    metadata: dict[str, Any],
    okf_filters: dict[str, str],
    *,
    now: _dt.datetime | None = None,
) -> bool:
    """Evaluate the OKF filter dimensions against one note's frontmatter.

    Semantics (design §4): ``status`` matches the derived lifecycle value,
    so ``status=stable`` matches notes with *absent* ``status`` (the spec
    default) and never a value outside the vocabulary, which matches its
    raw text instead (:func:`derive_status`); ``stale`` compares the derived staleness; ``trust_tier``
    compares the derived tier. Filters compose with AND.

    Args:
        metadata: The note's frontmatter dict (may be empty).
        okf_filters: The OKF-dimension subset of a ``filters`` dict —
            keys from :data:`OKF_FILTER_KEYS` only.
        now: The current instant for staleness, timezone-aware; defaults to
            the server's local now.

    Returns:
        ``True`` when every given dimension matches.

    Raises:
        InvalidRequestError: If a ``stale`` value is not a true/false spelling.
    """
    annotation = derive_annotation(metadata, now=now)
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
    break OKF 0.3 §11.1: a frontmatter block in any ``.md`` file that does
    not parse as a YAML mapping (rule 1), or a reserved file that disagrees
    with §8 or §9 (rule 2).
    ``advisories`` are tolerated-by-spec deviations worth fixing;
    ``informational`` entries are not deviations at all (wikilinks only
    matter at export; recommended fields are optional). A ``.md`` file with
    no frontmatter or no ``type`` is not a concept (§3.2): it does not
    affect conformance and is reported only as the ``missing_type``
    warning the spec asks validators for.

    Missing freshness is informational; malformed freshness, reviews,
    sources and generated records are format observations and do not change
    ``conformant_notes``. Unavailable
    evidence means no assessment, not absent metadata. These findings cover
    only discovered, non-excluded concepts; they certify neither complete
    traversal nor truth nor review applicability.

    Attributes:
        mode: Configured OKF mode at audit time.
        declared_version: Declared ``okf_version``, or ``None``.
        active: Whether read semantics were active at audit time.
        total_notes: Concepts examined, plus non-reserved files whose
            frontmatter does not parse (they may be concepts). Non-concepts
            and reserved files are not counted.
        conformant_notes: Concepts: non-reserved files whose frontmatter
            parses and has a non-empty ``type``.
        missing_type: Non-reserved files with no frontmatter or no
            non-empty ``type``. Not concepts and not a conformance finding:
            the §3.2 warning (advisory).
        unparseable_frontmatter: Files, reserved ones included, whose
            frontmatter block does not parse as a mapping (§11.1 rule 1).
            An empty or comment-only block reads as no keys and passes.
        unknown_status: Concepts with a ``status`` outside the spec
            vocabulary (tolerated; advisory).
        log_heading_shape: ``log.md`` files with ``##`` headings that are
            not ``YYYY-MM-DD`` dates (conformance: §9, §11.1 rule 2).
        root_index_missing: Whether the bundle-root ``index.md`` is absent
            (advisory; consumers must tolerate it).
        wikilink_files: Concepts containing wikilinks (informational; only
            matters at export).
        missing_recommended: Concepts lacking a recommended ``title`` or
            ``description`` (informational).
        index_frontmatter: Folder ``index.md`` files carrying a frontmatter
            block, which §8 does not allow there (§11.1 rule 2). The
            bundle-root ``index.md`` may carry any keys (§8.1) and is held
            only to rule 1. Server-generated indexes are held to the same
            rule. Presence is read from the block rather than its keys, so
            an empty block counts. ``okf_version`` on a folder index is
            this finding (§8); on a concept it is an extension key (§4.1)
            and no finding (#2427). Defaulted, like the fields after it.
        malformed_sources: Assessed concepts whose ``sources`` has an
            entry without a ``resource``; a single value is a one-item list
            and a null or empty one is absent (the annotation's
            ``attribution`` rule).
        malformed_generated: Assessed concepts whose ``generated`` lacks an
            actor ``by``, or has an ``at`` that is not an ISO 8601 datetime
            with an offset (the annotation's ``generation`` rule, OKF §5.2).
        missing_freshness: Assessed concepts with no expiry supplied.
        malformed_freshness: Assessed concepts with an unsupported expiry.
        malformed_review: Assessed concepts with malformed review input.
        evidence_unavailable: Discovered concepts not assessed for evidence
            because reading/parsing failed or the read cap was exceeded.
        log_frontmatter: ``log.md`` files carrying a frontmatter block,
            parseable or not; §9 says ``log.md`` has none (conformance,
            §11.1 rule 2). Appended and defaulted like the fields above.
    """

    mode: str
    declared_version: str | None
    active: bool
    total_notes: int
    conformant_notes: int
    missing_type: OkfFinding
    unparseable_frontmatter: OkfFinding
    unknown_status: OkfFinding
    log_heading_shape: OkfFinding
    root_index_missing: bool
    wikilink_files: OkfFinding
    missing_recommended: OkfFinding
    index_frontmatter: OkfFinding = OkfFinding(count=0, examples=())
    malformed_sources: OkfFinding = OkfFinding(count=0, examples=())
    malformed_generated: OkfFinding = OkfFinding(count=0, examples=())
    missing_freshness: OkfFinding = OkfFinding(count=0, examples=())
    malformed_freshness: OkfFinding = OkfFinding(count=0, examples=())
    malformed_review: OkfFinding = OkfFinding(count=0, examples=())
    evidence_unavailable: OkfFinding = OkfFinding(count=0, examples=())
    log_frontmatter: OkfFinding = OkfFinding(count=0, examples=())


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
        self.unknown_status = _FindingAccumulator(cap)
        self.log_shape = _FindingAccumulator(cap)
        self.wikilinks = _FindingAccumulator(cap)
        self.missing_recommended = _FindingAccumulator(cap)
        self.index_frontmatter = _FindingAccumulator(cap)
        self.log_frontmatter = _FindingAccumulator(cap)
        self.malformed_sources = _FindingAccumulator(cap)
        self.malformed_generated = _FindingAccumulator(cap)
        self.missing_freshness = _FindingAccumulator(cap)
        self.malformed_freshness = _FindingAccumulator(cap)
        self.malformed_review = _FindingAccumulator(cap)
        self.evidence_unavailable = _FindingAccumulator(cap)
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


def _block_is_mapping(raw: str) -> bool:
    """Whether a leading frontmatter block, if any, loads as a mapping.

    The shared parser reads a block that loads as a sequence or a scalar as
    no keys, which would make it indistinguishable from a file with no
    frontmatter. OKF §11.1 rule 1 asks every block to parse "as a YAML
    mapping"; an empty or comment-only block loads as null, which §11.2
    reads as absent, so it passes. Called only after the parse succeeded,
    so the same handler loads the block again without raising.
    """
    candidate = raw.lstrip()
    handler = fm.detect_format(candidate, fm.handlers)
    loaded = handler.load(handler.split(candidate)[0]) if handler else None
    return loaded is None or isinstance(loaded, dict)


def _is_concept(metadata: dict[str, Any]) -> bool:
    """Whether parsed frontmatter makes a non-reserved file a concept (§3.2)."""
    note_type = metadata.get("type")
    return isinstance(note_type, str) and bool(note_type.strip())


def _audit_reserved(
    rel: str, name: str, body: str, has_block: bool, acc: _AuditCounters
) -> None:
    """Apply §8 and §9 to a reserved file (§11.1 rule 2).

    A folder ``index.md`` and every ``log.md`` have no frontmatter; the
    bundle-root ``index.md`` may carry any keys (§8.1, extension keys
    included), so only rule 1 applies to it.
    """
    if name == "log.md":
        if has_block:
            acc.log_frontmatter.add(rel)
        if not _log_headings_conform(body):
            acc.log_shape.add(rel)
    elif has_block and rel != _ROOT_INDEX:
        acc.index_frontmatter.add(rel)


def _read_capped(file_path: Path, rel: str) -> str | None:
    """Read the audit cap plus one character to detect incomplete evidence."""
    try:
        return read_text_utf8(file_path, limit=_MAX_READ_CHARS + 1)
    except (OSError, UnicodeDecodeError):
        logger.debug("okf_audit_read_failed path=%s", rel, exc_info=True)
        return None


def _evaluate_note(
    rel: str, metadata: dict[str, Any], body: str, acc: _AuditCounters
) -> None:
    """Apply the concept rules to one concept's parsed frontmatter."""
    acc.conformant += 1
    if derive_status(metadata) not in OKF_STATUS_VALUES:
        acc.unknown_status.add(rel)
    if "[[" in body:
        acc.wikilinks.add(rel)
    if not metadata.get("title") or not metadata.get("description"):
        acc.missing_recommended.add(rel)


def _audit_evidence(rel: str, metadata: dict[str, Any], acc: _AuditCounters) -> None:
    """Report supplied-field shape findings, not local editorial policy."""
    if not _has_value(metadata, "stale_after"):
        acc.missing_freshness.add(rel)
    elif _parse_timestamp(metadata["stale_after"]) is None:
        acc.malformed_freshness.add(rel)
    if _review_evidence(metadata, verified_entries(metadata))["malformed_entries"]:
        acc.malformed_review.add(rel)
    if _attribution_evidence(metadata)["malformed_entries"]:
        acc.malformed_sources.add(rel)
    if _generation_evidence(metadata)["state"] == "malformed":
        acc.malformed_generated.add(rel)


def _audit_file(rel: str, raw: str, acc: _AuditCounters) -> None:
    """Classify one markdown file and update the audit counters.

    Rule 1 of OKF §11.1 (the block parses as a mapping) applies to every
    file, reserved ones included. A non-reserved file whose block parses
    but has no non-empty ``type`` is not a concept (§3.2): it is counted
    under ``missing_type`` as a warning and skipped by every concept rule.
    """
    evidence_available = len(raw) <= _MAX_READ_CHARS
    raw = raw[:_MAX_READ_CHARS]
    has_block = strip_frontmatter_block(raw) != raw
    try:
        post = parse_frontmatter(raw)
        metadata: dict[str, Any] = dict(post.metadata)
        body = post.content
        parse_ok = not has_block or _block_is_mapping(raw)
    except yaml.YAMLError:
        metadata, body, parse_ok = {}, raw, False

    name = rel.rsplit("/", 1)[-1]
    if rel == _ROOT_INDEX:
        acc.root_index_seen = True
    if not parse_ok:
        acc.unparseable.add(rel)
    if is_okf_reserved_file(rel):
        _audit_reserved(rel, name, body, has_block, acc)
        return
    if parse_ok and not _is_concept(metadata):
        acc.missing_type.add(rel)
        return

    acc.total += 1
    if parse_ok:
        _evaluate_note(rel, metadata, body, acc)
    if parse_ok and evidence_available:
        _audit_evidence(rel, metadata, acc)
    else:
        acc.evidence_unavailable.add(rel)


def audit_bundle(
    source_dir: Path,
    *,
    exclude_patterns: list[str] | None = None,
    detector: OkfDetector | None = None,
    example_cap: int = 20,
) -> OkfAuditReport:
    """Audit the vault's OKF conformance from disk (design §4; #962).

    Pure disk I/O, index-independent (the detection-probe pattern): each
    file's existing conformance checks use at most :data:`_MAX_READ_CHARS`
    characters; content beyond that cap goes unexamined. One extra character
    detects overflow: evidence findings (freshness, review, sources and
    generated) are withheld for such files, which count as
    ``evidence_unavailable`` instead. Read or parse failures also make their
    evidence assessment unavailable.
    These observations cover discovered, non-excluded concept files, not a
    complete inventory or an editorial-policy assessment. The index's effective exclude patterns double as the audit
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

    Raises:
        OSError: If the vault directory cannot be statted (#1625).
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
        if is_directory(source_dir)
        else []
    )
    for rel in paths:
        if is_path_excluded(rel, excludes):
            continue
        raw = _read_capped(source_dir / rel, rel)
        if raw is not None:
            _audit_file(rel, raw, acc)
        elif rel.rsplit("/", 1)[-1] not in OKF_RESERVED_FILENAMES:
            acc.evidence_unavailable.add(rel)

    return OkfAuditReport(
        mode=state.mode,
        declared_version=state.declared_version,
        active=state.active,
        total_notes=acc.total,
        conformant_notes=acc.conformant,
        missing_type=acc.missing_type.finding(),
        unparseable_frontmatter=acc.unparseable.finding(),
        unknown_status=acc.unknown_status.finding(),
        log_heading_shape=acc.log_shape.finding(),
        root_index_missing=not acc.root_index_seen,
        wikilink_files=acc.wikilinks.finding(),
        missing_recommended=acc.missing_recommended.finding(),
        index_frontmatter=acc.index_frontmatter.finding(),
        malformed_sources=acc.malformed_sources.finding(),
        malformed_generated=acc.malformed_generated.finding(),
        missing_freshness=acc.missing_freshness.finding(),
        malformed_freshness=acc.malformed_freshness.finding(),
        malformed_review=acc.malformed_review.finding(),
        evidence_unavailable=acc.evidence_unavailable.finding(),
        log_frontmatter=acc.log_frontmatter.finding(),
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
            A wikilink naming an attachment is not a link and is not
            counted here (#1333).
        notes_scanned: Notes examined.
    """

    files_changed: int
    links_converted: int
    links_skipped: int
    notes_scanned: int


@dataclass(frozen=True)
class OkfIndexResult:
    """Result of generating a reserved ``index.md`` listing (#963).

    Attributes:
        path: Vault-relative path of the generated listing.
        entries: Notes and sub-folder pointers listed.
        frontmatter_preserved: Whether frontmatter already on the file was
            carried over. Not "the file has frontmatter": a file that had
            none is still written with the vault's required fields seeded
            (#1175), which reports ``False`` here.
        created: Subfolder ``index.md`` files written because the listing
            points at them and they did not exist (#1647), outermost first.
    """

    path: str
    entries: int
    frontmatter_preserved: bool
    created: tuple[str, ...] = ()


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
    Unresolvable wikilinks are left as-is and counted as skipped; a
    wikilink naming an attachment (``![[pic.png]]``) is not a link at all,
    so it is left as-is and not counted (#1333). A resolved path and an
    alias are names, not markdown, and the two families differ on what a
    name may hold: the destination goes through
    :func:`~markdown_vault_mcp.utils.links.build_plain_destination` and the
    display text through
    :func:`~markdown_vault_mcp.utils.links.escape_link_text`, so a space,
    a ``#``, a parenthesis that will not parse, or a bracket survives the
    conversion as part of the name rather than changing what the link means
    (#1494, #1513). Interior whitespace and
    the table-cell ``\\|`` escape are not matched (the same limitation as
    the rename/move link-rewrite engine).

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
        default_display = raw_target
        if fragment and default_display.endswith("#" + fragment):
            default_display = default_display[: -(len(fragment) + 1)]

        def _replace(
            match: re.Match[str],
            *,
            tp: str = target_path,
            fg: str | None = fragment,
            fallback: str = default_display,
        ) -> str:
            alias = match.group(1)
            display = alias.strip() if alias and alias.strip() else fallback
            # A resolved path and an alias are names, not markdown: written
            # verbatim, a space ends the destination (#1494), a ``#`` or an
            # unbalanced paren re-points it, and a bracket in the alias ends
            # the link text (#1513).
            return f"[{escape_link_text(display)}]({build_plain_destination('/' + tp, fg)})"

        pattern = re.compile(r"\[\[" + re.escape(raw_target) + r"(?:\|([^\]]*))?\]\]")
        content, n = pattern.subn(_replace, content)
        converted += n
    return content, converted, skipped


@dataclass(frozen=True)
class ReservedFrontmatterPolicy:
    """Frontmatter the server's own generated reserved files must carry.

    ``index.md`` and ``log.md`` are written by the server, but the index gate
    is configured by the operator: with ``required_frontmatter`` set, a
    generated file carrying no frontmatter is a deterministic
    ``missing_frontmatter`` skip, so the bundle's own navigation and change
    history disappear from ``search`` and ``list_documents`` while staying
    readable from disk (#1174, #1175). The generators consult this policy so
    what they write satisfies the gate the same server enforces.

    The policy is deliberately *conditional*. With no required fields
    configured a reserved file needs no frontmatter to be indexed, and
    :meth:`build` returns ``None`` so the generators keep emitting the
    body-only files every existing vault already has on disk — closing the
    defect costs an unconfigured vault no churn.

    Attributes:
        title_field: The frontmatter key the indexer resolves titles from.
            The one required field given a derived value rather than a
            placeholder, since the server knows the title it just wrote.
        required_fields: The vault's configured ``required_frontmatter``.
    """

    title_field: str = "title"
    required_fields: tuple[str, ...] = ()

    def build(
        self, existing: Mapping[str, Any] | None, *, title: str
    ) -> dict[str, Any] | None:
        """Build the frontmatter for a generated reserved file.

        Existing keys always win, so a hand-authored title and the root
        ``index.md``'s ``okf_version`` declaration survive regeneration
        untouched and only genuinely absent required fields are added. A
        required field other than *title_field* is seeded as ``None``: the
        gate tests presence rather than value
        (:func:`~markdown_vault_mcp.scanner.parse_note_categorized`), and the
        server has nothing truthful to put there.

        ``okf_version`` is never synthesized: only the bundle-root
        ``index.md`` declares the version (OKF 0.3 §8.1), and a folder index
        carries no frontmatter at all (§8).

        Args:
            existing: The file's current frontmatter, or ``None`` when the
                file does not exist yet or carries none.
            title: Title for *title_field*, matching the H1 heading the body
                builders emit for the same file.

        Returns:
            The frontmatter mapping to write, or ``None`` when the file needs
            no frontmatter at all.
        """
        merged: dict[str, Any] = dict(existing or {})
        for field in self.required_fields:
            if field in merged:
                continue
            merged[field] = title if field == self.title_field else None
        return merged or None


def build_index_markdown(
    heading: str, entries: list[tuple[str, str, str | None]]
) -> str:
    """Build an OKF ``index.md`` progressive-disclosure listing.

    Args:
        heading: The H1 heading (bundle or folder name).
        entries: ``(title, root_absolute_path, description)`` per note,
            already ordered. Both the title and the path are *names*, not
            markdown: the path becomes a destination through
            :func:`~markdown_vault_mcp.utils.links.build_plain_destination`
            (#1494, #1513) and the title through
            :func:`~markdown_vault_mcp.utils.links.escape_link_text`, so an
            entry stays a link to its note whatever the note is called.
            ``description`` is emitted as written, being prose after the
            link rather than part of it.

    Returns:
        The markdown body (no frontmatter).
    """
    lines = [f"# {heading}", ""]
    for title, path, description in entries:
        line = f"- [{escape_link_text(title)}]({build_plain_destination(path)})"
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
    header = f"# {OKF_LOG_TITLE}"
    body = f"{header}\n\n" + "\n\n".join(sections) + "\n" if sections else f"{header}\n"
    return body, sum(len(v) for v in by_date.values()), len(order)


def append_okf_log_entry(text: str | None, *, date: str, summary: str) -> str:
    """Append a dated bullet to an OKF ``log.md``, newest date section on top.

    The enforced-write convention-maintenance transform (design §6, phase 5b).
    A ``log.md`` is a newest-first change history of ``## YYYY-MM-DD`` sections;
    this inserts one ``- <summary>`` bullet for *date*:

    - When *text* is empty/``None`` (no ``log.md`` yet), a fresh log with a
      single dated section is returned.
    - When the newest section already carries *date*, the bullet is appended to
      the end of that section (keeping same-day entries together, in order).
    - Otherwise a new ``## <date>`` section is inserted directly after the
      ``# Log`` header so the newest day stays on top.

    Existing content — the header, older sections, and any hand-written prose
    under a date — is preserved verbatim; only the one bullet is inserted.

    Args:
        text: The current ``log.md`` **body**, frontmatter excluded, or
            ``None`` when the log does not exist yet. A caller holding raw
            file text takes the block off first
            (:func:`~markdown_vault_mcp.scanner.strip_frontmatter_block`):
            the result is written back as a body, so a block left in it is
            serialised under a second one (#1391).
        date: The ISO date (``YYYY-MM-DD``) of the entry's section.
        summary: The bullet text (without the leading ``- ``), e.g.
            ``"**Update**: wrote `guides/note.md`"``.

    Returns:
        The updated body (always newline-terminated).
    """
    heading = f"## {date}"
    bullet = f"- {summary}"
    if not text or not text.strip():
        return f"# {OKF_LOG_TITLE}\n\n{heading}\n\n{bullet}\n"
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
    if first is not None and lines[first].strip() == heading:
        # Append to the existing newest-date section, before the next section.
        nxt = next(
            (i for i in range(first + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        insert = nxt
        while insert - 1 > first and not lines[insert - 1].strip():
            insert -= 1
        lines.insert(insert, bullet)
        return "\n".join(lines) + "\n"
    # Insert a new newest-date section after the header (before any sections).
    head = lines[:first] if first is not None else lines
    rest = lines[first:] if first is not None else []
    head = list(head)
    while head and not head[-1].strip():
        head.pop()
    parts = [*head, "", heading, "", bullet]
    if rest:
        parts += ["", *rest]
    return "\n".join(parts) + "\n"


def okf_timestamp(now: _dt.datetime) -> str:
    """Spell an instant the way OKF's timestamp-valued keys require.

    UTC with the ``Z`` designator at seconds precision — the spec's own
    example form (``2026-06-30T14:00:00Z``; ``docs/design/reference/okf-v0.2.md``,
    "Trust"). One spelling for every deployment, and a *string*: a
    ``datetime`` value would be re-dumped by the YAML emitter as
    ``2026-06-30 14:00:00+00:00``, the corruption the spec's amendment names
    (#1372).

    Args:
        now: The instant to spell, timezone-aware.

    Returns:
        The ISO 8601 text.

    Raises:
        ValueError: If *now* carries no offset — it would be read as the
            host's local time and gain an offset the caller never stated.
    """
    if now.tzinfo is None:
        raise ValueError("okf_timestamp needs an aware datetime")
    return now.astimezone(_dt.UTC).replace(microsecond=0).isoformat()[:-6] + "Z"


#: Marks a stored note that carries no readable ``verified`` key, as distinct
#: from one whose stored value is ``None``.
_NO_STORED_VERIFIED = object()


def _stored_verified(stored: str | None) -> object:
    """Return the ``verified`` value of the stored note, as stored.

    Args:
        stored: The note's text on disk before this write, or ``None`` for a
            new note.

    Returns:
        The stored value, kept exactly (malformed values included: they are
        the note's history, not this write's to judge), or
        ``_NO_STORED_VERIFIED`` when there is none to keep: a new note, a
        stored note without the key, or one whose block does not parse.
    """
    if stored is None:
        return _NO_STORED_VERIFIED
    try:
        meta = parse_frontmatter(stored).metadata
    except (yaml.YAMLError, ValueError) as exc:
        # The write itself parsed, so it lands; the history that cannot be
        # read cannot be carried across, and that loss is worth a line.
        logger.warning("okf_write_stored_verified_unreadable error=%s", exc)
        return _NO_STORED_VERIFIED
    return meta.get("verified", _NO_STORED_VERIFIED)


def apply_okf_write_stamp(
    text: str, *, actor: str, now: _dt.datetime, stored: str | None = None
) -> str:
    """Stamp ``generated: {by, at}`` and carry the stored ``verified`` across.

    The enforced-write layer's core transform (design §6). Operates on the final
    file text (frontmatter plus body), so it is uniform across the ``write``
    (frontmatter-param) and ``edit`` (raw-splice) paths. ``generated`` describes
    the current bytes and is overwritten with the instant of this write —
    "the content's last meaningful change" — so it moves on every content
    write. ``verified`` is history: OKF 0.3 §5.3 leaves earlier events in the
    file, and a reader compares each event's ``at`` with ``generated.at`` to
    decide which are current (#2405). It is taken from *stored*, never from
    *text*, so a write neither erases recorded reviews nor adds one of its
    own; ``okf_verify`` is the only path that records a review. ``sources``
    and every other field are untouched.
    Only ``generated`` and ``verified`` are rewritten: every other line of
    the block, comments and quoting included, and the body keep their bytes
    (OKF 0.3 §4, #2419). A new ``generated`` field appends, while replacing
    it retains its slot. A block the splice cannot handle with certainty is
    re-serialized whole, which keeps values and order but not comments.

    Args:
        text: The full note file text (frontmatter and body).
        actor: The provenance actor resolved by the caller — ``human:<subject>``
            for human attribution, else a tool actor such as
            ``markdown-vault-mcp/<version>``.
        now: The instant of the write, timezone-aware (spelled by
            :func:`okf_timestamp`).
        stored: The note's text on disk before this write, read under the
            same write lock; ``None`` for a new note, which leaves the result
            without ``verified``.

    Returns:
        The stamped file text, or *text* unchanged when the resulting frontmatter
        is identical (the same actor and instant, the stored ``verified``) — so
        a repeated stamp keeps the note's exact bytes and YAML formatting.
    """
    post = parse_frontmatter(text)
    meta: dict[str, Any] = dict(post.metadata)
    new_meta: dict[str, Any] = dict(meta)
    updates: dict[str, Any] = {"generated": {"by": actor, "at": okf_timestamp(now)}}
    new_meta["generated"] = updates["generated"]
    kept = _stored_verified(stored)
    if kept is _NO_STORED_VERIFIED:
        if "verified" in meta:
            updates["verified"] = REMOVE
        new_meta.pop("verified", None)
    else:
        if meta.get("verified", _NO_STORED_VERIFIED) != kept:
            updates["verified"] = kept
        new_meta["verified"] = kept
    if new_meta == meta:
        return text
    return _write_keys(text, post, updates, new_meta)


def append_okf_verification(text: str, *, subject: str, now: _dt.datetime) -> str:
    """Append a ``human:<subject>`` entry to a note's ``verified`` list.

    The ``okf_verify`` tool's core transform (design §6): records an attributable
    human attestation, promoting the note's trust tier to ``human-reviewed``
    when the entry is current (OKF 0.3 §5.3; not when ``generated`` is
    present without a usable ``at``, see :func:`derive_trust_tier`).
    Only ``verified`` is rewritten; every other line of the block and the
    body keep their bytes (OKF 0.3 §4, #2419). A new ``verified`` field
    appends; an existing field retains its slot. The entry keeps the
    ``{by, at}`` shape :func:`derive_trust_tier` reads. A block the splice
    cannot handle is re-serialized whole.

    Args:
        text: The full note file text (frontmatter and body).
        subject: The human verifier's subject (without the ``human:`` prefix),
            or ``local`` for a confirmed review without human identity claims.
        now: The instant of the verification, timezone-aware (spelled by
            :func:`okf_timestamp`).

    Returns:
        The note text with the appended verification.
    """
    post = parse_frontmatter(text)
    meta: dict[str, Any] = dict(post.metadata)
    verified = list(verified_entries(meta))
    verified.append({"by": f"{_HUMAN_ACTOR_PREFIX}{subject}", "at": okf_timestamp(now)})
    meta["verified"] = verified
    return _write_keys(text, post, {"verified": verified}, meta)


def _write_keys(
    text: str, post: fm.Post, updates: dict[str, Any], new_meta: dict[str, Any]
) -> str:
    """Write *updates* into *text*, keeping every other byte when possible.

    OKF 0.3 §4: "A producer that modifies an existing file SHOULD NOT
    re-serialize values that it did not change" (#2419). The changed keys
    are spliced into the existing block; a block the splice cannot handle
    with certainty, or a note with no block yet, is written whole.
    """
    spliced = splice_keys(text, updates, expected=new_meta)
    if spliced is not None:
        return spliced
    return fm.dumps(
        fm.Post(post.content, **new_meta), handler=YAML12_HANDLER, sort_keys=False
    )
