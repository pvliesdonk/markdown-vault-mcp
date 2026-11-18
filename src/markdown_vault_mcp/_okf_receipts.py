"""Instance-local evidence of reviews recorded by this server."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any, cast

from markdown_vault_mcp.exceptions import (
    InvalidRequestError,
    StoreUnsupportedSchemaError,
)

if TYPE_CHECKING:
    from pathlib import Path


class ReceiptStore:
    """Append review receipts to instance-local state outside the vault."""

    def __init__(self, path: Path | None, source_dir: Path) -> None:
        self._path = path
        self._source_dir = source_dir
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def _load(self) -> list[dict[str, Any]]:
        if self._path is None:
            return list(self._rows)
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except UnicodeError as exc:
            raise StoreUnsupportedSchemaError(
                "Unsupported or corrupt review receipt store",
                backend="review_receipts",
                location=str(self._path),
            ) from exc
        try:
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or not isinstance(data.get("rows"), list)
            ):
                raise ValueError("Invalid receipt schema")
            if not all(isinstance(row, dict) for row in data["rows"]):
                raise ValueError("Invalid receipt rows")
            return cast("list[dict[str, Any]]", data["rows"])
        except (ValueError, TypeError) as exc:
            raise StoreUnsupportedSchemaError(
                "Unsupported or corrupt review receipt store",
                backend="review_receipts",
                location=str(self._path),
            ) from exc

    def check(self) -> None:
        """Check local receipt state before asking for a review."""
        if self._path is not None and self._path.resolve().is_relative_to(
            self._source_dir.resolve()
        ):
            raise InvalidRequestError(
                "Local review receipts require a state path outside the vault"
            )
        with self._lock:
            self._load()

    def append(self, receipt: dict[str, Any]) -> None:
        """Record a receipt before writing its claim to a note."""
        with self._lock:
            rows = self._load()
            rows.append(receipt)
            if self._path is None:
                self._rows = rows
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(self._path.name + ".tmp")
            try:
                temporary.write_text(
                    json.dumps({"version": 1, "rows": rows}), encoding="utf-8"
                )
                temporary.replace(self._path)
            finally:
                temporary.unlink(missing_ok=True)
