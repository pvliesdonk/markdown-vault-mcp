"""A failed automatic embedding transition is never an empty search (#1946)."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vector_store_files import stored_vector_files


class _DocumentsDown(MockEmbeddingProvider):
    """Queries work, but every document embedding batch fails."""

    documents_down = True
    document_calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        if self.documents_down:
            raise RuntimeError("synthetic document embedding failure")
        return super().embed(texts)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return MockEmbeddingProvider.embed(self, texts)


def _vault(root: Path, backend: str, provider: MockEmbeddingProvider) -> Vault:
    source = root / "vault"
    if not source.exists():
        source.mkdir()
        (source / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    return Vault(
        source_dir=source,
        settings=VaultSettings(
            index_path=root / "index.db",
            embeddings_path=root / "vectors",
            embedding_store=backend,
            read_only=False,
        ),
        embedding_provider=provider,
    )


def _assert_failed_transition(
    root: Path,
    backend: str,
    mode: Literal["semantic", "hybrid"],
    providers: tuple[MockEmbeddingProvider, _DocumentsDown],
    recovery: Literal["read", "build", "reopen", "force"] = "read",
) -> None:
    previous, current = providers
    first = _vault(root, backend, previous)
    try:
        first.index.build_index()
        assert first.index.build_embeddings() == 1
    finally:
        first.close()
    before = stored_vector_files(root)
    second = _vault(root, backend, current)
    try:
        second.index.build_index()
        for _ in range(2):
            if recovery == "force":
                with pytest.raises(RuntimeError, match=r"(?i)fail|rebuild"):
                    second.index.build_embeddings(force=True)
            with pytest.raises(RuntimeError, match=r"(?i)fail|rebuild"):
                second.reader.search("Apples and pears.", mode=mode)
        failed_calls = 4 if recovery == "force" else 2
        assert current.document_calls == failed_calls
        assert stored_vector_files(root) == before
        # The preserved vectors still work with their original embedding model.
        original = _vault(root, backend, previous)
        try:
            original.index.build_index()
            results = original.reader.search("Apples and pears.", mode="semantic")
            assert [result.path for result in results] == ["alpha.md"]
        finally:
            original.close()
        current.documents_down = False
        if recovery in ("build", "force"):
            second.index.build_embeddings(force=recovery == "force")
        elif recovery == "reopen":
            second.close()
            second = _vault(root, backend, current)
            second.index.build_index()
        results = second.reader.search("Apples and pears.", mode=mode)
        assert [result.path for result in results] == ["alpha.md"]
        assert (
            second.index.embeddings_status()["identity"]["dimension"]
            == current.dimension
        )
        assert second.reader.search("Apples and pears.", mode=mode)
        assert current.document_calls == failed_calls + 1
    finally:
        second.close()


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
@pytest.mark.parametrize("same_model", [False, True], ids=["model", "dimension"])
@pytest.mark.parametrize("recovery", ["read", "build", "reopen", "force"])
@settings(max_examples=16, deadline=None)
@given(
    dimensions=st.tuples(
        st.integers(min_value=2, max_value=32), st.integers(min_value=1, max_value=16)
    )
)
@example(dimensions=(16, 16))
def test_failed_transition_refuses_repeated_reads(
    backend: str,
    mode: Literal["semantic", "hybrid"],
    same_model: bool,
    recovery: Literal["read", "build", "reopen", "force"],
    dimensions: tuple[int, int],
) -> None:
    old_dimension, increase = dimensions
    model = "fixed-model" if same_model else None
    with TemporaryDirectory(prefix="failed-transition-") as directory:
        _assert_failed_transition(
            Path(directory),
            backend,
            mode,
            (
                MockEmbeddingProvider(dim=old_dimension, model=model),
                _DocumentsDown(dim=old_dimension + increase, model=model),
            ),
            recovery,
        )


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
@pytest.mark.parametrize("authoritative", [False, True])
@pytest.mark.parametrize(
    "query",
    [("semantic", False), ("semantic", True), ("hybrid", False), ("hybrid", True)],
)
def test_an_empty_model_transition_requires_an_authoritative_index(
    tmp_path: Path,
    backend: str,
    query: tuple[Literal["semantic", "hybrid"], bool],
    authoritative: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mode, force = query
    (tmp_path / "vault").mkdir()
    first = _vault(tmp_path, backend, MockEmbeddingProvider(dim=16))
    try:
        first.index.build_index()
        assert first.index.build_embeddings() == 0
    finally:
        first.close()
    second = _vault(tmp_path, backend, _DocumentsDown(dim=32))
    try:
        second.index.build_index()
        if not authoritative:
            monkeypatch.setattr(second._fts, "is_build_walk_complete", lambda: False)
        for _ in range(2):
            if authoritative:
                if force:
                    assert second.index.build_embeddings(force=True) == 0
                assert second.reader.search("Apples", mode=mode) == []
            else:
                if force:
                    with pytest.raises(RuntimeError, match=r"(?i)fail|rebuild"):
                        second.index.build_embeddings(force=True)
                with pytest.raises(RuntimeError, match=r"(?i)fail|rebuild"):
                    second.reader.search("Apples", mode=mode)
        if authoritative:
            assert second.index.embeddings_status()["generation"] == (2 if force else 1)
    finally:
        second.close()


@pytest.mark.parametrize("backend", ["numpy", "sqlite"])
def test_force_without_previous_vectors_keeps_the_cold_failure_contract(
    tmp_path: Path, backend: str
) -> None:
    vault = _vault(tmp_path, backend, _DocumentsDown(dim=32))
    try:
        vault.index.build_index()
        assert vault.index.build_embeddings(force=True) == 0
        assert vault.reader.search("Apples", mode="semantic") == []
    finally:
        vault.close()
