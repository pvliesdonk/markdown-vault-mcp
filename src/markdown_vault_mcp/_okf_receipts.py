"""Instance-local evidence of reviews recorded by this server."""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

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

    def append(self, receipt: dict[str, str]) -> None:
        """Record a receipt outside the bundle."""
        with self._lock:
            if self._path is None:
                self._rows.append(receipt.copy())
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS receipts "
                    "(path TEXT, actor TEXT, at TEXT, check_name TEXT, "
                    "digest TEXT, recorded_at TEXT)"
                )
                db.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(
                        receipt[k]
                        for k in ("path", "by", "at", "check", "digest", "recorded_at")
                    ),
                )

    def rows(self) -> list[dict[str, str]]:
        """Return locally recorded receipts."""
        with self._lock:
            if self._path is None:
                return [row.copy() for row in self._rows]
            try:
                with sqlite3.connect(
                    f"{self._path.resolve().as_uri()}?mode=ro", uri=True
                ) as db:
                    rows = db.execute(
                        "SELECT * FROM receipts ORDER BY rowid"
                    ).fetchall()
            except sqlite3.OperationalError:
                return []
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
