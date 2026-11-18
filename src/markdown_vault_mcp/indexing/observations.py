"""Instance-local, versioned state of observed Markdown files."""

from __future__ import annotations

import copy
import json
import logging
import os
import posixpath
import re
import tempfile
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
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


def _valid_move_paths(row: Observation) -> bool:
    for related in (row.moved_from, row.moved_to):
        if related is not None:
            try:
                if not _components(related):
                    return False
            except (ValueError, TypeError):
                return False
    return True


def _valid_row(key: str, row: Observation) -> bool:
    try:
        if not _components(key) or key != row.path:
            return False
    except ValueError:
        return False
    if (
        row.role not in get_args(Role)
        or row.origin not in ("unknown", "server_write")
        or row.presence not in ("present", "removed")
        or not _valid_move_paths(row)
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

    Version 2 writers preserve row meaning and digest vectors across parser
    upgrades. A changed digest meaning needs a version bump and migration that
    retains trustworthy history and marks unknowns.
    """

    def __init__(self, path: Path | None, inspector: BundleInspector) -> None:
        self._path = path
        self._inspector = inspector
        self._pending: dict[str, tuple[str]] = {}
        self._removals: dict[str, _Removal] = {}
        self._pending_lock = threading.Lock()
        self._rows: dict[str, Observation] | None = {} if path is None else None
        self._lock = (
            threading.Lock()
            if path is None
            else _LEDGER_LOCKS.setdefault(path.resolve(), threading.Lock())
        )

    def _load(self) -> tuple[dict[str, Observation], bool]:
        if self._path is None:
            return (self._rows or {}).copy(), True
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
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
            return rows, True
        except FileNotFoundError:
            return {}, False
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("observation_ledger_reset path=%s", self._path)
            return {}, False

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
            self._pending[posixpath.normpath(path)] = (digest,)

    def capture(self, path: str) -> _Captured | None:
        """Capture the current source incarnation without loading ledger state.

        Args:
            path: Vault-relative source path.

        Returns:
            Evidence from an accessible source, or None when it is unavailable.
        """
        path = posixpath.normpath(path)
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
        path = posixpath.normpath(path)
        destination = posixpath.normpath(destination) if destination else None
        with self._pending_lock:
            self._removals[path] = _Removal(captured, destination)
            self._pending.pop(path, None)

    def refresh(self, path: str) -> None:
        """Reconcile one dirty path against the inventory scope.

        Args:
            path: Vault-relative path.
        """
        self.refresh_many({path})

    def refresh_many(self, paths: set[str]) -> None:
        """Reconcile a dirty batch with one ledger load and save.

        Args:
            paths: Vault-relative paths to inspect.
        """
        paths = {posixpath.normpath(path) for path in paths}
        with self._lock:
            rows, valid = self._load()
            previous = rows.copy()
            with self._pending_lock:
                pending = {path: self._pending.get(path) for path in paths}
                removals = {path: self._removals.get(path) for path in paths}
            states: dict[str, str] = {}
            for path in paths:
                self._seed_capture(rows, path, removals[path])
                hint = pending[path]
                states[path] = self._refresh_locked(
                    rows, path, hint[0] if hint else None
                )
            self._link_moves(rows, removals, states)
            if self._path is None:
                self._rows = rows
            elif rows != previous or not valid:
                self._save(rows)
            with self._pending_lock:
                for path in paths:
                    if states[path] != "blocked":
                        if self._pending.get(path) is pending[path]:
                            self._pending.pop(path, None)
                        if self._removals.get(path) is removals[path]:
                            self._removals.pop(path, None)

    @staticmethod
    def _link_moves(
        rows: dict[str, Observation],
        removals: dict[str, _Removal | None],
        states: dict[str, str],
    ) -> None:
        for path, removal in removals.items():
            if (
                removal is None
                or removal.captured is None
                or removal.destination is None
            ):
                continue
            target = removal.destination
            if (
                states.get(path) == "missing"
                and states.get(target) == "present"
                and path in rows
                and target in rows
            ):
                rows[path] = replace(rows[path], moved_to=target)
                rows[target] = replace(rows[target], moved_from=path)

    @staticmethod
    def _seed_capture(
        rows: dict[str, Observation], path: str, removal: _Removal | None
    ) -> None:
        if removal is None or removal.captured is None:
            return
        captured = removal.captured
        old = rows.get(path)
        if old and old.presence == "present" and old.content_digest == captured.digest:
            return
        rows[path] = Observation(
            path,
            captured.role,
            captured.digest,
            captured.header,
            captured.observed_at,
            captured.observed_at,
        )

    def _refresh_locked(
        self, rows: dict[str, Observation], path: str, expected: str | None
    ) -> str:
        now = datetime.now(UTC).isoformat()
        old = rows.get(path)

        def visit(name: str, role: str, digest: str, header: str | None) -> None:
            origin = (
                ("server_write" if expected == digest else "unknown")
                if expected is not None
                else None
            )
            rows[name] = _present(
                name, old, _Captured(role, digest, header, now), origin
            )

        state = self._inspector.observe_path(path, visit)
        if state == "missing" and old and old.presence == "present":
            rows[path] = _removed(old, now)
        return state

    def sweep(self) -> None:
        """Reconcile a live inventory sweep; incomplete walks retain absent rows."""
        with self._lock:
            previous, valid = self._load()
            now = datetime.now(UTC).isoformat()
            result = previous.copy()
            found: set[str] = set()
            with self._pending_lock:
                pending = self._pending.copy()

            def visit(
                path: str, role: str, digest: str, frontmatter: str | None
            ) -> None:
                found.add(path)
                expected = pending.get(path)
                if expected is not None and expected[0] != digest:
                    with self._pending_lock:
                        if self._pending.get(path) is expected:
                            self._pending.pop(path, None)
                result[path] = _present(
                    path, previous.get(path), _Captured(role, digest, frontmatter, now)
                )

            blocked = self._inspector.observe_files(visit)
            for path, expected in pending.items():
                visible = not any(
                    not prefix or path == prefix or path.startswith(prefix + "/")
                    for prefix in blocked
                )
                if path not in found and visible:
                    with self._pending_lock:
                        if self._pending.get(path) is expected:
                            self._pending.pop(path, None)
            for path in previous.keys() - found:
                old = previous[path]
                visible = not any(
                    not prefix or path == prefix or path.startswith(prefix + "/")
                    for prefix in blocked
                )
                if old.presence == "present" and visible:
                    result[path] = _removed(old, now)
            if self._path is None:
                self._rows = result
            elif result != previous or not valid:
                self._save(result)

    def _save(self, rows: dict[str, Observation]) -> None:
        # Private type invariant for persistent ledgers, not an authorization check.
        assert self._path is not None  # noqa: S101, RUF100
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".observations-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"version": 2, "rows": {k: asdict(v) for k, v in rows.items()}},
                    stream,
                    sort_keys=True,
                )
            Path(name).replace(self._path)
        finally:
            with suppress(FileNotFoundError):
                Path(name).unlink()
