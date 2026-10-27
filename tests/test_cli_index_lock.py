"""The CLI's owning commands stop before writing an index a server owns (#1890)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import _ENV_PREFIX, app
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.keyword_index_holder import Holder
from tests.fixtures.sidecar_v5_1.notes import NOTES

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "sidecar_v5_1"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault of the fixture notes and an on-disk index path."""
    import markdown_vault_mcp.providers as providers_mod

    monkeypatch.setattr(
        providers_mod, "get_embedding_provider", lambda _c: MockEmbeddingProvider()
    )
    root = tmp_path / "vault"
    for name, text in NOTES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(root))
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDINGS_PATH", raising=False)
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDING_STORE", raising=False)
    return tmp_path


@pytest.fixture
def server(env: Path) -> Iterator[Callable[[], Holder]]:
    """Start another process owning ``index.db``, as a running server would."""
    started: list[Holder] = []

    def start() -> Holder:
        started.append(Holder(env / "index.db"))
        return started[-1]

    try:
        yield start
    finally:
        for held in started:
            held.stop()


def _sums(root: Path) -> dict[str, str]:
    # The shared-memory index is SQLite's scratch, rewritten by any reader.
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith("-shm")
    }


def _embeddings_on(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_PROVIDER", "fastembed")


@pytest.mark.parametrize("embeddings", [False, True], ids=["keyword", "embeddings"])
@pytest.mark.parametrize(
    "args", [["index"], ["index", "--force"], ["reindex"]], ids=" ".join
)
def test_index_refuses_while_a_server_owns_the_index(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    server: Callable[[], Holder],
    args: list[str],
    embeddings: bool,
) -> None:
    _embeddings_on(monkeypatch, embeddings)
    assert runner.invoke(app, ["index"]).exit_code == 0
    (env / "vault" / "alpha.md").write_text(NOTES["alpha.md"] + "\nEdited.\n")
    server()
    before = _sums(env)
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert (
        "is being written by another process. Stop the server first, "
        f"then run {args[0]} again." in result.stderr
    )
    assert "Traceback" not in result.output
    assert _sums(env) == before


def test_embeddings_import_refuses_while_a_server_owns_the_index(
    env: Path, monkeypatch: pytest.MonkeyPatch, server: Callable[[], Holder]
) -> None:
    _embeddings_on(monkeypatch, True)
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_STORE", "sqlite")
    assert runner.invoke(app, ["index"]).exit_code == 0
    for suffix in (".json", ".npy"):
        shutil.copy(FIXTURE / f"index-db-embeddings{suffix}", env / f"released{suffix}")
    server()
    before = _sums(env)
    result = runner.invoke(
        app,
        [
            "embeddings",
            "import",
            "--from",
            "numpy",
            "--from-path",
            str(env / "released"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "Stop the server first, then run embeddings import again." in result.stderr
    assert _sums(env) == before


def test_search_reads_while_a_server_owns_the_index(
    server: Callable[[], Holder],
) -> None:
    assert runner.invoke(app, ["index"]).exit_code == 0
    server()
    result = runner.invoke(app, ["search", "alpha"])
    assert result.exit_code == 0, result.output
    assert "alpha.md" in result.stdout


def test_index_runs_once_the_server_stops(server: Callable[[], Holder]) -> None:
    held = server()
    assert runner.invoke(app, ["index"]).exit_code == 1
    held.stop()
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 0, result.output
