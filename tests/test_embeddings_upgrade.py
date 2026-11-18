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

    def __init__(self, dim: int = 32) -> None:
        super().__init__(dim)
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


def _vault(
    tmp_path: Path, root: Path, store: str, provider: Any, **settings: Any
) -> Vault:
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            read_only=False,
            embedding_store=store,
            **settings,
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


def test_a_v5_1_install_upgrades_onto_the_default_store_for_free(
    tmp_path: Path,
) -> None:
    """A 5.1 install that sets no store lands in streamed SQLite, paying nothing (#1900)."""
    root = _released(tmp_path)
    expected = _sums(tmp_path)
    provider = _Counting()
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
        embedding_provider=provider,
    )
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        status = vault.index.embeddings_status()
    finally:
        vault.close()
    assert provider.texts == []
    rows = len(json.loads((FIXTURE / "index-db-embeddings.json").read_text())["rows"])
    assert (status["backend"], status["search_mode"], status["chunk_count"]) == (
        "sqlite",
        "streamed",
        rows,
    )
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
    probe = _vault(tmp_path, root, store, _Counting())
    try:  # nothing reached the new store before publication
        assert probe.index.embeddings_status()["chunk_count"] == 0
    finally:
        probe.close()
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
    provider = _Counting(dim=8)  # another model: mock-dim-8
    with caplog.at_level(logging.INFO):
        status = _upgrade(tmp_path, root, store, provider)
    assert [a[1] for a in _events(caplog, "embeddings_donor_refused")] == [
        "identity_differs"
    ]
    assert len(provider.texts) == status["chunk_count"] > 0
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


@pytest.mark.parametrize("store", STORES)
def test_a_boot_reindex_that_purges_still_leaves_the_upgrade_to_the_build(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    keyword = _vault(tmp_path, root, store, _Counting())  # the 5.1 keyword index
    try:
        keyword.index.build_index()
    finally:
        keyword.close()
    (root / "alpha.md").write_text(NOTES["alpha.md"] + "\nEdited offline.\n")
    provider = _Counting()
    vault = _vault(tmp_path, root, store, provider, exclude_patterns=["sub/gamma.md"])
    try:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            vault.index.build_index()
            vault.index.reindex()  # purges sub/gamma.md, re-parses alpha.md
            vault.index.build_embeddings()
    finally:
        vault.close()
    assert len(_events(caplog, "embeddings_upgrade_started")) == 1
    assert len(provider.texts) == 1  # only the edited note


@pytest.mark.parametrize("bare_list", [False, True])
@pytest.mark.parametrize("store", STORES)
def test_an_empty_vault_opens_no_donor(
    tmp_path: Path, store: str, bare_list: bool, caplog: pytest.LogCaptureFixture
) -> None:
    root = _released(tmp_path)
    for name in NOTES:
        (root / name).unlink()
    if bare_list:
        sidecar = tmp_path / "index-db-embeddings.json"
        sidecar.write_text(json.dumps(json.loads(sidecar.read_text())["rows"]))
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _upgrade(tmp_path, root, store, _Counting())
    # Nothing to copy into, so the sidecar is not read at each start.
    assert _events(caplog, "embeddings_upgrade_started") == []
    assert _events(caplog, "embeddings_donor_refused") == []


def _edge_build(tmp_path: Path, root: Path) -> list[Path]:
    """Leave what an edge build wrote: a format-2 ``{base}.json`` naming ``{base}.m3.npy``."""
    _upgrade(tmp_path, root, "numpy", _Counting())
    base = tmp_path / "index-db-embeddings"
    for suffix in (".json", ".npy"):
        base.with_suffix(suffix).unlink()
    payload = json.loads(base.with_suffix(".v2.json").read_text())
    (matrix,) = tmp_path.glob("index-db-embeddings.v2.m*.npy")
    matrix.rename(base.with_suffix(".m3.npy"))
    meta = payload["index_metadata"]
    meta["matrix"], meta["matrix_seq"] = base.with_suffix(".m3.npy").name, 3
    base.with_suffix(".json").write_text(json.dumps(payload))
    base.with_suffix(".v2.json").unlink()
    return [base.with_suffix(".json"), base.with_suffix(".m3.npy")]


@pytest.mark.parametrize("store", STORES)
def test_an_edge_build_sidecar_upgrades_for_free(tmp_path: Path, store: str) -> None:
    root = _released(tmp_path)
    edge = _edge_build(tmp_path, root)
    expected = [hashlib.sha256(p.read_bytes()).hexdigest() for p in edge]
    provider = _Counting()
    status = _upgrade(tmp_path, root, store, provider)
    assert provider.texts == []
    assert (status["backend"], status["chunk_count"]) == (store, len(NOTES))
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in edge] == expected
