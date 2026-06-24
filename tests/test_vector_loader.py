from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.managers._vector_loader import load_or_self_heal
from markdown_vault_mcp.vector_index import (
    VectorIndex,
    VectorIndexCompatibilityError,
    VectorIndexCorruptError,
)
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_LOGGER_NAME = "test.vector_loader"
_LOG = logging.getLogger(_LOGGER_NAME)


def _slot() -> tuple[
    dict, Callable[[], VectorIndex | None], Callable[[VectorIndex], None]
]:
    """A dict-backed cache slot plus get/set accessors over it."""
    box: dict = {"v": None}
    return box, (lambda: box["v"]), (lambda v: box.__setitem__("v", v))


def _populated(provider: MockEmbeddingProvider) -> VectorIndex:
    vi = VectorIndex(provider)
    vi.add(["x"], [{"path": "x.md", "title": "X", "heading": None}])
    return vi


def test_cache_hit_returns_cached_without_rebuild(tmp_path: Path) -> None:
    provider = MockEmbeddingProvider()
    cached = _populated(provider)
    box, get, set_ = _slot()
    box["v"] = cached
    calls: list[int] = []
    result = load_or_self_heal(
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=provider,
        get_vectors=get,
        set_vectors=set_,
        rebuild=lambda: calls.append(1),
        logger=_LOG,
    )
    assert result is cached
    assert calls == []


def test_cold_build_when_no_sidecar(tmp_path: Path) -> None:
    provider = MockEmbeddingProvider()
    box, get, set_ = _slot()
    calls: list[int] = []
    result = load_or_self_heal(
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=provider,
        get_vectors=get,
        set_vectors=set_,
        rebuild=lambda: calls.append(1),
        logger=_LOG,
    )
    assert isinstance(result, VectorIndex)
    assert result.count == 0
    assert calls == []
    assert box["v"] is result


def test_clean_load_returns_loaded_index(tmp_path: Path) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    _populated(provider).save(base)
    box, get, set_ = _slot()
    calls: list[int] = []
    result = load_or_self_heal(
        embeddings_path=base,
        embedding_provider=provider,
        get_vectors=get,
        set_vectors=set_,
        rebuild=lambda: calls.append(1),
        logger=_LOG,
    )
    assert result.count == 1
    assert calls == []
    assert box["v"] is result  # set_vectors persisted the loaded index


def test_compatibility_error_triggers_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()  # satisfy the npy-exists gate

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCompatibilityError("mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    box, get, set_ = _slot()
    rebuilt = _populated(provider)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=lambda: box.__setitem__("v", rebuilt),
            logger=_LOG,
        )

    assert result is rebuilt
    assert any(
        r.name == _LOGGER_NAME and "Rebuilding embeddings" in r.getMessage()
        for r in caplog.records
    )


def test_corrupt_error_triggers_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCorruptError("row count mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    box, get, set_ = _slot()
    rebuilt = _populated(provider)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=lambda: box.__setitem__("v", rebuilt),
            logger=_LOG,
        )

    assert result is rebuilt
    # Pins both the event name AND that the PASSED logger emitted it (#736).
    assert any(
        r.name == _LOGGER_NAME and "vector_index_corrupt_rebuilding" in r.getMessage()
        for r in caplog.records
    )


def test_rebuild_failure_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCorruptError("row count mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()

    with pytest.raises(ValueError, match="corrupt sidecar"):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=lambda: None,  # no-op: slot stays None
            logger=_LOG,
        )


def test_compatibility_rebuild_failure_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCompatibilityError("mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()

    with pytest.raises(ValueError, match="compatibility error"):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=lambda: None,  # no-op: slot stays None
            logger=_LOG,
        )


def test_permission_error_propagates_without_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise PermissionError("denied")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()
    calls: list[int] = []

    with pytest.raises(PermissionError, match="denied"):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=lambda: calls.append(1),
            logger=_LOG,
        )
    assert calls == []
