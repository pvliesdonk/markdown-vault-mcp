"""Tests for :func:`markdown_vault_mcp.utils.normalize_folder` (#1103, #1106)."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.utils import normalize_folder


def test_none_means_no_restriction() -> None:
    """``None`` is passed through untouched — no folder restriction at all."""
    assert normalize_folder(None) is None


@pytest.mark.parametrize("value", ["", "/", "//"])
def test_root_selector_is_preserved_as_empty_string(value: str) -> None:
    """An explicit root selector stays ``""`` and never collapses to ``None``.

    Collapsing it would turn "root-level documents only" into "no
    restriction" on every surface sharing this helper (#1106).
    """
    assert normalize_folder(value) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("X", "X"),
        ("X/", "X"),
        ("/X", "X"),
        ("/X/", "X"),
        ("X/Y", "X/Y"),
        ("X/Y/", "X/Y"),
        ("X\\Y", "X/Y"),
        ("X\\Y\\", "X/Y"),
    ],
)
def test_folds_slashes_to_the_stored_spelling(value: str, expected: str) -> None:
    """Every natural spelling folds to the form stored in the index."""
    assert normalize_folder(value) == expected


def test_is_idempotent() -> None:
    """Normalizing an already-normalized value changes nothing."""
    for value in (None, "", "X", "X/Y"):
        assert normalize_folder(normalize_folder(value)) == normalize_folder(value)
