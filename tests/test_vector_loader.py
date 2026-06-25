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
    # Pin the warning AND that it carries a traceback (exc_info) for the
    # destructive rebuild (#735).
    assert any(
        r.name == _LOGGER_NAME
        and "Rebuilding embeddings" in r.getMessage()
        and r.exc_info is not None
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
    # Pins the event name, that the PASSED logger emitted it (#736), AND that
    # the warning carries a traceback (exc_info) for the destructive rebuild
    # (#735).
    assert any(
        r.name == _LOGGER_NAME
        and "vector_index_corrupt_rebuilding" in r.getMessage()
        and r.exc_info is not None
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


def test_empty_rebuild_is_accepted_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild that populates an EMPTY index returns it without raising.

    Respects #649 graceful degradation: a provider-down rebuild yields an
    empty (but populated) slot, which build_embeddings already warns about;
    load_or_self_heal must not turn that into a hard failure.
    """
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCorruptError("row count mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()
    empty = VectorIndex(provider)  # count == 0

    result = load_or_self_heal(
        embeddings_path=base,
        embedding_provider=provider,
        get_vectors=get,
        set_vectors=set_,
        rebuild=lambda: set_(empty),
        logger=_LOG,
    )
    assert result is empty
    assert result.count == 0


def test_tail_guard_uses_scenario_neutral_message(tmp_path: Path) -> None:
    """The cold-build tail guard must not misattribute to 'compatibility error'.

    A no-op set_vectors leaves the slot None after the cold-build branch, so
    the trailing guard fires. Its message must be scenario-neutral.
    """
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"  # no .npy on disk -> cold-build path

    with pytest.raises(ValueError, match="slot empty after load/rebuild"):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=lambda: None,  # slot never populated
            set_vectors=lambda _v: None,  # no-op: drops the cold-build index
            rebuild=lambda: None,
            logger=_LOG,
        )


def test_rebuild_that_raises_is_logged_then_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rebuild callback that itself raises logs vector_index_rebuild_failed
    and propagates the original exception (does not swallow it)."""
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCorruptError("row count mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()

    def rebuild_explodes() -> None:
        raise RuntimeError("provider down")

    with (
        caplog.at_level(logging.ERROR, logger=_LOGGER_NAME),
        pytest.raises(RuntimeError, match="provider down"),
    ):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=rebuild_explodes,
            logger=_LOG,
        )
    assert any(
        r.name == _LOGGER_NAME and "vector_index_rebuild_failed" in r.getMessage()
        for r in caplog.records
    )


def test_compat_rebuild_that_raises_is_logged_then_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising rebuild on the compatibility arm is also logged + propagated."""
    provider = MockEmbeddingProvider()
    base = tmp_path / "embeddings"
    (tmp_path / "embeddings.npy").touch()

    def boom(*_a: object, **_k: object) -> VectorIndex:
        raise VectorIndexCompatibilityError("mismatch")

    monkeypatch.setattr(VectorIndex, "load", boom)
    _box, get, set_ = _slot()

    def rebuild_explodes() -> None:
        raise RuntimeError("provider down")

    with (
        caplog.at_level(logging.ERROR, logger=_LOGGER_NAME),
        pytest.raises(RuntimeError, match="provider down"),
    ):
        load_or_self_heal(
            embeddings_path=base,
            embedding_provider=provider,
            get_vectors=get,
            set_vectors=set_,
            rebuild=rebuild_explodes,
            logger=_LOG,
        )
    assert any(
        r.name == _LOGGER_NAME and "vector_index_rebuild_failed" in r.getMessage()
        for r in caplog.records
    )
