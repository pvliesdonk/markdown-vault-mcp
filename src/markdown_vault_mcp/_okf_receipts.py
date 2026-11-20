"""Instance-local evidence of reviews recorded by this server."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from markdown_vault_mcp.exceptions import (
    InvalidRequestError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.indexing.observations import _LEDGER_LOCKS


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
