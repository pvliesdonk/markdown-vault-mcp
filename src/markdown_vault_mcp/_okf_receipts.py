"""Instance-local evidence of reviews recorded by this server."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from markdown_vault_mcp.exceptions import (
    InvalidRequestError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.indexing.observations import _LEDGER_LOCKS
from markdown_vault_mcp.okf import (
    _parse_stale_after,
    _review_claims,
    _review_recorded,
    verified_entries,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _snapshot_pair(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    algo, digest = value.get("algo"), value.get("digest")
    if (
        not isinstance(algo, str)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        return None
    return algo, digest


def _assess_claim(
    entry: dict[str, Any],
    claim: dict[str, Any] | None,
    snapshot: dict[str, str],
    malformed_container: bool,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "by": entry["by"],
        "at": entry["at"],
        "snapshot": "unbound",
        "check": None,
        "origin": "supplied",
        "local_evidence": "unavailable",
    }
    if claim is None:
        if malformed_container:
            item["snapshot_basis"] = "malformed_binding"
        return item
    check = claim.get("check")
    pair = _snapshot_pair(claim.get("snapshot"))
    if isinstance(check, str):
        item["check"] = check
    if pair is not None and pair[0] == snapshot["algo"]:
        item["snapshot"] = "matches" if pair[1] == snapshot["digest"] else "differs"
    elif pair is not None:
        item["snapshot_basis"] = "unsupported_algorithm"
    elif "snapshot" in claim:
        item["snapshot_basis"] = "malformed_binding"
    return item


def _review_key(row: Any) -> tuple[str, Any] | None:
    if not isinstance(row, dict) or not isinstance(row.get("by"), str):
        return None
    instant = _parse_stale_after(row.get("at"))
    return (row["by"], instant) if isinstance(instant, datetime) else None


def _receipt_tuple(row: Any) -> tuple[Any, ...] | None:
    key = _review_key(row)
    if key is None or not isinstance(row.get("check"), str):
        return None
    pair = _snapshot_pair(row.get("snapshot"))
    return (*key, row["check"], pair) if pair is not None else None


def assess_reviews(
    metadata: dict[str, Any],
    snapshot: dict[str, str],
    load: Callable[[], list[dict[str, Any]] | None] | None,
) -> list[dict[str, Any]]:
    """Assess supplied claims and path receipts for recorded review identities.

    Args:
        metadata: Parsed concept frontmatter.
        snapshot: Current concept digest.
        load: Deferred path-scoped receipt lookup, if available.

    Returns:
        Separate binding, check and origin facts for each claim or unbound entry.
    """
    groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for entry in verified_entries(metadata):
        if _review_recorded(entry):
            key = _review_key(entry)
            if key is not None:
                groups.setdefault(key, []).append(entry)
    raw = _review_claims(metadata)
    malformed = raw is None or any(_review_key(row) is None for row in raw)
    claims = [row for row in raw or [] if _review_key(row) in groups]
    rows = (load() if load is not None else None) if claims else None
    receipts = {_receipt_tuple(row) for row in rows or []}
    result = []
    for key, entries in groups.items():
        selected = [claim for claim in claims if _review_key(claim) == key]
        for claim in selected:
            item = _assess_claim(entries[0], claim, snapshot, malformed)
            if _receipt_tuple(claim) is not None and _receipt_tuple(claim) in receipts:
                item["origin"] = "local_receipt"
                item["local_evidence"] = "available"
            result.append(item)
        for entry in entries[len(selected) :]:
            result.append(_assess_claim(entry, None, snapshot, malformed))
    return result


class ReceiptNotRecordedError(Exception):
    """A review reached its note, but its local receipt was not saved."""

    def __init__(self, path: str) -> None:
        """Name the note that holds the review.

        Args:
            path: Vault-relative path of the note the review was written to.
        """
        super().__init__(f"Review recorded in note {path!r}, but no local receipt")
        self.path = path


class ReceiptStore:
    """Append review receipts to instance-local state outside the vault.

    Persistent stores share the observation ledger's per-path lock registry, so
    every store on one path in this process serialises its read-modify-write.
    Like the ledger, the state path belongs to one server process.
    """

    def __init__(self, path: Path | None, source_dir: Path) -> None:
        """Bind the store to its state file.

        Args:
            path: JSON state file, or ``None`` to keep receipts in memory.
            source_dir: Vault root; a state file inside it is refused.
        """
        self._path = path
        self._source_dir = source_dir
        self._rows: list[dict[str, Any]] = []
        self._lock = (
            threading.Lock()
            if path is None
            else _LEDGER_LOCKS.setdefault(path.resolve(), threading.Lock())
        )

    def _load(self) -> list[dict[str, Any]]:
        if self._path is None:
            return list(self._rows)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or not isinstance(data.get("rows"), list)
                or not all(isinstance(row, dict) for row in data["rows"])
            ):
                raise ValueError("Invalid receipt store")
            return cast("list[dict[str, Any]]", data["rows"])
        except FileNotFoundError:
            return []
        except (ValueError, TypeError) as exc:
            raise StoreUnsupportedSchemaError(
                "Unsupported or corrupt review receipt store",
                backend="review_receipts",
                location=str(self._path),
            ) from exc

    def check(self) -> None:
        """Check that receipts can be read before asking for a review.

        Raises:
            InvalidRequestError: If the state file lies inside the vault.
            StoreUnsupportedSchemaError: If the state file is corrupt or newer.
            OSError: If the state file cannot be read.
        """
        if self._path is not None and self._path.resolve().is_relative_to(
            self._source_dir.resolve()
        ):
            raise InvalidRequestError(
                "Local review receipts require a state path outside the vault"
            )
        with self._lock:
            self._load()

    def review_observation(
        self, path: str, metadata: dict[str, Any], snapshot: dict[str, str] | None
    ) -> dict[str, Any]:
        """Compare the current concept with the latest locally recorded review.

        Args:
            path: Vault-relative note path.
            metadata: Supplied note metadata.
            snapshot: Current concept digest, if available.

        Returns:
            An available comparison, or unavailable when local evidence is missing.
        """
        entries = [
            entry for entry in verified_entries(metadata) if _review_recorded(entry)
        ]
        if not entries or snapshot is None:
            return {"state": "unavailable"}
        latest = max(
            cast("datetime", _parse_stale_after(entry["at"])) for entry in entries
        )
        latest_entries = [
            entry for entry in entries if _parse_stale_after(entry["at"]) == latest
        ]
        actors = {entry["by"] for entry in latest_entries}
        if len(actors) != len(latest_entries):
            return {"state": "unavailable"}
        try:
            with self._lock:
                rows = self._load()
        except (OSError, StoreUnsupportedSchemaError):
            return {"state": "unavailable"}
        matching = [
            row
            for row in rows
            if row.get("path") == path
            and _parse_stale_after(row.get("at")) == latest
            and isinstance(row.get("by"), str)
            and row["by"] in actors
        ]
        if len(matching) != len(actors) or {row["by"] for row in matching} != actors:
            return {"state": "unavailable"}
        for row in matching:
            baseline = _snapshot_pair(row.get("snapshot"))
            if (
                baseline is None
                or baseline[0] != snapshot.get("algo")
                or baseline[1] == snapshot.get("digest")
            ):
                return {"state": "unavailable"}
        return {"state": "available", "later_change_observed": True}

    def receipts(self, path: str) -> list[dict[str, Any]] | None:
        """Return path-scoped receipts, or None when they cannot be read."""
        try:
            with self._lock:
                rows = self._load()
        except (OSError, StoreUnsupportedSchemaError):
            return None
        return [row for row in rows if row.get("path") == path]

    def append(self, receipt: dict[str, Any]) -> None:
        """Record the receipt of a review already written to its note.

        Args:
            receipt: Receipt row to add.

        Raises:
            StoreUnsupportedSchemaError: If the state file is corrupt or newer.
            OSError: If the state file cannot be read or replaced.
        """
        with self._lock:
            rows = [*self._load(), receipt]
            if self._path is None:
                self._rows = rows
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(
                dir=self._path.parent, prefix=".receipts-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"version": 1, "rows": rows}, stream)
                Path(name).replace(self._path)
            finally:
                with suppress(FileNotFoundError):
                    Path(name).unlink()
