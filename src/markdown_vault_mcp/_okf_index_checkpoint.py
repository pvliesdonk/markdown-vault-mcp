"""Durable last-applied state for generated OKF indexes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from markdown_vault_mcp.bundle_inventory import _components
from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
from markdown_vault_mcp.utils.content_kind import is_note

logger = logging.getLogger(__name__)


class IndexCheckpoint:
    """Store the concept state last applied under one designation policy."""

    def __init__(self, path: Path | None, scope: str) -> None:
        """Configure the checkpoint location and generation policy identity.

        Args:
            path: Persistent state file, or None for in-memory state.
            scope: Stable serialized identity of the generation policy.
        """
        self._path = path
        self._scope = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        self._cached: dict[str, str] | None = None

    def load(self) -> dict[str, str] | None:
        """Return last-applied state, or None when its history is unknown.

        Raises:
            StoreUnsupportedSchemaError: The checkpoint has a newer schema.
        """
        if self._cached is not None:
            return self._cached.copy()
        if self._path is None:
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            version = data["version"]
            if type(version) is not int:
                raise ValueError("invalid version")
            if version > 1:
                raise StoreUnsupportedSchemaError(
                    "Unsupported index checkpoint schema. Upgrade the server or move the checkpoint aside to start new history.",
                    backend="okf_index_checkpoint",
                    location=str(self._path),
                )
            if version != 1 or not isinstance(data["concepts"], dict):
                raise ValueError("invalid checkpoint")
            concepts = data["concepts"]
            if not all(self._valid(path, digest) for path, digest in concepts.items()):
                raise ValueError("invalid concept")
            scope = data["scope"]
            if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
                raise ValueError("invalid scope")
            if scope != self._scope:
                return None
            self._cached = concepts
            return concepts.copy()
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("okf_index_checkpoint_reset path=%s", self._path)
            return None

    @staticmethod
    def _valid(path: object, digest: object) -> bool:
        if not isinstance(path, str) or not isinstance(digest, str):
            return False
        try:
            return (
                bool(_components(path))
                and is_note(path)
                and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
            )
        except ValueError:
            return False

    def save(self, concepts: dict[str, str]) -> None:
        """Atomically persist a successfully applied concept snapshot.

        Args:
            concepts: Present concept paths and their generation-input fingerprints.
        """
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(
                dir=self._path.parent, prefix=".okf-index-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(
                        {"version": 1, "scope": self._scope, "concepts": concepts},
                        stream,
                        sort_keys=True,
                    )
                Path(name).replace(self._path)
            finally:
                with suppress(FileNotFoundError):
                    Path(name).unlink()
        self._cached = concepts.copy()
