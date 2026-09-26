"""summarize reports skipped notes apart from the note limit (#1637)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import DocumentUnreadableError, NoteTooLargeError
from markdown_vault_mcp.types import SummarySkip
from markdown_vault_mcp.vault import VaultSettings
from tests.test_summarize import FakeSummarizer, make_vault  # noqa: F401

if TYPE_CHECKING:
    import pytest

    from tests.test_summarize import VaultFactory


def test_missing_note_is_skipped_not_omitted(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    vault = make_vault(summarizer=FakeSummarizer())
    result = vault.summarizer.summarize(["alpha.md", "ghost.md"])
    assert result.notes_omitted == 0
    assert result.skipped == [SummarySkip(path="ghost.md", reason="not_found")]
    assert result.hint is not None
    assert "ghost.md" in result.hint
    assert "note limit" not in result.hint


def test_oversized_note_is_skipped_with_the_section_step(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    vault = make_vault(
        summarizer=FakeSummarizer(),
        notes={"small.md": "# S\n\ntiny", "big.md": "# B\n\n" + "x " * 500},
        settings=VaultSettings(max_note_read_bytes=64),
    )
    result = vault.summarizer.summarize(["small.md", "big.md"])
    assert result.skipped == [SummarySkip(path="big.md", reason="over_read_limit")]
    assert result.notes_omitted == 0
    assert result.truncated
    assert "section" in (result.hint or "")


def test_unreadable_note_is_skipped_and_the_user_told(
    make_vault: VaultFactory,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = make_vault(
        summarizer=FakeSummarizer(), notes={"a.md": "# A\n\nx", "b.md": "# B\n\ny"}
    )
    real_read = vault._doc_mgr.read

    def read(path: str, **kwargs: object) -> object:
        if path == "b.md":
            raise DocumentUnreadableError(path, "boom")
        return real_read(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vault._doc_mgr, "read", read)
    result = vault.summarizer.summarize(["a.md", "b.md"])
    assert result.skipped == [SummarySkip(path="b.md", reason="unreadable")]
    assert result.notes_omitted == 0
    assert "user" in (result.hint or "")


def test_note_limit_still_counts_in_notes_omitted(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    vault = make_vault(summarizer=FakeSummarizer())
    result = vault.summarizer.summarize(["sub"], max_notes=1)
    assert result.notes_omitted == 1
    assert result.skipped == []
    assert "note limit" in (result.hint or "")


def test_read_cap_is_its_own_signal(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    import pytest

    vault = make_vault(
        notes={"big.md": "# B\n\n" + "x " * 500},
        settings=VaultSettings(max_note_read_bytes=64),
    )
    with pytest.raises(NoteTooLargeError):
        vault.reader.read("big.md")


def test_every_matched_note_is_included_skipped_or_omitted(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    """The three counts add up, with the limit and a skip in the same call."""
    vault = make_vault(
        summarizer=FakeSummarizer(),
        notes={f"f/n{i}.md": f"# N{i}\n\nbody" for i in range(5)},
    )
    # ghost.md is matched first, so it is inside the limit and read (skipped);
    # a path matched after the limit is omitted without being read.
    result = vault.summarizer.summarize(["ghost.md", "f"], max_notes=3)
    matched = 6
    assert result.notes_included + len(result.skipped) + result.notes_omitted == (
        matched
    )
    assert (result.notes_included, len(result.skipped), result.notes_omitted) == (
        2,
        1,
        3,
    )
    assert "note limit" in (result.hint or "")
    assert "ghost.md" in (result.hint or "")


def test_invalid_path_is_skipped_with_a_next_step(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    vault = make_vault(summarizer=FakeSummarizer())
    result = vault.summarizer.summarize(["alpha.md", "b\x00.md"])
    assert result.skipped == [SummarySkip(path="b\x00.md", reason="invalid_path")]
    hint = result.hint or ""
    assert "list_documents" in hint
    assert "\x00" not in hint  # shown escaped, not as a raw control character
