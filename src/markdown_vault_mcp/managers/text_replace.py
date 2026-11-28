"""One textual replacement across many notes, previewed as a plan (#2031).

:class:`TextReplacer` turns a :class:`ReplaceRequest` (a literal or regular
expression pattern, its replacement, and the notes to cover) into a *plan*:
every note the replacement would change, with its etag and the hash of its
new text. A preview returns the plan's digest with per-note counts and
bounded diffs, and writes nothing. An apply given that digest recomputes the
plan, refuses when the digest differs, and otherwise writes each planned note
through :meth:`~markdown_vault_mcp.managers.document.DocumentManager.rewrite`,
the path ``edit`` takes, reporting each note applied, skipped or refused.

Matching covers a note's body only: the frontmatter block is never searched
or rewritten.
A regular expression runs in an isolated child process with a deadline; the
standard-library behaviour that rests on is recorded in
``docs/design/reference/regex-replacement-worker.md``.

The plan digest, the isolated regex worker and the refuse-rather-than-truncate
bounds come from the contributed implementation in #2187.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import posixpath
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentNotFoundError,
    InvalidRequestError,
    SubmodulePathError,
)
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers._replace_worker import (
    ReplacementLimitError,
    Span,
    literal_spans,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils import reject_nul, resolve_inside, validate_path
from markdown_vault_mcp.utils.fs import (
    could_be_regular_file,
    is_directory,
    is_regular_file,
    iter_markdown_files,
)
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from markdown_vault_mcp.managers.document import DocumentManager

logger = logging.getLogger(__name__)

#: Most notes one request may read, counting notes with no match.
MAX_SCANNED_NOTES = 1000
#: Largest note, in bytes, a request may read.
MAX_NOTE_BYTES = 256 * 1024
#: Most bytes one request may read across all its notes.
MAX_TOTAL_BYTES = 8 * 1024 * 1024
#: Most notes one request may change.
MAX_CHANGED_NOTES = 200
#: Most replacements one request may make across all its notes.
MAX_REPLACEMENTS = 10_000
#: Most characters of replacement text one request may produce.
MAX_OUTPUT_CHARS = 4 * 1024 * 1024
#: Longest pattern, in characters.
MAX_PATTERN_CHARS = 4096
#: Longest replacement, in characters.
MAX_REPLACEMENT_CHARS = 16 * 1024
#: Seconds a regular expression may run across the whole selection.
REGEX_TIMEOUT_SECONDS = 3.0
#: How many changed notes, in path order, a preview shows a diff for.
PREVIEW_DIFF_NOTES = 10
#: Longest diff, in characters, a preview shows for one note.
PREVIEW_DIFF_CHARS = 3000

_PLAN_FORMAT = "markdown-vault-mcp/replace-plan/1"
_WORKER = Path(__file__).with_name("_replace_worker.py")

_REASON_NESTED = "inside a nested repository, which this server does not write"
_REASON_NOT_UTF8 = "not UTF-8 text"
_REASON_CHANGED = "changed since the preview"

NoteStatus = Literal["applied", "skipped", "refused"]


@dataclass(frozen=True)
class ReplaceRequest:
    """One textual replacement and the notes it covers.

    Attributes:
        pattern: The text to find: exact text, or a Python regular
            expression when *regex* is true.
        replacement: The text each match becomes. With *regex*, ``\\1`` and
            ``\\g<name>`` insert capture groups.
        paths: Notes named one by one, vault-relative.
        folders: Folders whose notes are all covered, recursively; ``""``
            is the whole vault.
        regex: Whether *pattern* is a regular expression.
    """

    pattern: str
    replacement: str = ""
    paths: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    regex: bool = False


@dataclass(frozen=True)
class NoteRefusal:
    """A covered note that matches, or cannot be read, and is not to be written.

    Attributes:
        path: Vault-relative note path.
        reason: Why the note is left alone.
    """

    path: str
    reason: str


@dataclass(frozen=True)
class PlannedNote:
    """A note the previewed replacement would change.

    Attributes:
        path: Vault-relative note path.
        replacements: How many matches the replacement changes in it.
        diff: The changed lines as a unified diff without context, or
            ``None`` past the first :data:`PREVIEW_DIFF_NOTES` notes.
        diff_truncated: Whether *diff* was cut at :data:`PREVIEW_DIFF_CHARS`.
    """

    path: str
    replacements: int
    diff: str | None
    diff_truncated: bool = False


@dataclass(frozen=True)
class ReplacePreview:
    """What a replacement would do, computed from the notes as they are.

    Attributes:
        plan: The plan's digest: the SHA-256 of the pattern, replacement and
            mode with each changed note's path, etag and new-text hash. Two
            previews share it exactly when they would write the same text to
            the same notes from the same note versions.
        notes: Every note it would change, in path order.
        refused: Every covered note it matches but would not write, and
            every covered note it cannot read as text, in path order.
        replacements: Total matches across *notes*.
        scanned: How many notes were read.
    """

    plan: str
    notes: list[PlannedNote]
    refused: list[NoteRefusal]
    replacements: int
    scanned: int


@dataclass(frozen=True)
class NoteOutcome:
    """What an apply did with one note.

    Attributes:
        path: Vault-relative note path.
        status: ``applied`` (written), ``skipped`` (it changed after the
            plan was recomputed, so it was not written) or ``refused`` (not
            written: inside a nested repository, or not UTF-8 text).
        reason: Why it was skipped or refused; ``None`` when applied.
    """

    path: str
    status: NoteStatus
    reason: str | None = None


@dataclass(frozen=True)
class ReplaceResult:
    """What an apply did, note by note.

    Attributes:
        plan: The digest the apply was given and matched.
        notes: One outcome per planned or refused note, in path order.
        applied: How many notes were written.
        skipped: How many planned notes changed before their write.
        refused: How many notes were not written by policy.
    """

    plan: str
    notes: list[NoteOutcome]
    applied: int
    skipped: int
    refused: int


class StalePlanError(InvalidRequestError):
    """The notes no longer give the plan an apply was given; nothing was written."""


@dataclass(frozen=True)
class _Snapshot:
    """One read of a note: its etag and its text split at the body."""

    path: str
    etag: str
    text: str
    head: str

    @property
    def body(self) -> str:
        """The text after the frontmatter block."""
        return self.text[len(self.head) :]


@dataclass(frozen=True)
class _Change:
    """One note's planned change, from one read of its bytes."""

    snapshot: _Snapshot
    spans: tuple[Span, ...]

    @property
    def path(self) -> str:
        """The note's vault-relative path."""
        return self.snapshot.path

    @cached_property
    def new_text(self) -> str:
        """The note's text with every span replaced."""
        return self.snapshot.head + _apply_spans(self.snapshot.body, self.spans)

    @cached_property
    def new_hash(self) -> str:
        """The SHA-256 of the new text, as UTF-8."""
        return compute_etag(self.new_text.encode("utf-8"))


