"""An upgrade from 5.x reuses its vectors through a donor, in either built-in store (#1505)."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.sidecar_v5_1.notes import NOTES

FIXTURE = Path(__file__).parent / "fixtures" / "sidecar_v5_1"
STORES = ["numpy", "sqlite"]


class _Counting(MockEmbeddingProvider):
    """The fixture's provider (``mock/mock-dim-32``), counting embedded texts."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _released(tmp_path: Path) -> Path:
    """Lay out a v5.1 install: the notes, and the sidecar beside ``index.db``."""
    root = tmp_path / "vault"
    for name, text in NOTES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    for suffix in (".json", ".npy"):
        shutil.copy(
            FIXTURE / f"index-db-embeddings{suffix}",
            tmp_path / f"index-db-embeddings{suffix}",
        )
    return root


def _sums(tmp_path: Path) -> dict[str, str]:
    return {
        s: hashlib.sha256(
            (tmp_path / f"index-db-embeddings{s}").read_bytes()
        ).hexdigest()
        for s in (".json", ".npy")
    }


def _vault(tmp_path: Path, root: Path, store: str, provider: Any) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store=store
        ),
        embedding_provider=provider,
    )


def _upgrade(tmp_path: Path, root: Path, store: str, provider: Any) -> dict[str, Any]:
    vault = _vault(tmp_path, root, store, provider)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        return vault.index.embeddings_status()
    finally:
        vault.close()


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[Any]:
    return [r.args for r in caplog.records if r.msg.startswith(f"{name} ")]


@pytest.mark.parametrize("store", STORES)
def test_a_v5_1_vault_upgrades_for_free(tmp_path: Path, store: str) -> None:
    root = _released(tmp_path)
    expected = _sums(tmp_path)
    provider = _Counting()
    status = _upgrade(tmp_path, root, store, provider)
    assert provider.texts == []
    rows = len(json.loads((FIXTURE / "index-db-embeddings.json").read_text())["rows"])
    assert (status["backend"], status["chunk_count"]) == (store, rows)
    assert _sums(tmp_path) == expected


@pytest.mark.parametrize("store", STORES)
def test_the_upgrade_is_logged_once(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _upgrade(tmp_path, root, store, _Counting())
    base = str(tmp_path / "index-db-embeddings")
    assert _events(caplog, "embeddings_upgrade_started") == [(f"numpy:{base}", store)]
    (used,) = _events(caplog, "embeddings_donor_used")
    assert used[0] == f"numpy:{base}"
    assert used[2] == 0  # embedded nothing
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _upgrade(
            tmp_path, root, store, _Counting()
        )  # a second start: nothing to upgrade
    assert _events(caplog, "embeddings_upgrade_started") == []


@pytest.mark.parametrize("store", STORES)
def test_an_interrupted_upgrade_retries(
    tmp_path: Path, store: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.indexing import embedding_reconciler

    root = _released(tmp_path)
    expected = _sums(tmp_path)
    real = embedding_reconciler.EmbeddingReconciler.rebuild

    def dies(self: Any, store_: Any, identity: Any, **kw: Any) -> Any:
        store_.publish_generation = _crash  # the process dies at publication
        return real(self, store_, identity, **kw)

    def _crash(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the process dies at publication")

    monkeypatch.setattr(embedding_reconciler.EmbeddingReconciler, "rebuild", dies)
    vault = _vault(tmp_path, root, store, _Counting())
    try:
        vault.index.build_index()
        with pytest.raises(RuntimeError, match="dies at publication"):
            vault.index.build_embeddings()
    finally:
        vault.close()
    monkeypatch.undo()
    assert _sums(tmp_path) == expected
    provider = _Counting()
    status = _upgrade(tmp_path, root, store, provider)
    assert provider.texts == []
    assert status["chunk_count"] > 0


@pytest.mark.parametrize("store", STORES)
def test_a_bare_list_is_never_adopted(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    rows = json.loads((tmp_path / "index-db-embeddings.json").read_text())["rows"]
    (tmp_path / "index-db-embeddings.json").write_text(json.dumps(rows))
    expected = _sums(tmp_path)
    provider = _Counting()
    with caplog.at_level(logging.INFO):
        status = _upgrade(tmp_path, root, store, provider)
    assert [a[1] for a in _events(caplog, "embeddings_donor_refused")] == [
        "identity_unknown"
    ]
    assert len(provider.texts) == status["chunk_count"] > 0
    assert _sums(tmp_path) == expected


@pytest.mark.parametrize("store", STORES)
def test_a_released_sidecar_of_another_model_is_refused_and_left_alone(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    expected = _sums(tmp_path)
    provider = MockEmbeddingProvider(dim=8)  # another model: mock-dim-8
    with caplog.at_level(logging.INFO):
        _upgrade(tmp_path, root, store, provider)
    assert [a[1] for a in _events(caplog, "embeddings_donor_refused")] == [
        "identity_differs"
    ]
    assert _sums(tmp_path) == expected


def test_a_numpy_v2_store_is_not_imported_into_sqlite(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    _upgrade(tmp_path, root, "numpy", _Counting())  # writes index-db-embeddings.v2.*
    for suffix in (".json", ".npy"):
        (tmp_path / f"index-db-embeddings{suffix}").unlink()
    provider = _Counting()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _upgrade(tmp_path, root, "sqlite", provider)
    assert _events(caplog, "embeddings_upgrade_started") == []
    assert len(provider.texts) == status["chunk_count"] > 0


@pytest.mark.parametrize("store", STORES)
def test_a_forced_rebuild_does_not_upgrade(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    provider = _Counting()
    vault = _vault(tmp_path, root, store, provider)
    try:
        vault.index.build_index()
        caplog.clear()
        with caplog.at_level(logging.INFO):
            vault.index.build_embeddings(force=True)
    finally:
        vault.close()
    assert _events(caplog, "embeddings_upgrade_started") == []
    assert provider.texts


def test_a_writer_never_reads_the_released_sidecar_in_place(tmp_path: Path) -> None:
    from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
    from markdown_vault_mcp.types import EmbeddingIdentity

    _released(tmp_path)
    identity = EmbeddingIdentity.of(_Counting(), "v1")
    base = tmp_path / "index-db-embeddings"
    assert NumpySidecarBackend().open(base, identity).count == 0
    legacy = NumpySidecarBackend(legacy_only=True).open(base, identity)
    assert legacy.count > 0
    assert np.isfinite(np.asarray(legacy.rows_for("alpha.md")[0].vector)).all()
