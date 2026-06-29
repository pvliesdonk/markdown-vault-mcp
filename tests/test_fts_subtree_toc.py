"""FTS-layer tests for subtree TOC aggregation and max_level filtering (#773)."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import HeadingChunker, scan_directory


def _build_fts(root: Path) -> FTSIndex:
    # short_doc_lines=0 disables the short-doc bypass so heading-boundary
    # splitting fires even on small test fixtures.  max_chunk_words=1 forces
    # adaptive re-splitting at H3+ so all heading levels appear in sections.
    chunker = HeadingChunker(short_doc_lines=0, max_chunk_words=1)
    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(root, chunk_strategy=chunker):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    return fts


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "alpha.md").write_text(
        "---\ntitle: Alpha\n---\n# Alpha\n\n## Goals\n\nx\n\n### Detail\n\ny\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects" / "beta.md").write_text(
        "---\ntitle: Beta\n---\n# Beta\n\n## Plan\n\nz\n",
        encoding="utf-8",
    )
    sub = tmp_path / "Projects" / "sub"
    sub.mkdir()
    (sub / "gamma.md").write_text(
        "---\ntitle: Gamma\n---\n# Gamma\n\n## Nested\n\nq\n",
        encoding="utf-8",
    )
    # A root-level file whose name starts with "Project" (not a folder).
    (tmp_path / "Projectile.md").write_text(
        "---\ntitle: Projectile\n---\n# Projectile\n",
        encoding="utf-8",
    )
    # A sibling *folder* whose name starts with "Project" — exercises the /
    # boundary in the LIKE pattern so "Projects/" does not match "Projectile/".
    pjl = tmp_path / "Projectile"
    pjl.mkdir()
    (pjl / "note.md").write_text(
        "---\ntitle: Projectile Note\n---\n# Projectile Note\n\n## Misc\n\nm\n",
        encoding="utf-8",
    )
    return tmp_path


def test_subtree_toc_is_recursive_and_path_ordered(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, truncated = fts.get_subtree_toc("Projects")
    assert truncated is False
    assert [n["path"] for n in notes] == [
        "Projects/alpha.md",
        "Projects/beta.md",
        "Projects/sub/gamma.md",
    ]
    alpha = notes[0]
    assert alpha["title"] == "Alpha"
    # Raw section headings only — no synthetic H1 prepended at this layer.
    assert {"heading": "Goals", "level": 2} in alpha["headings"]
    assert {"heading": "Detail", "level": 3} in alpha["headings"]
    assert alpha["headings"].index({"heading": "Goals", "level": 2}) < alpha[
        "headings"
    ].index({"heading": "Detail", "level": 3})


def test_subtree_prefix_is_boundary_matched(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, _ = fts.get_subtree_toc("Project")
    assert notes == []  # neither "Projects/..." nor "Projectile.md" match
    # Also verify "Projects/" prefix does not bleed into "Projectile/" folder.
    notes_all, _ = fts.get_subtree_toc("Projects")
    assert all(not n["path"].startswith("Projectile/") for n in notes_all)


def test_subtree_max_level_filters_headings(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, _ = fts.get_subtree_toc("Projects", max_level=2)
    alpha = next(n for n in notes if n["path"] == "Projects/alpha.md")
    levels = {h["level"] for h in alpha["headings"]}
    assert levels <= {1, 2}  # H3 "Detail" dropped


def test_subtree_max_notes_truncates(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, truncated = fts.get_subtree_toc("Projects", max_notes=2)
    assert truncated is True
    assert len(notes) == 2
    assert [n["path"] for n in notes] == ["Projects/alpha.md", "Projects/beta.md"]


def test_subtree_max_notes_at_cap_not_truncated(vault: Path) -> None:
    fts = _build_fts(vault)
    notes, truncated = fts.get_subtree_toc("Projects", max_notes=3)
    assert truncated is False
    assert len(notes) == 3


def test_get_toc_max_level_filter(vault: Path) -> None:
    fts = _build_fts(vault)
    toc = fts.get_toc("Projects/alpha.md", max_level=2)
    assert all(h["level"] <= 2 for h in toc)
    assert {"heading": "Detail", "level": 3} not in toc


def test_subtree_escapes_like_wildcards(tmp_path: Path) -> None:
    (tmp_path / "notes_2026").mkdir()
    (tmp_path / "notes_2026" / "a.md").write_text(
        "---\ntitle: A\n---\n# A\n\n## Sec\n\nx\n", encoding="utf-8"
    )
    (tmp_path / "notesX2026").mkdir()
    (tmp_path / "notesX2026" / "b.md").write_text(
        "---\ntitle: B\n---\n# B\n\n## Sec\n\ny\n", encoding="utf-8"
    )
    fts = _build_fts(tmp_path)
    notes, _ = fts.get_subtree_toc("notes_2026")
    paths = [n["path"] for n in notes]
    assert paths == ["notes_2026/a.md"]
