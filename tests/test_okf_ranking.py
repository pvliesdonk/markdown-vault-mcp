"""Tests for OKF ranking downweights (#965, design §5, phase 6).

Three layers:

- The pure factor policy :func:`okf_downweight_factor` (deprecated / stale /
  reserved composition, ordering deprecated < stale < normal).
- The pure re-ranker :func:`apply_okf_downweight` (score scaling, re-sort,
  positive-only, no mutation).
- End-to-end through a declared, OKF-active vault across all three search
  modes: the ordering and reserved-file demotion are pinned against a ranking
  corpus, and a non-declared vault is byte-identical (the downweight is inert).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.managers.search import _apply_okf_downweight
from markdown_vault_mcp.okf import (
    OKF_DEPRECATED_WEIGHT,
    OKF_RESERVED_WEIGHT,
    OKF_STALE_WEIGHT,
    okf_downweight_factor,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from markdown_vault_mcp.vault import Vault


# ---------------------------------------------------------------------------
# Pure factor policy
# ---------------------------------------------------------------------------


class TestOkfDownweightFactor:
    def test_normal_note_is_unchanged(self) -> None:
        assert (
            okf_downweight_factor({"status": "stable", "stale": False}, "n.md") == 1.0
        )

    def test_deprecated(self) -> None:
        f = okf_downweight_factor({"status": "deprecated", "stale": False}, "n.md")
        assert f == OKF_DEPRECATED_WEIGHT

    def test_stale(self) -> None:
        f = okf_downweight_factor({"status": "stable", "stale": True}, "n.md")
        assert f == OKF_STALE_WEIGHT

    @pytest.mark.parametrize("name", ["index.md", "log.md", "guides/log.md"])
    def test_reserved_file(self, name: str) -> None:
        f = okf_downweight_factor({"status": "stable", "stale": False}, name)
        assert f == OKF_RESERVED_WEIGHT

    def test_deprecated_and_stale_compose(self) -> None:
        # A note that is both sinks below either alone.
        f = okf_downweight_factor({"status": "deprecated", "stale": True}, "n.md")
        assert f == OKF_DEPRECATED_WEIGHT * OKF_STALE_WEIGHT

    def test_ordering_deprecated_below_stale_below_normal(self) -> None:
        normal = okf_downweight_factor({"status": "stable", "stale": False}, "n.md")
        stale = okf_downweight_factor({"status": "stable", "stale": True}, "n.md")
        deprecated = okf_downweight_factor(
            {"status": "deprecated", "stale": False}, "n.md"
        )
        assert deprecated < stale < normal


# ---------------------------------------------------------------------------
# Pure re-ranker
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    """Stand-in for FTSResult / _SemanticRow / _GroupableFTS (path + score)."""

    path: str
    score: float


def _factor_map(mapping: dict[str, float]) -> object:
    return lambda path: mapping.get(path, 1.0)


class TestApplyOkfDownweight:
    def test_all_unity_is_order_preserving(self) -> None:
        rows = [_Row("a.md", 1.0), _Row("b.md", 0.5)]
        out = _apply_okf_downweight(rows, factor_for=lambda _p: 1.0)
        assert [(r.path, r.score) for r in out] == [("a.md", 1.0), ("b.md", 0.5)]

    def test_scales_and_resorts(self) -> None:
        rows = [_Row("dep.md", 1.0), _Row("norm.md", 0.9)]
        out = _apply_okf_downweight(rows, factor_for=_factor_map({"dep.md": 0.5}))
        # dep.md 1.0*0.5=0.5 drops below norm.md 0.9.
        assert [r.path for r in out] == ["norm.md", "dep.md"]
        assert out[0].score == 0.9
        assert out[1].score == 0.5

    def test_only_positive_scores_scaled(self) -> None:
        rows = [_Row("neg.md", -0.3)]
        out = _apply_okf_downweight(rows, factor_for=lambda _p: 0.5)
        # A negative score is left untouched so a demoting factor can't promote it.
        assert out[0].score == -0.3

    def test_input_not_mutated(self) -> None:
        rows = [_Row("x.md", 1.0)]
        _apply_okf_downweight(rows, factor_for=lambda _p: 0.5)
        assert rows[0].score == 1.0

    def test_empty_rows(self) -> None:
        assert _apply_okf_downweight([], factor_for=lambda _p: 0.5) == []


# ---------------------------------------------------------------------------
# SearchManager._rank_okf — gating and per-path memoisation
# ---------------------------------------------------------------------------


def _search_manager(tmp_path: Path, *, mode: str | None):
    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.okf import OkfDetector

    detector = OkfDetector(tmp_path, mode=mode) if mode is not None else None
    return SearchManager(FTSIndex(db_path=":memory:"), tmp_path, okf_detector=detector)


class TestRankOkfMethod:
    def test_inert_without_detector(self, tmp_path: Path) -> None:
        mgr = _search_manager(tmp_path, mode=None)
        rows = [_Row("a.md", 1.0), _Row("b.md", 2.0)]
        # No detector → the same list object is returned untouched (no re-sort).
        assert mgr._rank_okf(rows) is rows

    def test_inert_when_detector_inactive(self, tmp_path: Path) -> None:
        mgr = _search_manager(tmp_path, mode="off")
        rows = [_Row("a.md", 1.0)]
        assert mgr._rank_okf(rows) is rows

    def test_active_downweights_and_memoises_per_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _search_manager(tmp_path, mode="on")
        calls: list[str] = []

        def fake_frontmatter(path: str) -> dict[str, object]:
            calls.append(path)
            return {"status": "deprecated"} if path == "dep.md" else {}

        monkeypatch.setattr(mgr, "_get_frontmatter", fake_frontmatter)
        # 'dep.md' repeats: the second occurrence must hit the memo cache.
        rows = [_Row("dep.md", 1.0), _Row("norm.md", 0.9), _Row("dep.md", 0.8)]
        out = mgr._rank_okf(rows)
        # One frontmatter lookup per distinct path (memoised).
        assert calls.count("dep.md") == 1
        scored = {(r.path, round(r.score, 4)) for r in out}
        assert scored == {("dep.md", 0.5), ("norm.md", 0.9), ("dep.md", 0.4)}
        # Re-sorted descending: norm (0.9) leads, then the two dep rows.
        assert out[0].path == "norm.md"


# ---------------------------------------------------------------------------
# End to end — ranking corpus across all three modes
# ---------------------------------------------------------------------------

# Same body for the three real notes so their base relevance is identical and
# the downweight factors are the only differentiator.
_BODY = "# {t}\n\nZebra migration checklist steps and further zebra details here.\n"


def _note(title: str, extra: str = "") -> str:
    return f"---\ntitle: {title}\n{extra}---\n" + _BODY.format(t=title)


def _build_ranking_vault(root: Path, *, declared: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root_index = '---\nokf_version: "0.2"\ntitle: Root\n---\n' if declared else ""
    (root / "index.md").write_text(
        root_index + "# Root\n\nZebra navigation index here.\n", encoding="utf-8"
    )
    (root / "log.md").write_text(
        "# Log\n\n## 2026-01-01\n\n- Zebra change recorded here.\n", encoding="utf-8"
    )
    (root / "normal.md").write_text(_note("Normal"), encoding="utf-8")
    (root / "stale.md").write_text(
        _note("Stale", "stale_after: 2000-01-01\n"), encoding="utf-8"
    )
    (root / "deprecated.md").write_text(
        _note("Deprecated", "status: deprecated\n"), encoding="utf-8"
    )


def _vault_with_embeddings(root: Path) -> Vault:
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    vault = Vault(
        source_dir=root,
        okf_mode="auto",
        embeddings_path=root / "embeddings",
        embedding_provider=MockEmbeddingProvider(),
    )
    vault.index.build_index()
    return vault


@pytest.fixture
def declared_vault(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "declared"
    _build_ranking_vault(root, declared=True)
    vault = _vault_with_embeddings(root)
    try:
        yield vault
    finally:
        vault.close()


def _scores(vault: Vault, mode: str) -> dict[str, float]:
    return {r.path: r.score for r in vault.reader.search("zebra", mode=mode, limit=10)}


# The keyword and hybrid channels both yield usable positive scores for the
# deterministic mock provider; the semantic channel returns no hits for a
# hash-vector mock (cosines fall below the retrieval floor), so it is not
# exercised here — all three channels call the identical ``_rank_okf`` step, so
# the pure re-ranker tests above plus these two live channels cover the path.
_LIVE_MODES = ["keyword", "hybrid"]


class TestRankingCorpus:
    @pytest.mark.parametrize("mode", _LIVE_MODES)
    def test_deprecated_below_stale_below_normal(
        self, declared_vault: Vault, mode: str
    ) -> None:
        s = _scores(declared_vault, mode)
        assert {"normal.md", "stale.md", "deprecated.md"} <= s.keys()
        assert s["deprecated.md"] < s["stale.md"] < s["normal.md"]

    @pytest.mark.parametrize("mode", _LIVE_MODES)
    def test_reserved_files_demoted_below_real_notes(
        self, declared_vault: Vault, mode: str
    ) -> None:
        s = _scores(declared_vault, mode)
        real_min = min(s["normal.md"], s["stale.md"], s["deprecated.md"])
        for reserved in ("index.md", "log.md"):
            assert s[reserved] < real_min

    def test_keyword_factor_ratios_are_exact(self, declared_vault: Vault) -> None:
        # Identical-body notes → identical base score, so the factors show up
        # as exact ratios off the normal note.
        s = _scores(declared_vault, "keyword")
        assert s["stale.md"] == pytest.approx(s["normal.md"] * OKF_STALE_WEIGHT)
        assert s["deprecated.md"] == pytest.approx(
            s["normal.md"] * OKF_DEPRECATED_WEIGHT
        )


class TestByteIdenticalWhenNotDetected:
    def test_undeclared_vault_ranking_is_unaffected(self, tmp_path: Path) -> None:
        root = tmp_path / "plain"
        _build_ranking_vault(root, declared=False)
        vault = _vault_with_embeddings(root)
        try:
            s = _scores(vault, "keyword")
            # No detection → no downweight: the three identical-body notes keep
            # equal scores (deprecated/stale are not demoted).
            assert s["normal.md"] == pytest.approx(s["stale.md"])
            assert s["normal.md"] == pytest.approx(s["deprecated.md"])
        finally:
            vault.close()

    def test_mode_off_disables_downweight_on_declared_vault(
        self, tmp_path: Path
    ) -> None:
        # Even with the declaration present, OKF_MODE=off keeps ranking
        # byte-identical (the detector never reports active).
        root = tmp_path / "declared_off"
        _build_ranking_vault(root, declared=True)
        from markdown_vault_mcp.vault import Vault

        vault = Vault(source_dir=root, okf_mode="off")
        vault.index.build_index()
        try:
            s = _scores(vault, "keyword")
            assert s["normal.md"] == pytest.approx(s["stale.md"])
            assert s["normal.md"] == pytest.approx(s["deprecated.md"])
        finally:
            vault.close()
