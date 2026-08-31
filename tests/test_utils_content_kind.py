"""Tests for the note/artifact boundary predicate (#1235).

Two of these are tripwires rather than ordinary coverage. The symlink tests
pin that :func:`artifact_suffix` — the function every extension check shares —
never resolves. The transfer sink deliberately passes an already-resolved path
so it judges a symlink by its target, while ``ArtifactStore.validate_path``
passes the raw string so it judges by name; a helper that resolved internally
would silently change the sink's policy. And the ``has_md_suffix`` cases pin
that it is *not* :func:`is_note` with ``.lower()`` bolted on, so the two stay
non-interchangeable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_vault_mcp.types import DEFAULT_ATTACHMENT_EXTENSIONS
from markdown_vault_mcp.utils.content_kind import (
    artifact_suffix,
    effective_attachment_extensions,
    has_md_suffix,
    is_allowed_artifact,
    is_allowed_artifact_suffix,
    is_note,
)

_WILDCARD = frozenset({"*"})


class TestIsNote:
    """The routing predicate: case-sensitive, by design."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("a.md", True),
            ("notes/deep/a.md", True),
            ("a.MD", False),
            ("a.Md", False),
            ("a.markdown", False),
            ("a.png", False),
            ("md", False),
            ("", False),
        ],
    )
    def test_routing_cases(self, path: str, expected: bool) -> None:
        """``NOTE.MD`` is deliberately not a note here — see the module docstring."""
        assert is_note(path) is expected


class TestHasMdSuffix:
    """The case-insensitive variant, used only by SearchManager."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [("a.md", True), ("a.MD", True), ("a.Md", True), ("a.png", False)],
    )
    def test_ignores_case(self, path: str, expected: bool) -> None:
        assert has_md_suffix(path) is expected

    def test_is_not_is_note_with_lower(self) -> None:
        """A file literally named ``.md`` has no suffix, so the two differ.

        Pins that the pair cannot be collapsed into one function.
        """
        assert is_note(".md") is True
        assert has_md_suffix(".md") is False

    def test_accepts_path_objects(self) -> None:
        assert has_md_suffix(Path("notes/a.MD")) is True


class TestArtifactSuffix:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("a.PDF", "pdf"),
            ("a.pdf", "pdf"),
            ("a", ""),
            ("a.tar.gz", "gz"),
            ("dir/a.PnG", "png"),
        ],
    )
    def test_normalizes(self, path: str, expected: str) -> None:
        assert artifact_suffix(path) == expected

    def test_path_and_str_agree(self) -> None:
        assert artifact_suffix(Path("a/b.PDF")) == artifact_suffix("a/b.PDF")


class TestAllowlist:
    def test_wildcard_allows_anything(self) -> None:
        assert is_allowed_artifact_suffix("xyz", _WILDCARD) is True
        assert is_allowed_artifact_suffix("", _WILDCARD) is True

    def test_membership(self) -> None:
        assert is_allowed_artifact_suffix("pdf", frozenset({"pdf"})) is True
        assert is_allowed_artifact_suffix("xyz", frozenset({"pdf"})) is False

    def test_empty_suffix_not_in_named_allowlist(self) -> None:
        assert is_allowed_artifact_suffix("", frozenset({"pdf"})) is False

    def test_is_allowed_artifact_says_nothing_about_md(self) -> None:
        """A note passes whenever ``md`` is allowlisted — that is why the
        routing sites combine this with :func:`is_note` rather than replacing it."""
        assert is_allowed_artifact("a.md", frozenset({"md"})) is True

    def test_does_not_resolve_symlinks(self, tmp_path: Path) -> None:
        """The caller chooses name-vs-target; the helper never resolves.

        The transfer sink passes an already-resolved path and so tests the
        symlink's *target*; DocumentManager passes the raw string and so tests
        the *name*. Resolving here would silently change the sink's policy.
        """
        target = tmp_path / "target.bin"
        target.write_bytes(b"x")
        link = tmp_path / "link.pdf"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):  # pragma: no cover - platform
            pytest.skip("symlinks unavailable")

        allow_pdf = frozenset({"pdf"})
        assert is_allowed_artifact(link, allow_pdf) is True
        assert is_allowed_artifact(link.resolve(), allow_pdf) is False


class TestNonNoteAllowlistedComposition:
    """The concept the deleted ``_is_attachment`` named, composed at the call site.

    There is no ``is_attachment`` helper: the name reads like the routing test
    and is not one. These pin the same five cases the old private method did,
    expressed the way a caller must express them.
    """

    @staticmethod
    def _is_attachment(path: str, exts: frozenset[str]) -> bool:
        return not is_note(path) and is_allowed_artifact(path, exts)

    def test_allowlisted_non_note(self) -> None:
        assert self._is_attachment("image.png", DEFAULT_ATTACHMENT_EXTENSIONS) is True
        assert (
            self._is_attachment("assets/report.pdf", DEFAULT_ATTACHMENT_EXTENSIONS)
            is True
        )

    def test_note_is_never_an_attachment(self) -> None:
        assert self._is_attachment("note.md", DEFAULT_ATTACHMENT_EXTENSIONS) is False
        assert (
            self._is_attachment("notes/note.md", DEFAULT_ATTACHMENT_EXTENSIONS) is False
        )

    def test_non_allowlisted_is_not_an_attachment(self) -> None:
        assert self._is_attachment("file.xyz", DEFAULT_ATTACHMENT_EXTENSIONS) is False

    def test_wildcard_accepts_any_non_note(self) -> None:
        assert self._is_attachment("file.xyz", _WILDCARD) is True
        assert self._is_attachment("file.bin", _WILDCARD) is True
        assert self._is_attachment("notes/note.md", _WILDCARD) is False

    def test_routing_does_not_use_this_composition(self) -> None:
        """A non-allowlisted, non-note file is not an attachment, yet the tool
        layer must still route it to the artifact branch so it is rejected
        there with the allowlist error. Pins why no helper exists."""
        assert self._is_attachment("file.xyz", DEFAULT_ATTACHMENT_EXTENSIONS) is False
        assert is_note("file.xyz") is False


class TestEffectiveAttachmentExtensions:
    def test_none_means_defaults(self) -> None:
        assert effective_attachment_extensions(None) is DEFAULT_ATTACHMENT_EXTENSIONS

    def test_explicit_list_is_used(self) -> None:
        assert effective_attachment_extensions(["pdf"]) == frozenset({"pdf"})

    def test_wildcard_passes_through(self) -> None:
        assert effective_attachment_extensions(["*"]) == _WILDCARD

    def test_config_side_is_not_normalized_today(self) -> None:
        """Pins a known asymmetry as current behaviour, not as desirable.

        Only the *path* side is lower-cased and dot-stripped, so an operator
        who writes ``PDF`` or ``.pdf`` in the allowlist matches nothing. Pinning
        it here makes the eventual fix a visible one-line flip in this test
        rather than a silent semantic drift. Tracked as #1239.
        """
        assert (
            is_allowed_artifact("report.pdf", effective_attachment_extensions(["PDF"]))
            is False
        )
        assert (
            is_allowed_artifact("report.pdf", effective_attachment_extensions([".pdf"]))
            is False
        )
