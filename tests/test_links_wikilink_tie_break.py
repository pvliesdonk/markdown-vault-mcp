"""The wikilink tie-break is Obsidian's, observed (#1350).

When several documents match a bare or path-qualified wikilink, the code
picked the shortest path string and the design doc said fewest path
components; neither was sourced, and the pinned fixtures agreed on both.
The maintainer ran the reference page's discriminating fixture on Obsidian
1.13.7 (``app.metadataCache.getFirstLinkpathDest``); the four vaults below
are that run, verbatim, and the rule they fix is:

1. an exact vault path wins (``[[Note]]`` is ``Note.md`` at the root,
   ``[[b/Note]]`` is ``b/Note.md``), even over the source's own folder;
2. otherwise a match in the source note's own folder wins — an ancestor
   folder counts for nothing;
3. otherwise the shortest path string wins: not fewest components, not
   file-tree order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


def _write(vault: Path, rel: str, body: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _target(col: Vault, source: str) -> str:
    outlinks = col.graph.get_outlinks(source)
    assert len(outlinks) == 1, outlinks
    return outlinks[0].target_path


@pytest.fixture
def vault_1(tmp_path: Path) -> Path:
    """Root ``Note.md`` present; the observed answer is root from everywhere."""
    vault = tmp_path / "vault"
    for rel, body in {
        "Note.md": "root\n",
        "zzzz/Note.md": "zzzz\n",
        "a/b/Note.md": "a-b\n",
        "src-root.md": "[[Note]]\n",
        "zzzz/src-zzzz.md": "[[Note]]\n",
        "a/b/src-ab.md": "[[Note]]\n",
        "y.md": "y\n",
        "a/x.md": "[[../y]]\n",
        "suffix.md": "[[b/Note]]\n",
    }.items():
        _write(vault, rel, body)
    return vault


class TestVault1RootPresent:
    def test_the_exact_root_path_wins_from_every_folder(self, vault_1: Path) -> None:
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            assert _target(col, "src-root.md") == "Note.md"
            assert _target(col, "zzzz/src-zzzz.md") == "Note.md"
            # Even from a/b/, where a/b/Note.md sits beside the source.
            assert _target(col, "a/b/src-ab.md") == "Note.md"
        finally:
            col.close()

    def test_a_relative_wikilink_resolves_against_the_source(
        self, vault_1: Path
    ) -> None:
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            assert _target(col, "a/x.md") == "y.md"
        finally:
            col.close()

    def test_a_path_suffix_matches_when_no_exact_path_exists(
        self, vault_1: Path
    ) -> None:
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            assert _target(col, "suffix.md") == "a/b/Note.md"
        finally:
            col.close()


class TestVault2RootDeleted:
    def test_own_folder_then_shortest_string(self, vault_1: Path) -> None:
        (vault_1 / "Note.md").unlink()
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            # No own-folder match at the root: shortest string, and it is
            # a/b/Note.md (11) over zzzz/Note.md (12) — not fewest components.
            assert _target(col, "src-root.md") == "a/b/Note.md"
            # Own folder beats the shorter string.
            assert _target(col, "zzzz/src-zzzz.md") == "zzzz/Note.md"
            assert _target(col, "a/b/src-ab.md") == "a/b/Note.md"
        finally:
            col.close()


class TestVault3MoreCandidates:
    def test_shortest_string_not_tree_order_and_no_ancestor_preference(
        self, vault_1: Path
    ) -> None:
        (vault_1 / "Note.md").unlink()
        _write(vault_1, "b/Note.md", "b\n")
        _write(vault_1, "aaaaaaaa/Note.md", "aaaaaaaa\n")
        _write(vault_1, "zzzz/deep/src-deep.md", "[[Note]]\n")
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            # b/Note.md (9) is the shortest string; a/b/Note.md would be
            # tree order; aaaaaaaa/Note.md would be fewest components.
            assert _target(col, "src-root.md") == "b/Note.md"
            assert _target(col, "zzzz/src-zzzz.md") == "zzzz/Note.md"
            # zzzz/ is an ancestor of the source, and that counts for nothing.
            assert _target(col, "zzzz/deep/src-deep.md") == "b/Note.md"
            assert _target(col, "a/b/src-ab.md") == "a/b/Note.md"
        finally:
            col.close()


class TestVault4ExactPathBeatsOwnFolder:
    def test_an_exact_path_beats_the_own_folder_suffix_match(
        self, vault_1: Path
    ) -> None:
        (vault_1 / "Note.md").unlink()
        _write(vault_1, "b/Note.md", "b\n")
        _write(vault_1, "a/b/src-ab-path.md", "[[b/Note]]\n")
        col = Vault(source_dir=vault_1)
        col.index.build_index()
        try:
            assert _target(col, "a/b/src-ab-path.md") == "b/Note.md"
            assert _target(col, "suffix.md") == "b/Note.md"
        finally:
            col.close()


class TestAliasesShareTheTieBreak:
    """By analogy, not observed: own folder, then shortest path."""

    def test_own_folder_alias_wins(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _write(vault, "short.md", "---\naliases: [AI]\n---\n# S\n")
        _write(vault, "topic/long-name.md", "---\naliases: [AI]\n---\n# L\n")
        _write(vault, "topic/src.md", "See [[AI]].\n")
        col = Vault(source_dir=vault)
        col.index.build_index()
        try:
            assert _target(col, "topic/src.md") == "topic/long-name.md"
        finally:
            col.close()
