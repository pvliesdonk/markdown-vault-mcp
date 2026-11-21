"""A link target that climbs above the vault root is external (#2416).

OKF 0.3 §6: "A value that resolves to a location outside the bundle root
is external. Consumers MUST NOT read it from the local file system." The
resolver used to drop the surplus ``..`` and land on an in-vault note, so
``../../x.md`` from ``a/n.md`` became an edge to ``x.md``. An external
target is now what a URL already is: no link row, so no backlink, outlink,
broken-link entry, graph edge or rename rewrite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import _resolve_link_path, extract_links
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
def test_extraction_skips_an_out_of_root_target(body: str) -> None:
    assert extract_links(body, "a/n.md") == []


def test_an_in_root_neighbour_is_still_extracted() -> None:
    links = extract_links("[x](../../x.md) and [y](../y.md)", "a/n.md")
    assert [link.target_path for link in links] == ["y.md"]


def test_vault_wide_lookup_does_not_climb_out() -> None:
    """``b/../../x.md`` stays in the vault from ``a/``, but not from the root.

    The vault-wide fallback re-resolves a missing destination from the root;
    there it climbs out, so the link keeps its source-relative target.
    """
    idx = FTSIndex(":memory:")
    try:
        idx.build_from_notes(
            [
                make_note(
                    path="a/n.md", links=extract_links("[x](b/../../x.md)", "a/n.md")
                )
            ]
        )
        idx.resolve_vault_wikilinks()
        assert idx.get_outlinks("a/n.md")[0]["target_path"] == "x.md"
    finally:
        idx.close()


class TestThroughAVault:
    """Every link surface agrees: the external target is not a link."""

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

    def test_no_edge_backlink_or_broken_link(self, tmp_path: Path) -> None:
        vault = self._vault(tmp_path)
        assert vault.graph.get_outlinks("a/n.md") == []
        assert vault.graph.get_backlinks("x.md") == []
        assert vault.graph.get_broken_links() == []

    def test_rename_leaves_the_external_link_alone(self, tmp_path: Path) -> None:
        vault = self._vault(tmp_path)
        source = tmp_path / "vault" / "a" / "n.md"
        before = source.read_bytes()
        result = vault.writer.rename("x.md", "y.md", update_links=True)
        assert result.updated_links == 0
        assert source.read_bytes() == before
