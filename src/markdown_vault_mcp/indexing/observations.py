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
    destination: str | None
    source_present: bool = False


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
        if not _components(key) or key != row.path:
            return False
        if any(
            value is not None and not _components(value)
            for value in (row.moved_from, row.moved_to)
        ):
            return False
    except (ValueError, TypeError):
        return False
    if (
        row.role not in get_args(Role)
        or row.origin not in ("unknown", "server_write")
        or row.presence not in ("present", "removed")
        or not _valid_digest(row.content_digest)
        or (
            row.frontmatter_digest is not None
            and not _valid_digest(row.frontmatter_digest)
        )
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

    Version 2 writers preserve row meaning and digest vectors across parser
    upgrades. A changed digest meaning needs a version bump and migration that
    retains trustworthy history and marks unknowns.
    """

    def __init__(self, path: Path | None, inspector: BundleInspector) -> None:
        self._path = path
        self._inspector = inspector
        self._pending: dict[str, _WriteHint] = {}
        self._removals: dict[str, _Removal] = {}
        self._pending_lock = threading.Lock()
        self._rows: dict[str, Observation] | None = {} if path is None else None
        self._cache: tuple[tuple[int, int, int, int], dict[str, Observation]] | None = (
            None
        )
        self._lock = (
            threading.Lock()
            if path is None
            else _LEDGER_LOCKS.setdefault(path.resolve(), threading.Lock())
        )

    def _load(self) -> tuple[dict[str, Observation], bool]:
        if self._path is None:
            return (self._rows or {}).copy(), True
        try:
            key = self._stat_key()
            if self._cache is not None and self._cache[0] == key:
                return self._cache[1].copy(), True
            self._cache = None
            rows = self._decode(self._path.read_text(encoding="utf-8"))
            if self._stat_key() == key:
                self._cache = (key, rows.copy())
            return rows, True
        except FileNotFoundError:
            self._cache = None
            return {}, False
        except (ValueError, TypeError, KeyError, AttributeError):
            self._cache = None
            logger.warning("observation_ledger_reset path=%s", self._path)
            return {}, False
        except OSError:
            self._cache = None
            raise

    def _decode(self, text: str) -> dict[str, Observation]:
        data = json.loads(text)
        version = data["version"]
        if type(version) is not int:
            raise ValueError("Invalid observation version")
        if version > 2:
            raise StoreUnsupportedSchemaError(
                f"Unsupported observation schema version: {version}. "
                "Upgrade the server or move the ledger aside to start new history.",
                backend="observations",
                location=str(self._path),
            )
        if version not in (1, 2) or not isinstance(data["rows"], dict):
            raise ValueError("Invalid observation ledger")
        rows = {key: Observation(**row) for key, row in data["rows"].items()}
        if any(not _valid_row(key, row) for key, row in rows.items()):
            raise ValueError("Invalid observation row")
        return rows

    def _stat_key(self) -> tuple[int, int, int, int]:
        assert self._path is not None  # noqa: S101, RUF100
        stat = self._path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size

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
        self, path: str, captured: _Captured | None, destination: str | None = None
    ) -> None:
        """Queue evidence for an already completed deletion or move.

        Args:
            path: Former path.
            captured: Source evidence collected before the mutation.
            destination: Destination when the mutation moved the source.
        """
        with self._pending_lock:
            self._removals[path] = _Removal(captured, destination)
            self._pending.pop(path, None)

    def refresh_many(self, paths: set[str]) -> None:
        """Reconcile a dirty batch and any related move pairs.

        Args:
            paths: Canonical vault-relative paths to inspect.
        """
        with self._lock:
            rows, valid = self._load()
            previous = rows.copy()
            with self._pending_lock:
                removals = self._removals.copy()
                pending = self._pending.copy()
            paths = self._related_paths(paths, removals)
            states: dict[str, str] = {}
            for path in paths:
                self._seed_capture(rows, path, removals.get(path))
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
                self._save(rows)
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
    def _related_paths(paths: set[str], removals: dict[str, _Removal]) -> set[str]:
        result = set(paths)
        while True:
            before = len(result)
            for source, removal in removals.items():
                target = removal.destination
                if target is not None and (source in result or target in result):
                    result.update((source, target))
            if len(result) == before:
                return result

    @staticmethod
    def _link_moves(
        rows: dict[str, Observation],
        previous: dict[str, Observation],
        removals: dict[str, _Removal],
        states: dict[str, str],
    ) -> None:
        for source, removal in removals.items():
            target = removal.destination
            if (
                target is None
                or states.get(source) != "missing"
                or states.get(target) not in ("present", "missing")
                or target not in rows
            ):
                continue
            target_removal = removals.get(target)
            if states[target] == "missing" and (
                target_removal is None or target_removal.captured is None
            ):
                continue
            if removal.captured is not None and (
                rows[target].content_digest != removal.captured.digest
            ):
                continue
            rows[target] = replace(rows[target], moved_from=source)
            old = previous.get(source)
            known = (
                removal.captured is not None
                or removal.source_present
                or (old is not None and old.presence == "present")
            )
            if known and source in rows and rows[source].presence == "removed":
                rows[source] = replace(rows[source], moved_to=target)

    def _consume_evidence(
        self,
        pending: dict[str, _WriteHint],
        removals: dict[str, _Removal],
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
            for path, removal in removals.items():
                if self._removals.get(path) is not removal or path not in states:
                    continue
                target = removal.destination
                if states[path] == "blocked" or (
                    target is not None and states.get(target, "blocked") == "blocked"
                ):
                    old = previous.get(path)
                    if (
                        old is not None
                        and old.presence == "present"
                        and not removal.source_present
                    ):
                        self._removals[path] = replace(removal, source_present=True)
                    continue
                self._removals.pop(path, None)

    @staticmethod
    def _seed_capture(
        rows: dict[str, Observation], path: str, removal: _Removal | None
    ) -> None:
        if removal is None or removal.captured is None:
            return
        captured = removal.captured
        old = rows.get(path)
        if old is not None:
            last = (
                old.removed_observed_at
                if old.presence == "removed"
                else old.last_change_observed_at
            )
            if last is not None and datetime.fromisoformat(
                last
            ) >= datetime.fromisoformat(captured.observed_at):
                return
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
            previous, valid = self._load()
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
                self._seed_capture(result, path, removals.get(path))
                result[path] = _present(
                    path,
                    result.get(path),
                    _Captured(role, digest, frontmatter, datetime.now(UTC).isoformat()),
                )

            blocked = self._inspector.observe_files(visit)
            relevant = set(previous) | set(pending) | set(removals)
            relevant.update(
                r.destination for r in removals.values() if r.destination is not None
            )
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
                    self._seed_capture(result, path, removals.get(path))
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
                self._save(result)
            self._consume_evidence(
                {path: hint for path, hint in pending.items() if path in contradicted},
                removals,
                previous,
                states,
            )

    def _save(self, rows: dict[str, Observation]) -> None:
        # Private type invariant for persistent ledgers, not an authorization check.
        assert self._path is not None  # noqa: S101, RUF100
        self._cache = None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".observations-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"version": 2, "rows": {k: vars(v) for k, v in rows.items()}},
                        sort_keys=True,
                    )
                )
                staged_inode = os.fstat(stream.fileno()).st_ino
            Path(name).replace(self._path)
            with suppress(OSError):
                key = self._stat_key()
                if key[0] == staged_inode:
                    self._cache = (key, rows.copy())
        finally:
            with suppress(FileNotFoundError):
                Path(name).unlink()
