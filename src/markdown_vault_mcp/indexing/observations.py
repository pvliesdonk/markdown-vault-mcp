"""Instance-local, versioned state of observed Markdown files."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, get_args

from markdown_vault_mcp.bundle_inventory import Role, _components
from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
from markdown_vault_mcp.hashing import compute_etag

if TYPE_CHECKING:
    from _thread import LockType

    from markdown_vault_mcp.bundle_inventory import BundleInspector


logger = logging.getLogger(__name__)
_LEDGER_LOCKS: dict[Path, LockType] = {}


@dataclass(frozen=True)
class Observation:
    """Latest state observed for one path, including removal evidence."""

    path: str
    role: str
    content_digest: str
    frontmatter_digest: str | None
    first_seen_at: str
    last_change_observed_at: str
    presence: str = "present"
    removed_observed_at: str | None = None
    origin: str = "unknown"
    moved_from: str | None = None
    moved_to: str | None = None


@dataclass(frozen=True)
class _Captured:
    role: str
    digest: str
    header: str | None
    observed_at: str


@dataclass(frozen=True)
class _Removal:
    captured: _Captured | None
    rewritten: str | None = None
    source_present: bool = False


# Move evidence keyed by (source, destination); a deletion has no destination.
_Evidence = dict[tuple[str, str | None], _Removal]


@dataclass(frozen=True, eq=False)
class _WriteHint:
    digest: str


def _present(
    path: str, old: Observation | None, captured: _Captured, origin: str | None = None
) -> Observation:
    if (
        old
        and old.presence == "present"
        and (old.content_digest, old.frontmatter_digest)
        == (captured.digest, captured.header)
    ):
        return replace(
            old, role=captured.role, origin=origin if origin is not None else old.origin
        )
    return Observation(
        path,
        captured.role,
        captured.digest,
        captured.header,
        old.first_seen_at if old else captured.observed_at,
        captured.observed_at,
        origin=origin or "unknown",
    )


def _removed(old: Observation, now: str) -> Observation:
    return replace(
        old,
        presence="removed",
        removed_observed_at=now,
        last_change_observed_at=now,
        origin="unknown",
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_row(key: str, row: Observation) -> bool:
    try:
        if (
            not _components(key)
            or key != row.path
            or any(
                value is not None and not _components(value)
                for value in (row.moved_from, row.moved_to)
            )
        ):
            return False
    except (ValueError, TypeError):
        return False
    if (
        row.role not in get_args(Role)
        or row.origin not in ("unknown", "server_write")
        or row.presence not in ("present", "removed")
    ):
        return False
    if not _valid_digest(row.content_digest) or (
        row.frontmatter_digest is not None and not _valid_digest(row.frontmatter_digest)
    ):
        return False
    if not _valid_timestamp(row.first_seen_at) or not _valid_timestamp(
        row.last_change_observed_at
    ):
        return False
    return (row.presence == "removed") == (row.removed_observed_at is not None) and (
        row.removed_observed_at is None or _valid_timestamp(row.removed_observed_at)
    )


class ObservationLedger:
    """Compare complete inventory sweeps against durable local state.

    Version 3 adds generated navigation history; versions 1 and 2 retain row
    meaning and digest vectors across parser
    upgrades. A changed digest meaning needs a version bump and migration that
    retains trustworthy history and marks unknowns.
    """

    def __init__(self, path: Path | None, inspector: BundleInspector) -> None:
        self._path = path
        self._inspector = inspector
        self._navigation: dict[str, object] = {"generated": {}, "reported": {}}
        self._pending: dict[str, _WriteHint] = {}
        self._removals: _Evidence = {}
        self._pending_lock = threading.Lock()
        self._rows: dict[str, Observation] | None = {} if path is None else None
        self._lock = (
            threading.Lock()
            if path is None
            else _LEDGER_LOCKS.setdefault(path.resolve(), threading.Lock())
        )

    def _load(self) -> tuple[dict[str, Observation], bool]:
        rows, valid, _ = self._load_full()
        return rows, valid

    def _load_full(self) -> tuple[dict[str, Observation], bool, dict[str, object]]:
        empty: dict[str, object] = {"generated": {}, "reported": {}}
        if self._path is None:
            return (self._rows or {}).copy(), True, copy.deepcopy(self._navigation)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            version = data["version"]
            if type(version) is not int:
                raise ValueError("Invalid observation version")
            if version > 3:
                raise StoreUnsupportedSchemaError(
                    f"Unsupported observation schema version: {version}. "
                    "Upgrade the server or move the ledger aside to start new history.",
                    backend="observations",
                    location=str(self._path),
                )
            if version not in (1, 2, 3) or not isinstance(data["rows"], dict):
                raise ValueError("Invalid observation ledger")
            rows = {key: Observation(**row) for key, row in data["rows"].items()}
            if any(not _valid_row(key, row) for key, row in rows.items()):
                raise ValueError("Invalid observation row")
            navigation = self._parse_navigation(data.get("navigation"), version)
            return rows, True, navigation
        except FileNotFoundError:
            return {}, False, empty
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("observation_ledger_reset path=%s", self._path)
            return {}, False, empty

    def _parse_navigation(self, state: object, version: int) -> dict[str, object]:
        empty: dict[str, object] = {"generated": {}, "reported": {}}
        if state is None and version < 3:
            return empty
        try:
            if not isinstance(state, dict):
                raise ValueError("invalid navigation history")
            generated, reported = state["generated"], state["reported"]
            if not isinstance(generated, dict) or not isinstance(reported, dict):
                raise ValueError("invalid navigation history")
            self._validate_navigation(generated, reported)
            return {"generated": generated, "reported": reported}
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("okf_navigation_history_reset path=%s", self._path)
            return empty

    def rows(self) -> dict[str, Observation]:
        """Return a snapshot of locally recorded observations."""
        with self._lock:
            return self._load()[0]

    def expect_write(self, path: str, digest: str) -> None:
        """Record a digest of bytes staged by a completed server write.

        Args:
            path: Vault-relative path.
            digest: SHA-256 of the staged bytes.
        """
        with self._pending_lock:
            self._pending[path] = _WriteHint(digest)

    def capture(self, path: str) -> _Captured | None:
        """Capture the current source incarnation without loading ledger state.

        Args:
            path: Vault-relative source path.

        Returns:
            Evidence from an accessible source, or None when it is unavailable.
        """
        found: list[_Captured] = []

        def visit(_path: str, role: str, digest: str, header: str | None) -> None:
            found.append(_Captured(role, digest, header, datetime.now(UTC).isoformat()))

        # Policy preparation mutates the inspector; isolate it from a concurrent sweep.
        inspector = copy.copy(self._inspector)
        inspector.observe_path(path, visit)
        return found[0] if found else None

    def record_removal(
        self,
        path: str,
        captured: _Captured | None,
        destination: str | None = None,
        rewritten: str | None = None,
    ) -> None:
        """Queue evidence for an already completed deletion or move.

        Args:
            path: Former path.
            captured: Source evidence collected before the mutation.
            destination: Destination when the mutation moved the source.
            rewritten: Digest of a successful rewrite at the destination.
        """
        with self._pending_lock:
            self._removals.pop((path, destination), None)
            self._removals[path, destination] = _Removal(captured, rewritten)
            self._pending.pop(path, None)

    def refresh_many(self, paths: set[str]) -> None:
        """Reconcile a dirty batch and any related move pairs.

        Args:
            paths: Canonical vault-relative paths to inspect.
        """
        with self._lock:
            rows, valid, navigation = self._load_full()
            previous = rows.copy()
            with self._pending_lock:
                removals = self._removals.copy()
                pending = self._pending.copy()
            paths = self._related_paths(paths, removals)
            states: dict[str, str] = {}
            self._seed_capture(rows, removals, paths)
            for path in paths:
                hint = pending.get(path)
                states[path] = self._refresh_locked(
                    rows, path, hint.digest if hint else None
                )
                if states[path] == "blocked":
                    self._restore_blocked(rows, previous, path)
            self._link_moves(rows, previous, removals, states)
            if self._path is None:
                self._rows = rows
            elif rows != previous or not valid:
                self._save(rows, navigation)
            self._consume_evidence(pending, removals, previous, states)

    @staticmethod
    def _restore_blocked(
        rows: dict[str, Observation], previous: dict[str, Observation], path: str
    ) -> None:
        if path in previous:
            rows[path] = previous[path]
        else:
            rows.pop(path, None)

    @staticmethod
    def _related_paths(paths: set[str], removals: _Evidence) -> set[str]:
        result = set(paths)
        while True:
            before = len(result)
            for source, target in removals:
                if target is not None and (source in result or target in result):
                    result.update((source, target))
            if len(result) == before:
                return result

    @staticmethod
    def _link_moves(
        rows: dict[str, Observation],
        previous: dict[str, Observation],
        removals: _Evidence,
        states: dict[str, str],
    ) -> None:
        latest = dict(removals.keys())
        known_sources = {source for (source, _), r in removals.items() if r.captured}
        for (source, target), removal in removals.items():
            captured = removal.captured
            if (
                target is None
                or states.get(source) not in ("present", "missing")
                or states.get(target) not in ("present", "missing")
                or target not in rows
                or (states[target] == "missing" and target not in known_sources)
                or rows[target].content_digest
                not in (captured and captured.digest, removal.rewritten)
            ):
                continue
            rows[target] = replace(rows[target], moved_from=source)
            if latest[source] == target:
                ObservationLedger._mark_moved(rows, previous, source, target, removal)

    @staticmethod
    def _mark_moved(
        rows: dict[str, Observation],
        previous: dict[str, Observation],
        source: str,
        target: str,
        removal: _Removal,
    ) -> None:
        row, old, captured = rows.get(source), previous.get(source), removal.captured
        known = (
            captured or removal.source_present or (old and old.presence == "present")
        )
        # The removed row must still describe the incarnation this move took;
        # a destination already recorded belongs to a later, consumed move.
        if (
            known
            and row is not None
            and row.presence == "removed"
            and row.moved_to is None
            and (captured is None or captured.digest == row.content_digest)
        ):
            rows[source] = replace(row, moved_to=target)

    def _consume_evidence(
        self,
        pending: dict[str, _WriteHint],
        removals: _Evidence,
        previous: dict[str, Observation],
        states: dict[str, str],
    ) -> None:
        with self._pending_lock:
            for path, hint in pending.items():
                if (
                    states.get(path) in ("present", "missing")
                    and self._pending.get(path) is hint
                ):
                    self._pending.pop(path, None)
            held: set[str] = set()  # sources with older evidence still retained
            for key, removal in removals.items():
                path, target = key
                if self._removals.get(key) is not removal or path not in states:
                    continue
                ends = states[path], states.get(target or path, "blocked")
                if path in held or "blocked" in ends:
                    held.add(path)
                    old = previous.get(path)
                    if old and old.presence == "present":
                        self._removals[key] = replace(removal, source_present=True)
                    continue
                self._removals.pop(key, None)

    @staticmethod
    def _seed_capture(
        rows: dict[str, Observation], removals: _Evidence, paths: set[str]
    ) -> None:
        for (path, _), removal in removals.items():
            captured, old = removal.captured, rows.get(path)
            if captured is None or path not in paths:
                continue
            if old is not None and datetime.fromisoformat(
                old.removed_observed_at or old.last_change_observed_at
            ) >= datetime.fromisoformat(captured.observed_at):
                continue
            rows[path] = _present(path, old, captured)

    def _refresh_locked(
        self, rows: dict[str, Observation], path: str, expected: str | None
    ) -> str:
        old = rows.get(path)

        def visit(name: str, role: str, digest: str, header: str | None) -> None:
            origin = (
                ("server_write" if expected == digest else "unknown")
                if expected is not None
                else None
            )
            rows[name] = _present(
                name,
                old,
                _Captured(role, digest, header, datetime.now(UTC).isoformat()),
                origin,
            )

        state = self._inspector.observe_path(path, visit)
        if state == "missing" and old and old.presence == "present":
            rows[path] = _removed(old, datetime.now(UTC).isoformat())
        return state

    def sweep(self) -> None:
        """Reconcile a live inventory sweep; incomplete walks retain absent rows."""
        with self._lock:
            previous, valid, navigation = self._load_full()
            result = previous.copy()
            found: set[str] = set()
            contradicted: set[str] = set()
            with self._pending_lock:
                pending = self._pending.copy()
                removals = self._removals.copy()

            def visit(
                path: str, role: str, digest: str, frontmatter: str | None
            ) -> None:
                found.add(path)
                expected = pending.get(path)
                if expected is not None and expected.digest != digest:
                    contradicted.add(path)
                result[path] = _present(
                    path,
                    result.get(path),
                    _Captured(role, digest, frontmatter, datetime.now(UTC).isoformat()),
                )

            relevant = {p for p, row in previous.items() if row.presence == "present"}
            relevant.update(pending)
            relevant.update(path for pair in removals for path in pair if path)
            self._seed_capture(result, removals, relevant)
            blocked = self._inspector.observe_files(visit)
            states: dict[str, str] = {}
            for path in relevant:
                if path in found:
                    states[path] = "present"
                elif any(
                    not prefix or path == prefix or path.startswith(prefix + "/")
                    for prefix in blocked
                ):
                    states[path] = "blocked"
                else:
                    states[path] = self._refresh_locked(result, path, None)
                    if states[path] == "blocked":
                        self._restore_blocked(result, previous, path)
                    elif states[path] == "missing" or (
                        path in pending
                        and result[path].content_digest != pending[path].digest
                    ):
                        contradicted.add(path)
            self._link_moves(result, previous, removals, states)
            if self._path is None:
                self._rows = result
            elif result != previous or not valid:
                self._save(result, navigation)
            self._consume_evidence(
                {path: hint for path, hint in pending.items() if path in contradicted},
                removals,
                previous,
                states,
            )

    @staticmethod
    def _validate_navigation(
        generated: dict[str, object], reported: dict[str, object]
    ) -> None:
        """Reject malformed local digest and deduplication history."""

        for path, digest in generated.items():
            if (
                not isinstance(path, str)
                or not _components(path)
                or not _valid_digest(digest)
            ):
                raise ValueError("invalid generated digest")
        for path, digests in reported.items():
            if (
                not isinstance(path, str)
                or not _components(path)
                or not isinstance(digests, list)
                or not all(_valid_digest(d) for d in digests)
                or len(digests) != len(set(digests))
            ):
                raise ValueError("invalid divergence history")

    def observe_navigation(self, path: str, old: bytes | None, new: bytes) -> None:
        """Report an unexpected body before replacing a designated index."""
        with self._lock:
            rows, _, state = self._load_full()
            if old is None:
                return
            generated, reported = state["generated"], state["reported"]
            assert isinstance(generated, dict) and isinstance(reported, dict)  # noqa: S101, RUF100 - validated local state
            desired = compute_etag(new)
            observed = compute_etag(old)
            seen = reported.get(path, [])
            if (
                path in generated
                and observed not in (generated[path], desired)
                and observed not in seen
            ):
                reported.setdefault(path, []).append(observed)
                self._save_navigation(rows, state)
                logger.warning("okf_index_divergence path=%s digest=%s", path, observed)

    def record_navigation(self, path: str, body: bytes) -> None:
        """Record a successfully generated index body."""
        with self._lock:
            rows, _, state = self._load_full()
            generated = state["generated"]
            assert isinstance(generated, dict)  # noqa: S101, RUF100 - validated local state
            digest = compute_etag(body)
            if generated.get(path) != digest:
                generated[path] = digest
                self._save_navigation(rows, state)

    def _save_navigation(
        self, rows: dict[str, Observation], state: dict[str, object]
    ) -> None:
        if self._path is None:
            self._navigation = state
        else:
            self._save(rows, state)

    def navigation_stats(self) -> dict[str, object]:
        """Return the cumulative count and bounded examples of local divergences."""
        with self._lock:
            reported = self._load_full()[2]["reported"]
            assert isinstance(reported, dict)  # noqa: S101, RUF100 - validated local state
            count = sum(len(digests) for digests in reported.values())
            examples: list[dict[str, str]] = []
            for path, digests in sorted(reported.items()):
                for digest in digests:
                    if len(examples) == 10:
                        return {"count": count, "examples": examples}
                    examples.append({"path": path, "digest": digest})
            return {"count": count, "examples": examples}

    def _save(
        self, rows: dict[str, Observation], navigation: dict[str, object] | None = None
    ) -> None:
        # Private type invariant for persistent ledgers, not an authorization check.
        assert self._path is not None  # noqa: S101, RUF100
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".observations-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "version": 3,
                            "rows": {k: vars(v) for k, v in rows.items()},
                            "navigation": navigation
                            if navigation is not None
                            else self._load_full()[2],
                        },
                        sort_keys=True,
                    )
                )
            Path(name).replace(self._path)
        finally:
            with suppress(FileNotFoundError):
                Path(name).unlink()