@dataclass
class _Proposal:
    """The replacement computed against the notes as they are now."""

    changes: dict[str, _Change] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    scanned: int = 0

    def digest(self, request: ReplaceRequest) -> str:
        """Hash the request's text and mode with each changed note's identity."""
        identity = [
            [path, change.snapshot.etag, change.new_hash]
            for path, change in sorted(self.changes.items())
        ]
        material = [
            _PLAN_FORMAT,
            request.pattern,
            request.replacement,
            request.regex,
            identity,
        ]
        encoded = json.dumps(material, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class TextReplacer:
    """Preview and apply one textual replacement across many notes."""

    def __init__(
        self,
        doc_mgr: DocumentManager,
        *,
        after_write: Callable[[str], None] | None = None,
    ) -> None:
        """Hold the collaborators a replacement reads and writes through.

        Args:
            doc_mgr: Supplies the vault root, the exclude patterns and the
                nested-repository check, and writes each note.
            after_write: Called with each note's path right after its write,
                outside the write lock, for the upkeep ``edit`` runs after
                its write; ``None`` runs none.
        """
        self._doc_mgr = doc_mgr
        self._after_write = after_write

    def preview(self, request: ReplaceRequest) -> ReplacePreview:
        """Compute what *request* would change, writing nothing.

        Args:
            request: The replacement and the notes it covers.

        Returns:
            The plan digest with per-note counts and bounded diffs.

        Raises:
            DocumentNotFoundError: A named note or folder does not exist.
            InvalidRequestError: The request is malformed, names a path that
                is not a note or escapes the vault, or exceeds a bound.
        """
        _validate(request)
        proposal = self._propose(request)
        digest = proposal.digest(request)
        notes = [
            _planned_note(change, with_diff=index < PREVIEW_DIFF_NOTES)
            for index, change in enumerate(proposal.changes.values())
        ]
        refused = [NoteRefusal(p, r) for p, r in proposal.refused.items()]
        logger.debug(
            "text_replace_previewed plan=%s changed=%d refused=%d scanned=%d",
            digest,
            len(notes),
            len(refused),
            proposal.scanned,
        )
        return ReplacePreview(
            plan=digest,
            notes=notes,
            refused=refused,
            replacements=sum(n.replacements for n in notes),
            scanned=proposal.scanned,
        )

    def apply(self, request: ReplaceRequest, plan: str) -> ReplaceResult:
        """Write the planned notes when the notes still give *plan*.

        The plan is recomputed from the notes as they are, with the write
        lock not held. A different digest refuses the whole apply. Otherwise
        each planned note is written as ``edit`` writes it, conditional on
        the etag its plan entry was computed from.

        Args:
            request: The same replacement and notes the preview was given.
            plan: The digest :meth:`preview` returned.

        Returns:
            One outcome per planned or refused note.

        Raises:
            ReadOnlyError: The vault is read-only.
            DocumentNotFoundError: A named note or folder does not exist.
            InvalidRequestError: The request is malformed or exceeds a bound.
            StalePlanError: The notes no longer give *plan*.
        """
        _validate(request)
        self._doc_mgr.ensure_writable()
        proposal = self._propose(request)
        if proposal.digest(request) != plan:
            raise StalePlanError(
                f"plan {plan!r} does not match the notes now: a note changed "
                "since the preview, or the request differs from the previewed "
                "one; nothing was written. Preview the same request again and "
                "apply the new plan."
            )
        outcomes = {
            p: NoteOutcome(p, "refused", r) for p, r in proposal.refused.items()
        }
        for path, change in proposal.changes.items():
            outcomes[path] = self._apply_one(change)
        return _result(plan, outcomes)

    def _apply_one(self, change: _Change) -> NoteOutcome:
        """Write one planned note if it is still at its etag, and say what happened."""
        path = change.path
        try:
            self._doc_mgr.rewrite(path, change.new_text, if_match=change.snapshot.etag)
        except SubmodulePathError:
            return NoteOutcome(path, "refused", _REASON_NESTED)
        except (ConcurrentModificationError, DocumentNotFoundError):
            return NoteOutcome(path, "skipped", _REASON_CHANGED)
        if self._after_write is not None:
            self._after_write(path)
        return NoteOutcome(path, "applied")

    def _propose(self, request: ReplaceRequest) -> _Proposal:
        """Read every covered note once and compute its replacement spans."""
        proposal = _Proposal()
        readable: list[_Snapshot] = []
        budget = _ReadBudget()
        for path, abs_path in self._select(request):
            snapshot = _read_snapshot(path, abs_path, budget)
            if snapshot is None:
                continue
            proposal.scanned += 1
            if isinstance(snapshot, str):
                proposal.refused[path] = snapshot
            else:
                readable.append(snapshot)
        spans = _find_spans(request, [s.body for s in readable])
        for snapshot, note_spans in zip(readable, spans, strict=True):
            if not note_spans:
                continue
            if self._doc_mgr.in_submodule(snapshot.path):
                proposal.refused[snapshot.path] = _REASON_NESTED
                continue
            proposal.changes[snapshot.path] = _Change(snapshot, tuple(note_spans))
        if len(proposal.changes) > MAX_CHANGED_NOTES:
            raise InvalidRequestError(
                f"the replacement changes {len(proposal.changes)} notes, more than "
                f"{MAX_CHANGED_NOTES}; narrow paths and folders and apply in parts."
            )
        proposal.refused = dict(sorted(proposal.refused.items()))
        return proposal

    def _select(self, request: ReplaceRequest) -> list[tuple[str, Path]]:
        """Return the covered notes, each once, in path order.

        A note named in ``paths`` is validated as ``edit`` validates its
        ``path``, so it may be a dot path or an excluded one. A note reached
        by two paths (a symlink, or a folder and an explicit path) is covered
        once, under the first path that reached it.
        """
        root = self._doc_mgr.source_dir
        chosen: dict[Path, str] = {}
        named = ((path, _named_note(path, root)) for path in request.paths)
        walked = (item for folder in request.folders for item in self._walk(folder))
        for found in (named, walked):
            for path, abs_path in found:
                chosen.setdefault(abs_path, path)
                if len(chosen) > MAX_SCANNED_NOTES:
                    raise InvalidRequestError(
                        f"paths and folders cover more than {MAX_SCANNED_NOTES} "
                        "notes; narrow them and preview again."
                    )
        return sorted(((p, a) for a, p in chosen.items()), key=lambda item: item[0])

    def _walk(self, folder: str) -> Iterator[tuple[str, Path]]:
        """Yield the notes under *folder* that a folder selection covers.

        The walk is the indexer's (:func:`iter_markdown_files`). It leaves out
        every path with a component starting with ``.`` and every path an
        exclude pattern matches, which search leaves out too, and every path
        that resolves outside the vault.
        """
        root = self._doc_mgr.source_dir
        for abs_path in iter_markdown_files(_folder_root(folder, root)):
            relative = abs_path.relative_to(root)
            path = relative.as_posix()
            if any(part.startswith(".") for part in relative.parts):
                continue
            if self._doc_mgr.is_excluded(path) or not could_be_regular_file(abs_path):
                continue
            try:
                yield path, resolve_inside(path, root)
            except InvalidRequestError:
                # A symlink out of the vault is not a vault note.
                continue


class _ReadBudget:
    """The bytes one request may still read."""

    def __init__(self) -> None:
        self.remaining = MAX_TOTAL_BYTES

    def spend(self, size: int) -> None:
        """Charge *size* bytes, refusing the request past the total."""
        self.remaining -= size
        if self.remaining < 0:
            raise InvalidRequestError(
                f"paths and folders cover more than {MAX_TOTAL_BYTES} bytes of "
                "notes; narrow them and preview again."
            )


def _validate(request: ReplaceRequest) -> None:
    """Refuse a request no selection could serve."""
    if not request.pattern:
        raise InvalidRequestError("pattern is empty; pass the text to find.")
    if len(request.pattern) > MAX_PATTERN_CHARS:
        raise InvalidRequestError(
            f"pattern is longer than {MAX_PATTERN_CHARS} characters; shorten it."
        )
    if len(request.replacement) > MAX_REPLACEMENT_CHARS:
        raise InvalidRequestError(
            f"replacement is longer than {MAX_REPLACEMENT_CHARS} characters; "
            "shorten it."
        )
    if not request.paths and not request.folders:
        raise InvalidRequestError(
            "no notes selected; pass paths, folders, or folders=[''] for the "
            "whole vault."
        )


def _named_note(path: str, root: Path) -> Path:
    """Resolve a note named in ``paths`` as ``edit`` does; it must exist."""
    abs_path = validate_path(path, root)
    if not is_regular_file(abs_path):
        raise DocumentNotFoundError.note(path)
    return abs_path


def _folder_root(folder: str, root: Path) -> Path:
    """Return the unresolved directory a ``folders`` entry walks from."""
    reject_nul(folder)
    # Normalised, so a "." or ".." component never reads as a dot path below.
    relative = posixpath.normpath(folder.strip("/") or ".")
    if relative == ".":
        return root
    if not is_directory(resolve_inside(relative, root)):
        raise DocumentNotFoundError.folder(folder)
    return root / relative


def _read_snapshot(
    path: str, abs_path: Path, budget: _ReadBudget
) -> _Snapshot | str | None:
    """Read one note once: its snapshot, a refusal reason, or ``None`` if gone.

    The text is decoded as ``edit`` reads a note: UTF-8 with a leading BOM
    dropped and line endings read as ``\\n``.
    """
    try:
        if abs_path.stat().st_size > MAX_NOTE_BYTES:
            raise _too_large(path)
        raw = abs_path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    if len(raw) > MAX_NOTE_BYTES:
        raise _too_large(path)
    budget.spend(len(raw))
    try:
        decoded = decode_utf8(raw)
    except UnicodeDecodeError:
        return _REASON_NOT_UTF8
    text = decoded.replace("\r\n", "\n").replace("\r", "\n")
    body = strip_frontmatter_block(text)
    return _Snapshot(path, compute_etag(raw), text, text[: len(text) - len(body)])


def _too_large(path: str) -> InvalidRequestError:
    return InvalidRequestError(
        f"note {path!r} is larger than {MAX_NOTE_BYTES} bytes; leave it out of "
        "paths and folders, or change it with edit."
    )


def _find_spans(request: ReplaceRequest, bodies: list[str]) -> list[list[Span]]:
    """Return each body's replacement spans, literal in-process, regex isolated."""
    if not request.regex:
        try:
            return literal_spans(
                request.pattern,
                request.replacement,
                bodies,
                max_replacements=MAX_REPLACEMENTS,
                max_output_chars=MAX_OUTPUT_CHARS,
            )
        except ReplacementLimitError as exc:
            raise _limit(str(exc)) from exc
    return _regex_spans(request, bodies)


def _regex_spans(request: ReplaceRequest, bodies: list[str]) -> list[list[Span]]:
    """Run the regex worker in an isolated interpreter killed at the deadline."""
    payload = json.dumps(
        {
            "pattern": request.pattern,
            "replacement": request.replacement,
            "bodies": bodies,
            "max_replacements": MAX_REPLACEMENTS,
            "max_output_chars": MAX_OUTPUT_CHARS,
        },
        ensure_ascii=True,
    )
    try:
        # Fixed interpreter and script; the request travels on stdin only.
        completed = subprocess.run(  # noqa: S603, RUF100
            [sys.executable, "-I", str(_WORKER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=REGEX_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        logger.info(
            "text_replace_regex_timeout seconds=%s notes=%d",
            REGEX_TIMEOUT_SECONDS,
            len(bodies),
        )
        raise InvalidRequestError(
            f"pattern ran longer than {REGEX_TIMEOUT_SECONDS:g} seconds across "
            "the selected notes; simplify it or narrow paths and folders."
        ) from exc
    response = json.loads(completed.stdout)
    if "error" in response:
        raise InvalidRequestError(
            f"pattern or replacement is not a valid regular expression: "
            f"{response['error']}; correct it and preview again."
        )
    if "limit" in response:
        raise _limit(response["limit"])
    return [[(s, e, t) for s, e, t in note] for note in response["spans"]]


def _limit(detail: str) -> InvalidRequestError:
    return InvalidRequestError(
        f"the replacement makes {detail}; narrow paths and folders or the pattern."
    )


def _apply_spans(body: str, spans: Sequence[Span]) -> str:
    """Return *body* with each ``(start, end, text)`` span replaced."""
    parts: list[str] = []
    position = 0
    for start, end, text in spans:
        parts.append(body[position:start])
        parts.append(text)
        position = end
    parts.append(body[position:])
    return "".join(parts)


def _result(plan: str, outcomes: dict[str, NoteOutcome]) -> ReplaceResult:
    notes = [outcomes[p] for p in sorted(outcomes)]
    counts = {
        s: sum(1 for n in notes if n.status == s)
        for s in ("applied", "skipped", "refused")
    }
    logger.info(
        "text_replace_applied plan=%s applied=%d skipped=%d refused=%d",
        plan,
        counts["applied"],
        counts["skipped"],
        counts["refused"],
    )
    return ReplaceResult(
        plan, notes, counts["applied"], counts["skipped"], counts["refused"]
    )


def _planned_note(change: _Change, *, with_diff: bool) -> PlannedNote:
    if not with_diff:
        return PlannedNote(change.path, len(change.spans), None)
    diff, truncated = _render_diff(change)
    return PlannedNote(change.path, len(change.spans), diff, truncated)


def _render_diff(change: _Change) -> tuple[str, bool]:
    """Return the note's changed lines as a context-free unified diff.

    Hunks are built from the match spans, so the work is linear in the note's
    size whatever the spread of its matches. A span ending in a newline also
    takes the following line, which the replacement may join to it.
    """
    body = change.snapshot.body
    offset = change.snapshot.head.count("\n")
    starts = [0, *(i + 1 for i, ch in enumerate(body) if ch == "\n")]
    lines = [f"--- a/{change.path}\n", f"+++ b/{change.path}\n"]
    delta = 0
    for first, last, group in _hunks(body, starts, change.spans):
        begin = starts[first]
        end = starts[last + 1] if last + 1 < len(starts) else len(body)
        old = body[begin:end]
        new = _apply_spans(old, [(s - begin, e - begin, t) for s, e, t in group])
        old_lines, new_lines = _split_lines(old), _split_lines(new)
        old_start = offset + first + 1
        lines.append(
            f"@@ -{_range(old_start, len(old_lines))} "
            f"+{_range(old_start + delta, len(new_lines))} @@\n"
        )
        lines.extend(f"-{line}\n" for line in old_lines)
        lines.extend(f"+{line}\n" for line in new_lines)
        delta += len(new_lines) - len(old_lines)
    return _cut("".join(lines))


def _hunks(
    body: str, starts: list[int], spans: Sequence[Span]
) -> Iterator[tuple[int, int, list[Span]]]:
    """Group spans whose lines touch into ``(first_line, last_line, spans)``."""
    current: tuple[int, int, list[Span]] | None = None
    for span in spans:
        start, end, _ = span
        first = bisect.bisect_right(starts, start) - 1
        last = bisect.bisect_right(starts, max(start, end - 1)) - 1
        if end > start and body[end - 1] == "\n" and end < len(body):
            last += 1
        if current is not None and first <= current[1]:
            current = (current[0], max(current[1], last), [*current[2], span])
            continue
        if current is not None:
            yield current
        current = (first, last, [span])
    if current is not None:
        yield current


def _split_lines(text: str) -> list[str]:
    """Split *text* into lines, a final newline ending the last one."""
    if not text:
        return []
    return text.removesuffix("\n").split("\n")


def _range(start: int, count: int) -> str:
    """Return a unified-diff line range; an empty one names the line before."""
    if count == 0:
        return f"{start - 1},0"
    return str(start) if count == 1 else f"{start},{count}"


def _cut(diff: str) -> tuple[str, bool]:
    """Cut *diff* at the last whole line within :data:`PREVIEW_DIFF_CHARS`."""
    if len(diff) <= PREVIEW_DIFF_CHARS:
        return diff, False
    kept = diff[:PREVIEW_DIFF_CHARS]
    return kept[: kept.rfind("\n") + 1], True
