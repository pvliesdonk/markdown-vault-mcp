"""Explicit closed-candidate OKF curation, independent of server model setup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import frontmatter as fm
import yaml
from markdown_it import MarkdownIt
from pydantic_core import to_jsonable_python

from markdown_vault_mcp._okf_decisions import (
    DecisionError,
    DecisionsClient,
    _json_bounds,
    _metric,
)
from markdown_vault_mcp._okf_write import OkfWriteIntent, okf_write_intent
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    EditConflictError,
    MarkdownMCPError,
)
from markdown_vault_mcp.git.submodules import SubmoduleGuard
from markdown_vault_mcp.okf import (
    OKF_RESERVED_FILENAMES,
    apply_okf_write_stamp,
    okf_timestamp,
)
from markdown_vault_mcp.scanner import (
    _RE_INLINE_CODE,
    _blank_reference_definitions,
    _collect_reference_definitions,
    _make_reference_follow,
    _normalise_line_endings,
    _paragraph_regions,
    _strip_fenced_code,
    extract_links,
    parse_frontmatter,
    parse_note,
    strip_frontmatter_block,
)
from markdown_vault_mcp.utils import is_path_excluded, validate_path
from markdown_vault_mcp.utils.fs import iter_markdown_files
from markdown_vault_mcp.utils.links import (
    _decode_escapes_and_entities,
    _follow_inline_destination,
    decode_markdown_destination,
    is_pointy_destination,
    iter_bracket_links,
    split_markdown_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from markdown_vault_mcp.types import LinkInfo
    from markdown_vault_mcp.vault import Vault

_KEYS = frozenset({"type", "status", "description", "sources", "stale_after"})
_MEANINGFUL = "Would filling or correcting the eligible metadata from the supplied candidates be a meaningful change to this page, rather than a cosmetic or redundant update? Evaluate the current metadata and body; existing equal values do not count."


class DecisionInputError(ValueError):
    """An authored local-input remedy safe to display to the operator."""


@dataclass(frozen=True)
class DecisionPolicy:
    """Validated acceptance thresholds and native enforcement ownership."""

    thresholds: dict[str, float]
    replace_threshold: float = 0.95
    meaningful_threshold: float = 0.8
    okf_write: bool = False

    def __post_init__(self) -> None:
        if set(self.thresholds) != _KEYS or not all(
            _metric(v)
            for v in [
                *self.thresholds.values(),
                self.replace_threshold,
                self.meaningful_threshold,
            ]
        ):
            raise DecisionInputError(
                "Decisions thresholds must name all five fields and be finite numbers in [0,1]."
            )
        if self.replace_threshold <= max(
            self.thresholds["type"], self.thresholds["status"]
        ):
            raise DecisionInputError(
                "Replacement threshold must exceed type and status thresholds."
            )


@dataclass(frozen=True)
class Vocabulary:
    """Operator-owned choices plus their contained filesystem origin."""

    path: str
    types: tuple[dict[str, str], ...]
    statuses: tuple[dict[str, str], ...]
    durations: tuple[dict[str, str], ...] = ()
    root: Path | None = field(default=None, repr=False)


def _choices(raw: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list) or not 2 <= len(raw) <= 255:
        raise DecisionInputError("Vocabulary lists require 2..255 described options.")
    result = []
    for item in raw:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(k), str) or not item[k].strip()
            for k in ("value", "description")
        ):
            raise DecisionInputError(
                "Vocabulary options require nonempty value and description strings."
            )
        result.append({"value": item["value"], "description": item["description"]})
    if len({item["value"] for item in result}) != len(result):
        raise DecisionInputError("Vocabulary values must be unique.")
    return tuple(result)


def load_vocabulary(root: Path, path: str) -> Vocabulary:
    """Read a contained ordinary concept's closed lists without network access.

    Args:
        root: Existing local vault root.
        path: Vault-relative markdown vocabulary note.

    Returns:
        Validated operator choices and their filesystem origin.

    Raises:
        ValueError: The path, options, statuses or durations are invalid.
        OSError: The vocabulary cannot be read.
    """
    absolute = validate_path(path, root)
    if Path(path).is_absolute() or {Path(path).name, absolute.name} & {
        *OKF_RESERVED_FILENAMES,
        "SKILL.md",
    }:
        raise DecisionInputError(
            "Vocabulary must name an ordinary vault-relative concept."
        )
    metadata = _raw_metadata(absolute.read_text(encoding="utf-8"))
    types, statuses = (
        _choices(metadata.get("types")),
        _choices(metadata.get("statuses")),
    )
    if {v["value"] for v in statuses} != {"draft", "stable", "deprecated"}:
        raise DecisionInputError(
            "Vocabulary statuses must be exactly draft, stable, deprecated."
        )
    durations = (
        _choices(metadata["stale_after_durations"])
        if "stale_after_durations" in metadata
        else ()
    )
    for option in durations:
        value = option["value"]
        if not re.fullmatch(r"[1-9][0-9]*d", value):
            raise DecisionInputError(
                "Durations require positive integer days, such as 30d."
            )
        try:
            timedelta(days=int(value[:-1]))
        except OverflowError:
            raise DecisionInputError(
                "Duration exceeds supported datetime range."
            ) from None
    return Vocabulary(
        absolute.relative_to(root.resolve()).as_posix(),
        types,
        statuses,
        durations,
        root.resolve(),
    )


def _raw_metadata(text: str) -> dict[str, Any]:
    candidate = text.lstrip()
    handler = fm.detect_format(candidate, fm.handlers)
    if handler is None:
        return {}
    try:
        header, _ = handler.split(candidate)
    except ValueError:
        return {}
    try:
        metadata = handler.load(header) if header.strip() else {}
    except yaml.YAMLError:
        raise DecisionInputError("Use valid YAML frontmatter.") from None
    if not isinstance(metadata, dict) or any(not isinstance(k, str) for k in metadata):
        raise DecisionInputError("Frontmatter requires a mapping with string keys.")
    _json_bounds(metadata)
    return metadata


def _paragraph(body: str) -> str | None:
    lines = _normalise_line_endings(body).split("\n")
    for token in MarkdownIt("commonmark").enable("table").parse("\n".join(lines)):
        if token.level or token.type in {"heading_open", "heading_close"}:
            continue
        if token.type != "paragraph_open" or token.map is None:
            return None
        start, end = token.map
        # Conservatively refuse incomplete pipe rows as domain policy.
        if re.match(r" {0,3}\|", lines[start]):
            return None
        paragraph = "\n".join(lines[start:end]).strip()
        return paragraph if paragraph and len(paragraph) <= 280 else None
    return None


def _source_body(body: str) -> str:
    """Mask excluded spans without joining tokens or creating blank lines."""
    body = _strip_fenced_code(_normalise_line_endings(body))
    body = _RE_INLINE_CODE.sub(lambda match: "\x00" * len(match.group()), body)
    return re.sub(
        r"""(?is:<img\b(?:[^>"']|"[^"]*"|'[^']*')*>)""",
        lambda match: re.sub(r"[^\n]", "\x00", match.group()),
        body,
    )


def _contained_source(
    link: LinkInfo, path: str, root: Path, resolved_path: str | None = None
) -> str | None:
    raw = link.raw_target
    if link.link_type == "wikilink":
        raw = raw.split("#", 1)[0]
    else:
        raw = raw[1:-1] if is_pointy_destination(raw) else raw
        raw = decode_markdown_destination(split_markdown_fragment(raw)[0])
    base = root if raw.startswith("/") else root / Path(path).parent
    if link.link_type == "wikilink" and not raw.startswith(("./", "../", "/")):
        base = root
    (base / raw.removeprefix("/")).resolve().relative_to(root.resolve())
    resolved = validate_path(resolved_path or link.target_path, root)
    return resolved.relative_to(root).as_posix() if resolved.is_file() else None


def _source_tokens(region: str, definitions: dict[str, str]) -> list[str]:
    spans: list[tuple[int, int]] = []
    reference = _make_reference_follow(definitions)

    def follow(text: str, start: int, close: int) -> tuple[str, int] | None:
        inline = _follow_inline_destination(text, start, close)
        matched = (inline[0][0], inline[1]) if inline else reference(text, start, close)
        if matched is not None:
            spans.append((start, matched[1]))
        return matched

    values = [
        _decode_escapes_and_entities(
            target[1:-1] if is_pointy_destination(target) else target
        )
        for _, _, target, _ in iter_bracket_links(region, follow)
    ]
    # Callbacks observe image spans even though the native walker omits images.
    for start, end in spans:
        region = region[:start] + " " * (end - start) + region[end:]
    values.extend(
        _bare_url(
            match.group(), quoted=match.start() > 0 and region[match.start() - 1] == "'"
        )
        for match in re.finditer(r'https?://[^\s<>"\x00-\x20]+', region, re.IGNORECASE)
    )
    return values


def _bare_url(value: str, *, quoted: bool = False) -> str:
    value = value.rstrip(".,;:!?")
    if quoted:
        value = value.removesuffix("'")
    pairs = {")": "(", "]": "[", "}": "{"}
    while value and value[-1] in pairs:
        closer = value[-1]
        if value.count(closer) <= value.count(pairs[closer]):
            break
        value = value[:-1].rstrip(".,;:!?")
    return value


def _sources(
    body: str,
    path: str,
    root: Path | None,
    *,
    resolved_wikilinks: dict[str, str] | None = None,
) -> list[str]:
    candidates = []
    clean = _source_body(body)
    if root is not None:
        for link in extract_links(clean, path):
            try:
                resource = _contained_source(
                    link,
                    path,
                    root,
                    (resolved_wikilinks or {}).get(link.raw_target)
                    if link.link_type == "wikilink"
                    else None,
                )
                if resource:
                    candidates.append(resource)
            except (OSError, ValueError, RuntimeError):
                continue
    definitions = _collect_reference_definitions(clean)
    clean = _blank_reference_definitions(clean)
    for region in _paragraph_regions(clean):
        for value in _source_tokens(region, definitions):
            if "\x00" in value:
                continue
            try:
                parsed = urlsplit(value)
                if parsed.scheme in {"http", "https"} and parsed.hostname:
                    candidates.append(value)
            except (OSError, ValueError, RuntimeError):
                continue
    return list(dict.fromkeys(candidates))


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


@dataclass
class NoteDecision:
    """Raw note snapshot, questions and server-owned candidate bindings."""

    metadata: dict[str, Any]
    questions: list[dict[str, Any]]
    input_parts: list[dict[str, Any]]
    bindings: dict[str, Any]

    def evaluate(
        self, answers: list[dict[str, Any]], policy: DecisionPolicy, *, now: datetime
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Evaluate proposals, retaining policy outcomes before persistence.

        Args:
            answers: Validated ordered answers, including refusals.
            policy: Field, replacement and meaningful thresholds.
            now: Aware write-attempt time for review durations.

        Returns:
            Accepted patch and complete proposed/accepted/persisted field records.
        """
        by_name = {q["name"]: a for q, a in zip(self.questions, answers, strict=True)}
        meaningful = _qualifies(
            by_name.get("meaningful", {}), policy.meaningful_threshold
        )
        fields: dict[str, Any] = {}
        for key in sorted(_KEYS - {"sources"}):
            answer = by_name.get(key, {})
            proposed = (
                answer.get("choice")
                if key in {"type", "status", "stale_after"}
                else self.bindings.get(key)
            )
            disposition = "unavailable"
            if key in by_name:
                disposition = (
                    "refusal" if answer.get("type") == "refusal" else "low-confidence"
                )
                if _qualifies(answer, policy.thresholds[key]):
                    disposition = self._field_disposition(
                        key, proposed, by_name, policy
                    )
            if key == "stale_after" and answer.get("type") == "choice":
                try:
                    proposed = okf_timestamp(
                        now + timedelta(days=int(answer["choice"][:-1]))
                    )
                except (OverflowError, ValueError, KeyError):
                    proposed, disposition = None, "duration-unavailable"
            fields[key] = _field_outcome(proposed, disposition, meaningful)
        sources = (
            list(self.metadata.get("sources") or [])
            if self.bindings["sources_valid"]
            else []
        )
        candidates = self.bindings.get("sources", {})
        outcomes = {}
        for name, resource in candidates.items():
            disposition = (
                "equal" if resource in {s["resource"] for s in sources} else "accepted"
            )
            if not _qualifies(by_name[name], policy.thresholds["sources"]):
                disposition = (
                    "refusal"
                    if by_name[name].get("type") == "refusal"
                    else "low-confidence"
                )
            outcomes[name] = _field_outcome(resource, disposition, meaningful)
            if disposition == "accepted":
                sources.append({"resource": resource})
        disposition = (
            "accepted" if sources != self.metadata.get("sources", []) else "unchanged"
        )
        if not self.bindings["sources_valid"]:
            disposition = "malformed-existing"
        fields["sources"] = {
            **_field_outcome(sources, disposition, meaningful),
            "candidates": candidates,
            "decisions": outcomes,
        }
        return {
            key: value["proposed"] for key, value in fields.items() if value["accepted"]
        }, fields

    def _field_disposition(
        self, key: str, proposed: Any, answers: dict[str, Any], policy: DecisionPolicy
    ) -> str:
        current = self.metadata.get(key)
        if proposed == current or (
            key == "status" and not _present(current) and proposed == "stable"
        ):
            return "equal"
        if (
            key in {"type", "status"}
            and _present(current)
            and not _qualifies(
                answers[key + "_wrong"], policy.replace_threshold, strict=True
            )
        ):
            return "replacement-protected"
        return "accepted"


def _qualifies(
    answer: dict[str, Any], threshold: float, *, strict: bool = False
) -> bool:
    metric = answer.get(
        "confidence" if answer.get("type") == "choice" else "probability", -1
    )
    return (
        answer.get("type") != "refusal"
        and _metric(metric)
        and (metric > threshold if strict else metric >= threshold)
    )


def _field_outcome(proposed: Any, disposition: str, meaningful: bool) -> dict[str, Any]:
    if disposition == "accepted" and not meaningful:
        disposition = "meaningful-suppressed"
    return {
        "proposed": proposed,
        "disposition": disposition,
        "accepted": disposition == "accepted",
        "persisted": False,
    }


def build_questions(
    path: str,
    metadata: dict[str, Any],
    body: str,
    vocabulary: Vocabulary,
    *,
    resolved_wikilinks: dict[str, str] | None = None,
) -> NoteDecision:
    """Build one stable closed request for all eligible fields of one note.

    Args:
        path: Contained vault-relative note path.
        metadata: Full raw frontmatter, including unknown operator fields.
        body: Original body without YAML frontmatter.
        vocabulary: Validated operator options and source root.
        resolved_wikilinks: Native scratch-index destinations keyed by raw wiki target.

    Returns:
        Questions, input and literal candidate bindings for policy evaluation.
    """
    _json_bounds(metadata)
    questions: list[dict[str, Any]] = [
        {"name": "meaningful", "type": "predicate", "instructions": _MEANINGFUL}
    ]
    bindings: dict[str, Any] = {}
    for key, choices in (("type", vocabulary.types), ("status", vocabulary.statuses)):
        questions.append(
            {
                "name": key,
                "type": "choice",
                "instructions": f"Choose the {key} of this page using the supplied descriptions.",
                "choices": list(choices),
            }
        )
        if _present(metadata.get(key)):
            questions.append(
                {
                    "name": key + "_wrong",
                    "type": "predicate",
                    "instructions": f"Is the explicitly stored {key} incorrect for this page?",
                }
            )
    paragraph = _paragraph(body)
    if not _present(metadata.get("description")) and paragraph:
        bindings["description"] = paragraph
        questions.append(
            {
                "name": "description",
                "type": "predicate",
                "instructions": f"Is this exact first paragraph a usable page description?\n{paragraph}",
            }
        )
    sources = metadata.get("sources", [])
    bindings["sources_valid"] = isinstance(sources, list) and all(
        isinstance(s, dict)
        and isinstance(s.get("resource"), str)
        and s["resource"].strip()
        for s in sources
    )
    if bindings["sources_valid"]:
        bindings["sources"] = {
            f"sources:{i}": value
            for i, value in enumerate(
                _sources(
                    body, path, vocabulary.root, resolved_wikilinks=resolved_wikilinks
                )
            )
        }
        for name, resource in bindings["sources"].items():
            questions.append(
                {
                    "name": name,
                    "type": "predicate",
                    "instructions": f"Is this body resource evidence supporting this page?\n{resource}",
                }
            )
    if not _present(metadata.get("stale_after")) and vocabulary.durations:
        questions.append(
            {
                "name": "stale_after",
                "type": "choice",
                "instructions": "Choose a review duration appropriate for this page.",
                "choices": list(vocabulary.durations),
            }
        )
    return NoteDecision(
        metadata,
        questions,
        [
            {"type": "input_text", "text": f"Path: {path}"},
            {"type": "input_json", "data": to_jsonable_python(metadata)},
            {"type": "input_text", "text": body},
        ],
        bindings,
    )


@dataclass(frozen=True)
class DecideSelection:
    """Filesystem mtime and missing-raw-type selectors, combined by union."""

    since: datetime | None = None
    missing_type: bool = False

    def __post_init__(self) -> None:
        if not self.missing_type and self.since is None:
            raise DecisionInputError("Choose --since or --missing-type.")
        if self.since is not None and self.since.utcoffset() is None:
            raise DecisionInputError("--since requires an offset-aware ISO datetime.")


@dataclass
class DecideReport:
    """Per-note results including unmodified actual metrics and safe failures."""

    results: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("written", "unchanged", "skipped", "conflict", "error"), 0
        )
    )
    terminal_error: str | None = None
    disabled: bool = False

    def exit_code(self) -> int:
        """Return one for failed/conflicting notes, zero for successful abstentions."""
        return int(
            bool(self.terminal_error or self.counts["error"] or self.counts["conflict"])
        )

    def add(self, result: dict[str, Any]) -> None:
        """Record one note's disposition and increment its matching total.

        Args:
            result: Path, disposition and optional field answers or error.
        """
        self.results.append(result)
        self.counts[result["disposition"]] += 1


def discover_notes(
    root: Path,
    selection: DecideSelection,
    vocabulary_path: str,
    excludes: Sequence[str] | None = None,
    guard: SubmoduleGuard | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Select safe ordinary notes from disk, without opening indexes or models.

    Args:
        root: Resolved local vault root.
        selection: Union of requested filesystem selectors.
        vocabulary_path: The normalized vocabulary path to omit.
        excludes: Effective native exclusion patterns.
        guard: Optional native submodule boundary.

    Returns:
        Sorted unique paths and observable discovery failures.
    """
    paths, errors = set(), []

    def failed(error: OSError) -> None:
        errors.append(
            {
                "path": str(error.filename or ""),
                "disposition": "error",
                "error": "Cannot enumerate notes.",
            }
        )

    for file in iter_markdown_files(root, excludes, on_error=failed):
        path = file.relative_to(root).as_posix()
        if (
            file.name in {*OKF_RESERVED_FILENAMES, "SKILL.md"}
            or path == vocabulary_path
            or is_path_excluded(path, excludes)
        ):
            continue
        try:
            absolute = validate_path(path, root)
            canonical = absolute.relative_to(root.resolve()).as_posix()
            if (
                absolute.name in {*OKF_RESERVED_FILENAMES, "SKILL.md"}
                or absolute == (root / vocabulary_path).resolve()
                or is_path_excluded(canonical, excludes)
            ):
                continue
            if guard is not None and guard.submodule_of(absolute):
                continue
            recent = (
                selection.since is not None
                and absolute.stat().st_mtime >= selection.since.timestamp()
            )
            missing = selection.missing_type and not _present(
                _raw_metadata(absolute.read_text(encoding="utf-8")).get("type")
            )
            if recent or missing:
                paths.add(path)
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            RuntimeError,
            yaml.YAMLError,
        ):
            errors.append(
                {
                    "path": path,
                    "disposition": "error",
                    "error": "Cannot select note safely.",
                }
            )
    return sorted(paths), errors


def _now() -> datetime:
    return datetime.now(UTC)


class OkfDecisionManager:
    """Apply explicit per-note decisions through the native conditional writer.

    Args:
        vault: Writable local vault with native callbacks and protections.
        client: Injected Decisions transport; the caller owns its lifecycle.
        policy: Validated thresholds and native stamping ownership.
        vocabulary: Validated closed options and contained origin.
        guard: Optional native submodule boundary.
    """

    def __init__(
        self,
        vault: Vault,
        client: DecisionsClient,
        policy: DecisionPolicy,
        vocabulary: Vocabulary,
        guard: SubmoduleGuard | None = None,
    ) -> None:
        self.vault, self.client, self.policy, self.vocabulary = (
            vault,
            client,
            policy,
            vocabulary,
        )
        self.guard = guard or SubmoduleGuard(vault.source_dir)

    def decide(
        self,
        selection: DecideSelection,
        *,
        discovery: tuple[list[str], list[dict[str, Any]]] | None = None,
        report: DecideReport | None = None,
    ) -> DecideReport:
        """Process selected notes once, stopping paid work on terminal failures.

        Args:
            selection: At least one explicit filesystem selector.
            discovery: Optional authoritative preflight membership and errors.
            report: Caller-owned outcomes retained through processing and cleanup.

        Returns:
            Complete prior outcomes, field answers and safe per-note errors.
        """
        report = DecideReport() if report is None else report
        if discovery is None:
            discovery = discover_notes(
                self.vault.source_dir,
                selection,
                self.vocabulary.path,
                self.vault.exclude_patterns,
                self.guard,
            )
        paths, errors = discovery
        for error in errors:
            report.add(error)
        if paths:
            self.vault._index_mgr._required_frontmatter = None
        for path in paths:
            try:
                report.add(self._decide_note(path))
            except DecisionError as error:
                report.add({"path": path, "disposition": "error", "error": str(error)})
                if error.terminal:
                    report.terminal_error = str(error)
                    break
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                RuntimeError,
                yaml.YAMLError,
                MarkdownMCPError,
            ) as error:
                conflict = isinstance(
                    error, (EditConflictError, ConcurrentModificationError)
                )
                report.add(
                    {
                        "path": path,
                        "disposition": "conflict" if conflict else "error",
                        "error": "Note changed during inference."
                        if conflict
                        else "Cannot process note safely.",
                    }
                )
        return report

    def _decide_note(self, path: str) -> dict[str, Any]:
        read = self.vault.reader.read(path)
        if read is None:
            raise DecisionInputError("Note disappeared.")
        body = strip_frontmatter_block(read.content)
        metadata = _raw_metadata(read.content)
        if any(link.link_type == "wikilink" for link in extract_links(body, path)):
            self._prepare_index()
            read = self.vault.reader.read(path)
            if read is None:
                raise DecisionInputError("Note disappeared.")
            body = strip_frontmatter_block(read.content)
            metadata = _raw_metadata(read.content)
        resolved = (
            self._resolved_wikilinks(path)
            if any(link.link_type == "wikilink" for link in extract_links(body, path))
            else None
        )
        decision = build_questions(
            path, metadata, body, self.vocabulary, resolved_wikilinks=resolved
        )
        answers = self.client.decide(decision.input_parts, decision.questions)
        now = _now()
        patch, fields = decision.evaluate(answers, self.policy, now=now)
        result = {
            "path": path,
            "disposition": "written" if patch else "unchanged",
            "fields": fields,
            "answers": [
                {**answer, "name": question["name"]}
                for question, answer in zip(decision.questions, answers, strict=True)
            ],
        }
        if patch:
            try:
                if self.policy.okf_write:
                    self._prepare_index()
                    now = _now()
                    patch, fields = decision.evaluate(answers, self.policy, now=now)
                    result["fields"] = fields
                    if not patch:
                        result["disposition"] = "unchanged"
                        return result
                self._persist(path, body, {**metadata, **patch}, read.etag, now)
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                RuntimeError,
                yaml.YAMLError,
                MarkdownMCPError,
            ) as error:
                conflict = isinstance(
                    error, (EditConflictError, ConcurrentModificationError)
                )
                result.update(
                    disposition="conflict" if conflict else "error",
                    error="Note changed during inference."
                    if conflict
                    else "Cannot persist note safely.",
                )
            else:
                for value in [
                    *fields.values(),
                    *fields["sources"]["decisions"].values(),
                ]:
                    value["persisted"] = value["accepted"]
        return result

    def _prepare_index(self) -> None:
        self.vault._coordinator.prepare_index_read()
        if not self.vault.index.is_queryable():
            self.vault.index.build_index()

    def _resolved_wikilinks(self, path: str) -> dict[str, str]:
        self._prepare_index()
        parsed = parse_note(
            self.vault.source_dir / path,
            self.vault.source_dir,
            self.vault._chunk_strategy,
            title_field=self.vault._title_field,
            attachment_extensions=self.vault._index_mgr._attachment_extensions,
        )
        self.vault._fts.upsert_note(parsed)
        self.vault._fts.resolve_vault_wikilinks()
        return {
            row["raw_target"]: row["target_path"]
            for row in self.vault._fts.get_outlinks(path, limit=None)
            if row["link_type"] == "wikilink"
        }

    def _persist(
        self,
        path: str,
        body: str,
        merged: dict[str, Any],
        etag: str | None,
        now: datetime,
    ) -> None:
        if not self.policy.okf_write:
            stamped = apply_okf_write_stamp(
                fm.dumps(fm.Post(body, **merged)),
                actor="process:okf-decide",
                now=now,
            )
            merged = dict(parse_frontmatter(stamped).metadata)
        with okf_write_intent(OkfWriteIntent(actor="process:okf-decide")):
            self.vault.writer.write(path, body, frontmatter=merged, if_match=etag)
