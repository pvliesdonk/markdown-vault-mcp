"""Instance-local evidence of reviews recorded by this server."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.vault import Vault


class ReceiptWriteError(RuntimeError):
    """The note was written but its local receipt could not be saved."""


class ReceiptStore:
    """Append review receipts to an instance-local store."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._rows: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def _check_schema(
        self, db: sqlite3.Connection, *, allow_empty: bool = False
    ) -> int:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, 1):
            raise StoreUnsupportedSchemaError(
                f"Unsupported receipt schema version: {version}",
                backend="review_receipts",
                location=str(self._path),
            )
        columns = [row[1] for row in db.execute("PRAGMA table_info(receipts)")]
        if not columns and allow_empty and version == 0:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not tables:
                return version
        if columns != ["path", "actor", "at", "check_name", "digest", "recorded_at"]:
            raise sqlite3.DatabaseError("Invalid receipt store schema")
        return version

    def prepare(self) -> None:
        """Refuse unreadable or unsupported existing state before writing a note."""
        if self._path is None:
            return
        try:
            self._path.stat()
        except FileNotFoundError:
            return
        with (
            self._lock,
            closing(
                sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True)
            ) as db,
        ):
            self._check_schema(db)

    def append(self, receipt: dict[str, str]) -> None:
        """Record a receipt outside the bundle."""
        with self._lock:
            if self._path is None:
                self._rows.append(receipt.copy())
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                version = self._check_schema(db, allow_empty=True)
                if version == 0:
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS receipts "
                        "(path TEXT, actor TEXT, at TEXT, check_name TEXT, "
                        "digest TEXT, recorded_at TEXT)"
                    )
                    db.execute("PRAGMA user_version=1")
                db.execute(
                    "INSERT INTO receipts (path, actor, at, check_name, digest, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(
                        receipt[k]
                        for k in ("path", "by", "at", "check", "digest", "recorded_at")
                    ),
                )

    def rows(self) -> list[dict[str, str]]:
        """Return local receipts; unavailable state raises instead of appearing empty."""
        with self._lock:
            if self._path is None:
                return [row.copy() for row in self._rows]
            with closing(
                sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True)
            ) as db:
                self._check_schema(db)
                rows = db.execute(
                    "SELECT path, actor, at, check_name, digest, recorded_at "
                    "FROM receipts ORDER BY rowid"
                ).fetchall()
            keys = ("path", "by", "at", "check", "digest", "recorded_at")
            return [dict(zip(keys, row, strict=True)) for row in rows]


_stores: WeakKeyDictionary[Vault, ReceiptStore] = WeakKeyDictionary()
_stores_lock = threading.Lock()


def receipts_for(vault: Vault) -> ReceiptStore:
    """Return the receipt store beside this vault's observation ledger."""
    from markdown_vault_mcp.exceptions import InvalidRequestError

    with _stores_lock:
        if vault not in _stores:
            state = vault._state_path
            path = state.with_name(state.name + ".receipts.sqlite3") if state else None
            if path is not None and path.resolve().is_relative_to(
                vault._source_dir.resolve()
            ):
                raise InvalidRequestError(
                    "Local review receipts require a state path outside the vault"
                )
            _stores[vault] = ReceiptStore(path)
        return _stores[vault]
