"""Tests for the search/index storage seam (#1230).

These pin two claims the annotations make.  That ``FTSIndex`` and
``VectorIndex`` really satisfy the protocols the managers are now typed
against — mypy checks this statically at the composition root, but a runtime
check catches a drift a `type: ignore` could hide.  And that the split is
real: satisfying the keyword facet does not imply satisfying the graph facet,
which is what makes a backend that separates them expressible.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.interfaces import (
    GraphStore,
    KeywordGraphIndex,
    KeywordIndex,
    VectorStore,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fts(tmp_path: Path) -> Iterator[FTSIndex]:
    """A real on-disk index, closed afterwards."""
    index = FTSIndex(db_path=str(tmp_path / "index.db"))
    yield index
    index.close()


class TestFTSIndexConformance:
    """``FTSIndex`` serves both the keyword and graph facets."""

    @pytest.mark.parametrize(
        "protocol",
        [KeywordIndex, GraphStore, KeywordGraphIndex],
        ids=["keyword", "graph", "combined"],
    )
    def test_satisfies_facet(self, fts: FTSIndex, protocol: type) -> None:
        """One SQLite backend answers both concepts today."""
        assert isinstance(fts, protocol)


class TestVectorIndexConformance:
    """``VectorIndex`` serves the semantic facet."""

    def test_satisfies_vector_store(self) -> None:
        """The numpy implementation matches the seam the managers hold."""
        from markdown_vault_mcp.vector_index import VectorIndex

        class _StubProvider:
            dimension = 3
            provider_name = "stub"
            model_name = "stub"
            context_length = 128

            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.0, 0.0, 0.0] for _ in texts]

        assert isinstance(VectorIndex(_StubProvider()), VectorStore)  # type: ignore[arg-type]


class _GraphOnlyStore:
    """A backend serving only the link graph — no search, no enumeration.

    Exists to show the two concepts really are separable: this is the shape a
    future deployment would have if the graph moved to its own store
    alongside a different search index.
    """

    def get_backlinks(self, *a: Any, **k: Any) -> list[Any]: ...
    def get_outlinks(self, *a: Any, **k: Any) -> list[Any]: ...
    def get_broken_links(self, *a: Any, **k: Any) -> list[Any]: ...
    def get_orphan_notes(self, *a: Any, **k: Any) -> list[Any]: ...
    def get_most_linked(self, *a: Any, **k: Any) -> list[Any]: ...
    def get_connection_path(self, *a: Any, **k: Any) -> list[str] | None: ...
    def count_links(self) -> int: ...
    def count_broken_links(self) -> int: ...
    def count_orphans(self) -> int: ...
    def resolve_vault_wikilinks(self) -> int: ...


class TestFacetsAreIndependent:
    """The protocols separate concepts, not just names."""

    def test_graph_only_backend_is_not_a_keyword_index(self) -> None:
        """A store that answers structure need not answer relevance.

        This is the point of splitting the two: it makes a backend that
        serves the link graph alongside a different search index expressible.
        """
        store = _GraphOnlyStore()
        assert isinstance(store, GraphStore)
        assert not isinstance(store, KeywordIndex)
        assert not isinstance(store, KeywordGraphIndex)

    def test_unrelated_object_satisfies_nothing(self) -> None:
        """The runtime checks are real checks."""
        assert not isinstance(object(), KeywordIndex)
        assert not isinstance(object(), GraphStore)
        assert not isinstance(object(), VectorStore)


class TestManagersAcceptAnyConformingStore:
    """The managers depend on the seam, not on ``FTSIndex``."""

    def test_link_manager_accepts_a_graph_only_store(self, tmp_path: Path) -> None:
        """``LinkManager`` needs the graph facet plus one relational read.

        Constructing it against a hand-rolled store — never an ``FTSIndex`` —
        is what shows the dependency is on the protocol.
        """
        from markdown_vault_mcp.managers.link import LinkManager

        row = {
            "source_path": "other.md",
            "source_title": "Other",
            "link_text": "Seed",
            "link_type": "markdown",
            "fragment": None,
            "raw_target": "seed.md",
        }

        class _Store:
            def get_backlinks(
                self, path: str, *, limit: int | None = None
            ) -> list[dict[str, Any]]:
                return ([row] if path == "seed.md" else [])[:limit]

            def get_note(self, path: str) -> dict[str, Any] | None:
                return {"path": path, "title": "Seed"}

            def __getattr__(self, name: str) -> Any:
                # The remaining facet members are unused by this test.
                raise AttributeError(name)

        mgr = LinkManager(_Store(), tmp_path)  # type: ignore[arg-type]

        backlinks = mgr.get_backlinks("seed.md")

        assert [b.source_path for b in backlinks] == ["other.md"]


def test_interfaces_module_imports_nothing_at_runtime() -> None:
    """The seam stays dependency-free, so depending on it pulls in no backend."""
    source = (
        Path(__file__).parent.parent / "src" / "markdown_vault_mcp" / "interfaces.py"
    ).read_text()
    runtime_imports = [
        line
        for line in source.splitlines()
        if re.match(r"^(import|from)\s", line)
        if "__future__" not in line and "typing" not in line
    ]
    assert runtime_imports == [], (
        f"interfaces.py gained runtime imports: {runtime_imports}"
    )
