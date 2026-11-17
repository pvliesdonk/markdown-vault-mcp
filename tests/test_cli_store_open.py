"""Owned-store commands report typed open failures at the CLI boundary."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import markdown_vault_mcp.cli as cli
from markdown_vault_mcp.exceptions import StoreUnavailableError

runner = CliRunner()


def _unavailable() -> StoreUnavailableError:
    return StoreUnavailableError("store unavailable", backend="sqlite", location="test")


@pytest.mark.parametrize("args", [["admin", "index"], ["reindex"]])
def test_real_index_parent_is_a_file(tmp_path: Path, args: list[str]) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    parent = tmp_path / "file"
    parent.write_text("not a directory")
    result = runner.invoke(
        cli.app,
        [*args, "--source-dir", str(vault), "--index-path", str(parent / "index.db")],
    )
    assert result.exit_code == 1
    assert "ERROR:" in result.stderr
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "args",
    [
        ["admin", "index"],
        ["admin", "reindex"],
        ["admin", "embeddings", "import", "--from", "numpy"],
        ["embeddings", "import", "--from", "numpy"],
    ],
)
def test_open_failure_at_construction(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise _unavailable()

    monkeypatch.setattr(cli, "_build_vault", fail)
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1
    assert "ERROR: store unavailable" in result.stderr
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("args", [["admin", "index"], ["admin", "reindex"]])
def test_open_failure_during_index_operation(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    built = MagicMock()
    built.vault.index.build_index.side_effect = _unavailable()
    monkeypatch.setattr(cli, "_build_vault", lambda *_a, **_kw: built)
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1
    assert "ERROR: store unavailable" in result.stderr
    assert isinstance(result.exception, SystemExit)
    built.close.assert_called_once()


def test_unexpected_exception_remains_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "_build_vault", fail)
    result = runner.invoke(cli.app, ["admin", "index"])
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "unexpected"
