"""Tests for :func:`markdown_vault_mcp.utils.attachment_target_exists` (#1333).

The vault-truth half of link resolution: the index knows only about notes, so
this is what tells a link to an on-disk attachment from a link to nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.utils import attachment_target_exists

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A vault root holding one attachment, one note, and one extensionless file."""
    root = tmp_path / "vault"
    (root / "Images").mkdir(parents=True)
    (root / "Images" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "note.md").write_text("# Note\n", encoding="utf-8")
    (root / "Makefile").write_text("all:\n", encoding="utf-8")
    return root


class TestAttachmentTargetExists:
    def test_allowlisted_attachment_on_disk(self, vault: Path) -> None:
        assert attachment_target_exists("Images/pic.png", vault, frozenset({"png"}))

    def test_allowlisted_but_absent(self, vault: Path) -> None:
        assert not attachment_target_exists(
            "Images/gone.png", vault, frozenset({"png"})
        )

    def test_present_but_not_allowlisted(self, vault: Path) -> None:
        assert not attachment_target_exists("Images/pic.png", vault, frozenset({"pdf"}))

    def test_a_note_is_never_an_attachment(self, vault: Path) -> None:
        """Even with ``md`` allowlisted — the note branch owns those."""
        assert not attachment_target_exists("note.md", vault, frozenset({"md", "png"}))

    def test_wildcard_serves_an_extensionless_file(self, vault: Path) -> None:
        """No extension-shape guard here: ``read`` serves this under ``*``.

        This is the deliberate asymmetry with ``scanner._names_attachment``,
        which does apply a shape guard because it answers a different
        question (whether to append ``.md`` at extraction time).
        """
        assert attachment_target_exists("Makefile", vault, frozenset({"*"}))

    def test_directory_is_not_a_file(self, vault: Path) -> None:
        (vault / "Images.png").mkdir()
        assert not attachment_target_exists("Images.png", vault, frozenset({"png"}))

    def test_traversal_outside_the_vault_is_refused(self, vault: Path) -> None:
        """The file exists one level up; the guard must still say no."""
        (vault.parent / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        assert not attachment_target_exists(
            "Images/../../outside.png", vault, frozenset({"png"})
        )

    def test_unresolvable_path_is_refused_not_raised(self, vault: Path) -> None:
        """A symlink loop raises OSError (RuntimeError on Python < 3.13)."""
        with patch(
            "markdown_vault_mcp.utils.resolve_inside",
            side_effect=OSError("Too many levels of symbolic links"),
        ):
            assert not attachment_target_exists(
                "Images/pic.png", vault, frozenset({"png"})
            )

    def test_unstattable_file_is_refused_not_raised(self, vault: Path) -> None:
        """``is_file()`` can itself raise on an unreadable path component."""
        target = vault / "Images" / "pic.png"
        with patch.object(type(target), "is_file", side_effect=OSError("EACCES")):
            assert not attachment_target_exists(
                "Images/pic.png", vault, frozenset({"png"})
            )
