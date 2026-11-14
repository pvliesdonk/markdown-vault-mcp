"""Synthetic OKF note shared by active and inactive public-read scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def malformed_okf_note(okf_vault: Path) -> tuple[str, Path, bytes]:
    """Create a malformed provenance note and capture its original bytes."""
    path = "guides/malformed.md"
    raw = (
        "---\ntype: Note\nsources: https://example.com\n"
        "generated: codex at a date\n---\n# Malformed\n\n"
        "Zebra malformed provenance.\n\n## Steps\n\nZebra checklist.\n"
    )
    note = okf_vault / path
    note.write_text(raw, encoding="utf-8")
    return path, note, note.read_bytes()
