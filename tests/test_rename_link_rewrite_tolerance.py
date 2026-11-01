"""A conditional rename accepts an etag the server's own link rewrites made stale (#1492).

Two renames sent together: B links to A, so renaming A rewrites B, and
B's etag no longer matches. The rename of B is accepted only when every
change since that etag is a link rewrite the server recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import ConcurrentModificationError
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.managers._rewrite_ledger import LinkRewriteLedger
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from pathlib import Path


class _Clock:
    """A settable stand-in for ``time.monotonic``."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestLinkRewriteLedger:
    def test_walks_a_chain_back_to_the_etag(self) -> None:
        ledger = LinkRewriteLedger()
        ledger.record("b.md", "h0", "h1")
        ledger.record("b.md", "h1", "h2")

        assert ledger.rewrites_since("b.md", etag="h0", current="h2") == 2
        assert ledger.rewrites_since("b.md", etag="h1", current="h2") == 1

    def test_an_unexplained_step_breaks_the_chain(self) -> None:
        ledger = LinkRewriteLedger()
        ledger.record("b.md", "h1", "h2")

        assert ledger.rewrites_since("b.md", etag="h0", current="h2") is None
        assert ledger.rewrites_since("b.md", etag="h0", current="hx") is None

    def test_entries_belong_to_their_path(self) -> None:
        ledger = LinkRewriteLedger()
        ledger.record("a.md", "h0", "h1")

        assert ledger.rewrites_since("b.md", etag="h0", current="h1") is None

    def test_an_expired_entry_is_no_evidence(self) -> None:
        clock = _Clock()
        ledger = LinkRewriteLedger(ttl_s=300.0, clock=clock)
        ledger.record("b.md", "h0", "h1")

        clock.now += 301.0

        assert ledger.rewrites_since("b.md", etag="h0", current="h1") is None

    def test_the_oldest_entry_is_evicted_past_capacity(self) -> None:
        ledger = LinkRewriteLedger(capacity=2)
        ledger.record("a.md", "h0", "h1")
        ledger.record("b.md", "h0", "h1")
        ledger.record("c.md", "h0", "h1")

        assert ledger.rewrites_since("a.md", etag="h0", current="h1") is None
        assert ledger.rewrites_since("c.md", etag="h0", current="h1") == 1

    def test_a_cycle_ends_without_a_match(self) -> None:
        ledger = LinkRewriteLedger()
        ledger.record("b.md", "h1", "h2")
        ledger.record("b.md", "h2", "h1")

        assert ledger.rewrites_since("b.md", etag="h0", current="h2") is None


@pytest.fixture
def linked_pair(tmp_path: Path) -> tuple[Vault, Path]:
    """A writable vault where b.md and c.md link to a.md."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nThe first note.\n")
    (root / "b.md").write_text("# B\n\nSee [A](a.md).\n")
    (root / "c.md").write_text("# C\n\nSee [[a]] and [B](b.md).\n")
    vault = Vault(source_dir=root, settings=VaultSettings(read_only=False))
    vault.index.build_index()
    return vault, root


class TestTolerantRename:
    def test_overlapping_renames_both_land(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        vault, root = linked_pair
        etag_a = compute_file_hash(root / "a.md")
        etag_b = compute_file_hash(root / "b.md")

        vault.writer.rename("a.md", "x/a.md", if_match=etag_a, update_links=True)
        result = vault.writer.rename(
            "b.md",
            "x/b.md",
            if_match=etag_b,
            update_links=True,
            tolerate_link_rewrites=True,
        )

        assert result.link_rewrites_since_etag == 1
        # The rename moved B's current bytes, keeping the first rewrite.
        assert "(x/a.md)" in (root / "x" / "b.md").read_text()
        assert "x/b.md" in (root / "c.md").read_text()

    def test_a_chain_of_rewrites_is_crossed(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")

        vault.writer.rename("a.md", "a2.md", update_links=True)
        vault.writer.rename("a2.md", "a3.md", update_links=True)
        result = vault.writer.rename(
            "b.md", "b2.md", if_match=etag_b, tolerate_link_rewrites=True
        )

        assert result.link_rewrites_since_etag == 2

    def test_a_folder_move_rewrite_is_recorded(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        (root / "old").mkdir(parents=True)
        (root / "old" / "a.md").write_text("# A\n")
        (root / "b.md").write_text("# B\n\nSee [A](old/a.md).\n")
        vault = Vault(source_dir=root, settings=VaultSettings(read_only=False))
        vault.index.build_index()
        etag_b = compute_file_hash(root / "b.md")

        vault.writer.move_folder("old", "new")
        result = vault.writer.rename(
            "b.md", "b2.md", if_match=etag_b, tolerate_link_rewrites=True
        )

        assert result.link_rewrites_since_etag == 1

    def test_a_matching_etag_reports_nothing_crossed(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")

        result = vault.writer.rename(
            "b.md", "b2.md", if_match=etag_b, tolerate_link_rewrites=True
        )

        assert result.link_rewrites_since_etag is None


class TestTolerantRenameRefuses:
    def test_strict_is_the_library_default(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")
        vault.writer.rename("a.md", "a2.md", update_links=True)

        with pytest.raises(ConcurrentModificationError):
            vault.writer.rename("b.md", "b2.md", if_match=etag_b)

        assert (root / "b.md").exists()

    def test_an_edit_after_the_rewrite(self, linked_pair: tuple[Vault, Path]) -> None:
        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")
        vault.writer.rename("a.md", "a2.md", update_links=True)
        vault.writer.append("b.md", "\nMore.\n")

        with pytest.raises(ConcurrentModificationError):
            vault.writer.rename(
                "b.md", "b2.md", if_match=etag_b, tolerate_link_rewrites=True
            )

    def test_an_external_edit_before_the_rewrite(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")
        (root / "b.md").write_text("# B\n\nEdited. See [A](a.md).\n")
        vault.writer.rename("a.md", "a2.md", update_links=True)

        with pytest.raises(ConcurrentModificationError):
            vault.writer.rename(
                "b.md", "b2.md", if_match=etag_b, tolerate_link_rewrites=True
            )

    def test_an_unrelated_etag(self, linked_pair: tuple[Vault, Path]) -> None:
        vault, _root = linked_pair
        vault.writer.rename("a.md", "a2.md", update_links=True)

        with pytest.raises(ConcurrentModificationError):
            vault.writer.rename(
                "b.md", "b2.md", if_match="0" * 64, tolerate_link_rewrites=True
            )

    def test_a_taken_destination_is_still_refused(
        self, linked_pair: tuple[Vault, Path]
    ) -> None:
        from markdown_vault_mcp.exceptions import DocumentExistsError

        vault, root = linked_pair
        etag_b = compute_file_hash(root / "b.md")
        vault.writer.rename("a.md", "a2.md", update_links=True)

        with pytest.raises(DocumentExistsError):
            vault.writer.rename(
                "b.md", "c.md", if_match=etag_b, tolerate_link_rewrites=True
            )
