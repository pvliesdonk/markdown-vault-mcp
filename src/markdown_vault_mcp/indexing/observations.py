"""Instance-local, versioned state of observed Markdown files."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from markdown_vault_mcp.bundle_inventory import BundleInspector


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


def _frontmatter_digest(raw: bytes) -> str | None:
    try:
        text = decode_utf8(raw)
    except UnicodeDecodeError:
        return None
    body = strip_frontmatter_block(text)
    if body == text or not text.endswith(body):
        return None
    return compute_etag(text[: len(text) - len(body)].encode("utf-8"))


def _valid_row(key: str, row: Observation) -> bool:
    if not all(
        isinstance(value, str) and value
        for value in (
            key,
            row.path,
            row.role,
            row.content_digest,
            row.first_seen_at,
            row.last_change_observed_at,
            row.origin,
        )
    ):
        return False
    if key != row.path or row.presence not in {"present", "removed"}:
        return False
    if row.frontmatter_digest is not None and not isinstance(
        row.frontmatter_digest, str
    ):
        return False
    if row.removed_observed_at is not None and not isinstance(
        row.removed_observed_at, str
    ):
        return False
    return (row.presence == "removed") == (row.removed_observed_at is not None)


class ObservationLedger:
    """Compare complete inventory sweeps against durable local state."""

    def __init__(self, path: Path | None, inspector: BundleInspector) -> None:
        self._path = path
        self._inspector = inspector
        self._rows: dict[str, Observation] = {}
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Observation]:
        if self._path is None:
            return self._rows.copy()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            version = data["version"]
            if type(version) is not int:
                raise ValueError("Invalid observation version")
            if version > 1:
                raise RuntimeError(f"Unsupported observation schema version: {version}")
            if version != 1 or not isinstance(data["rows"], dict):
                raise ValueError("Invalid observation ledger")
            rows = {key: Observation(**row) for key, row in data["rows"].items()}
            if any(not _valid_row(key, row) for key, row in rows.items()):
                raise ValueError("Invalid observation row")
            return rows
        except (FileNotFoundError, ValueError, TypeError, KeyError, AttributeError):
            return {}

    def rows(self) -> dict[str, Observation]:
        """Return a snapshot of locally recorded observations."""
        with self._lock:
            return self._load()

    def sweep(self) -> None:
        """Reconcile a live inventory sweep; incomplete walks retain absent rows."""
        with self._lock:
            previous = self._load()
            now = datetime.now(UTC).isoformat()
            result = previous.copy()
            found: set[str] = set()

            def visit(path: str, role: str, raw: bytes) -> None:
                found.add(path)
                digest = compute_etag(raw)
                frontmatter = _frontmatter_digest(raw)
                old = previous.get(path)
                if old and (
                    old.role,
                    old.content_digest,
                    old.frontmatter_digest,
                    old.presence,
                ) == (role, digest, frontmatter, "present"):
                    return
                result[path] = Observation(
                    path,
                    role,
                    digest,
                    frontmatter,
                    old.first_seen_at if old else now,
                    now,
                )

            complete = self._inspector.observe_files(visit)
            if complete:
                for path in previous.keys() - found:
                    old = previous[path]
                    if (
                        old.presence == "present"
                        and not self._inspector.is_internal_path(path)
                    ):
                        result[path] = replace(
                            old,
                            presence="removed",
                            removed_observed_at=now,
                            last_change_observed_at=now,
                            origin="unknown",
                        )
            if result != previous or self._path is not None:
                self._save(result)
            self._rows = result

    def _save(self, rows: dict[str, Observation]) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".observations-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"version": 1, "rows": {k: asdict(v) for k, v in rows.items()}},
                    stream,
                    sort_keys=True,
                )
            Path(name).replace(self._path)
        finally:
            with suppress(FileNotFoundError):
                Path(name).unlink()
