"""A link target that climbs above the vault root is external (#2416).

OKF 0.3 §6: "A value that resolves to a location outside the bundle root
is external. Consumers MUST NOT read it from the local file system." The
resolver used to drop the surplus ``..`` and land on an in-vault note, so
``../../x.md`` from ``a/n.md`` became an edge to ``x.md``. The account
holder ruled such a target "External and broken" (2026-11-21): it is never
a note, an outlink, a backlink, a graph edge or a rename candidate, but its
row is kept, led by ``..`` (``../x.md``), so the broken-link report lists it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import (
    _resolve_link_path,
    extract_links,
    is_out_of_root_target,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.test_links import make_note

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("target", "source"),
    [
        ("../../x.md", "a/n.md"),
        ("../x.md", "n.md"),
        ("/../x.md", "a/n.md"),
        ("a/../../x.md", "n.md"),
    ],
)
def test_resolver_reports_an_out_of_root_target_as_external(
    target: str, source: str
) -> None:
    assert _resolve_link_path(target, source) == (None, None)


@pytest.mark.parametrize(
    ("target", "source", "expected"),
    [
        ("../x.md", "a/n.md", "x.md"),
        ("../../x.md", "a/b/n.md", "x.md"),
        ("../a/../x.md", "a/n.md", "x.md"),
        ("/a/../x.md", "a/b/n.md", "x.md"),
    ],
)
def test_resolver_keeps_a_target_that_reaches_the_root(
    target: str, source: str, expected: str
) -> None:
    """Climbing to the root itself, not past it, stays in the vault."""
    assert _resolve_link_path(target, source) == (expected, None)


@pytest.mark.parametrize(
    "body",
    [
        "[x](../../x.md)",
        "[x](<../../x.md>)",
        "[x](%2E%2E/%2E%2E/x.md)",
        "[x](../../x.md#part)",
        "[x](/../x.md)",
        "[x][r]\n\n[r]: ../../x.md\n",
        "[[../../x]]",
    ],
)
def test_extraction_keeps_an_out_of_root_target_above_the_root(body: str) -> None:
    """The row names where the target lies relative to the root: ``../x.md``."""
    [link] = extract_links(body, "a/n.md")
    assert link.target_path == "../x.md"
    assert is_out_of_root_target(link.target_path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("..", True), ("../x.md", True), ("x.md", False), ("..x.md", False)],
)
def test_is_out_of_root_target(path: str, expected: bool) -> None:
    assert is_out_of_root_target(path) is expected


def test_an_in_root_neighbour_resolves_beside_it() -> None:
    links = extract_links("[x](../../x.md) and [y](../y.md)", "a/n.md")
    assert [link.target_path for link in links] == ["../x.md", "y.md"]


def test_vault_wide_lookup_does_not_climb_out() -> None:
    """``b/../../x.md`` stays in the vault from ``a/``, but not from the root.

    The vault-wide fallback re-resolves a missing destination from the root;
    there it climbs out, so the link keeps its source-relative target rather
    than suffix-matching the decoy ``c/x.md``.
    """
    idx = FTSIndex(":memory:")
    try:
        idx.build_from_notes(
            [
                make_note(
                    path="a/n.md", links=extract_links("[x](b/../../x.md)", "a/n.md")
                ),
                make_note(path="c/x.md"),
            ]
        )
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("a/n.md")[0]["target_path"] == "x.md"
    finally:
        idx.close()


class TestThroughAVault:
    """External for every link surface, except the broken-link report."""

    @staticmethod
    def _vault(tmp_path: Path) -> Vault:
        src = tmp_path / "vault"
        (src / "a").mkdir(parents=True)
        (src / "x.md").write_text("# X\n", encoding="utf-8")
        (src / "a" / "n.md").write_text(
            "[out](../../x.md)\n\n[[../../x]]\n", encoding="utf-8"
        )
        vault = Vault(source_dir=src, settings=VaultSettings(read_only=False))
        vault.index.build_index()
        return vault

    def test_no_outlink_backlink_or_edge(self, tmp_path: Path) -> None:
        vault = self._vault(tmp_path)
        assert vault.graph.get_outlinks("a/n.md") == []
        assert vault.graph.get_backlinks("x.md") == []
        assert vault.graph.get_most_linked() == []
        assert vault.graph.get_connection_path("a/n.md", "x.md") is None

    def test_reported_as_broken_links(self, tmp_path: Path) -> None:
        vault = self._vault(tmp_path)
        broken = vault.graph.get_broken_links()
        assert [
            (b.source_path, b.target_path, b.link_type, b.raw_target) for b in broken
        ] == [
            ("a/n.md", "../x.md", "markdown", "../../x.md"),
            ("a/n.md", "../x.md", "wikilink", "../../x"),
        ]
        assert vault._fts.count_broken_links() == 2

    def test_its_source_is_an_orphan(self, tmp_path: Path) -> None:
        """An external target is no outgoing edge, so ``a/n.md`` links nowhere."""
        vault = self._vault(tmp_path)
        orphans = {note.path for note in vault.graph.get_orphan_notes()}
        assert orphans == {"a/n.md", "x.md"}

    def test_rename_leaves_the_external_link_alone(self, tmp_path: Path) -> None:
        vault = self._vault(tmp_path)
        source = tmp_path / "vault" / "a" / "n.md"
        before = source.read_bytes()
        result = vault.writer.rename("x.md", "y.md", update_links=True)
        assert result.updated_links == 0
        assert source.read_bytes() == before
        assert [b.target_path for b in vault.graph.get_broken_links()] == [
            "../x.md",
            "../x.md",
        ]

    def test_refresh_keeps_the_row_above_the_root(self, tmp_path: Path) -> None:
        """A rescan's vault-wide pass never re-points it onto ``x.md``."""
        vault = self._vault(tmp_path)
        vault._fts.resolve_vault_wikilinks()
        assert len(vault.graph.get_broken_links()) == 2


def test_a_write_reports_it_unresolved(tmp_path: Path) -> None:
    """The write-time check counts it with the broken links, not the outlinks."""
    (tmp_path / "x.md").write_text("# X\n", encoding="utf-8")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    vault.index.build_index()
    result = vault.writer.write(
        "a/n.md", "[x](../../x.md)\n", report_unresolved_links=True
    )
    assert result.unresolved_links == ["../../x.md"]
