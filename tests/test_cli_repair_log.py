"""``admin repair-log``: preview the stacked log frontmatter, then apply it (#1403)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import _ENV_PREFIX, app
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers import document as document_module
from tests.fixtures.keyword_index_holder import Holder

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import Result

runner = CliRunner()
UNIT = b"---\ntitle: Log\n---\n\n"
SUFFIX = b"# Log\n\n## 2026-09-07\n\n- **Update**: wrote `a.md`\n"
STACKED = UNIT * 3 + SUFFIX
BACKUP = "log.md.frontmatter-repair.bak"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault holding one stacked guides/log.md, with no index on disk."""
    root = tmp_path / "vault"
    (root / "guides").mkdir(parents=True)
    (root / "guides" / "log.md").write_bytes(STACKED)
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(root))
    for name in ("INDEX_PATH", "STATE_PATH", "EMBEDDING_PROVIDER", "READ_ONLY"):
        monkeypatch.delenv(f"{_ENV_PREFIX}_{name}", raising=False)
    return root


def _repair(*args: str) -> Result:
    return runner.invoke(app, ["admin", "repair-log", "guides/log.md", *args])


def test_preview_prints_the_removal_and_changes_nothing(vault: Path) -> None:
    result = _repair()
    assert result.exit_code == 0, result.output
    assert "2 repeated frontmatter blocks to remove, bytes 20 to 59" in result.stdout
    removal = result.stdout.split(">>> removal begins\n")[1].split("<<< removal")[0]
    assert removal == (UNIT * 2).decode()
    assert f"--apply {compute_etag(STACKED)}" in result.stdout
    assert (vault / "guides" / "log.md").read_bytes() == STACKED
    assert sorted(p.name for p in vault.rglob("*")) == ["guides", "log.md"]


def test_preview_without_a_candidate_says_so(vault: Path) -> None:
    (vault / "guides" / "log.md").write_bytes(UNIT + SUFFIX)
    result = _repair()
    assert result.exit_code == 0, result.output
    assert "Nothing to repair in guides/log.md." in result.stdout
    assert "--apply" not in result.stdout


@pytest.mark.parametrize("protected", ["guides/log.md", f"guides/{BACKUP}"])
def test_apply_with_the_previewed_etag_repairs_and_keeps_the_original(
    vault: Path, monkeypatch: pytest.MonkeyPatch, protected: str
) -> None:
    monkeypatch.setenv(f"{_ENV_PREFIX}_PROTECTED_PATHS", protected)
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 0, result.output
    assert "removed 2 blocks" in result.stdout
    assert f"guides/{BACKUP}" in result.stdout
    assert (vault / "guides" / "log.md").read_bytes() == UNIT + SUFFIX
    assert (vault / "guides" / BACKUP).read_bytes() == STACKED


def test_apply_after_the_log_changed_is_refused(vault: Path) -> None:
    reviewed = compute_etag(STACKED)
    (vault / "guides" / "log.md").write_bytes(STACKED + b"- new entry\n")
    result = _repair("--apply", reviewed)
    assert result.exit_code == 1
    assert "changed since the preview" in result.stderr
    assert (vault / "guides" / "log.md").read_bytes() == STACKED + b"- new entry\n"
    assert not (vault / "guides" / BACKUP).exists()


def test_apply_refuses_an_occupied_recovery_path(vault: Path) -> None:
    (vault / "guides" / BACKUP).write_bytes(b"earlier evidence")
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 1
    assert "already exists" in result.stderr
    assert (vault / "guides" / BACKUP).read_bytes() == b"earlier evidence"
    assert (vault / "guides" / "log.md").read_bytes() == STACKED


def test_apply_reports_an_interrupted_repair(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        document_module, "atomic_write", Mock(side_effect=OSError("disk full"))
    )
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 1
    assert "target may already have changed" in result.stderr
    assert "Cause: disk full" in result.stderr
    assert (vault / "guides" / BACKUP).read_bytes() == STACKED


def test_apply_on_a_read_only_vault_is_refused(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"{_ENV_PREFIX}_READ_ONLY", "true")
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 1
    assert "ERROR: Vault is read-only" in result.stderr
    assert (vault / "guides" / "log.md").read_bytes() == STACKED


def test_a_server_owning_the_index_blocks_apply_but_not_preview(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "index.db"
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(index))
    server = Holder(index)
    try:
        assert _repair().exit_code == 0
        result = _repair("--apply", compute_etag(STACKED))
    finally:
        server.stop()
    assert result.exit_code == 1
    assert "Stop the server first, then run admin repair-log again" in result.stderr
    assert (vault / "guides" / "log.md").read_bytes() == STACKED


def test_okf_upkeep_adds_nothing_to_the_repaired_log(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (vault / "index.md").write_text("---\nokf_version: 0.2\n---\n# Bundle\n")
    monkeypatch.setenv(f"{_ENV_PREFIX}_OKF_MODE", "on")
    monkeypatch.setenv(f"{_ENV_PREFIX}_OKF_WRITE", "true")
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 0, result.output
    assert (vault / "guides" / "log.md").read_bytes() == UNIT + SUFFIX


def test_a_git_vault_commits_the_repair_and_the_original(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (git_repo / "log.md").write_bytes(STACKED)
    subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "stacked"], check=True)
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(git_repo))
    for name in ("INDEX_PATH", "STATE_PATH", "READ_ONLY", "GIT_REPO_URL"):
        monkeypatch.delenv(f"{_ENV_PREFIX}_{name}", raising=False)
    result = runner.invoke(
        app, ["admin", "repair-log", "log.md", "--apply", compute_etag(STACKED)]
    )
    assert result.exit_code == 0, result.output
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""
    committed = subprocess.run(
        ["git", "-C", str(git_repo), "show", f"HEAD:{BACKUP}"],
        check=True,
        capture_output=True,
    ).stdout
    assert committed == STACKED


def test_preview_prints_a_command_that_runs_as_shown(vault: Path) -> None:
    folder = vault / "my notes"
    folder.mkdir()
    (folder / "log.md").write_bytes(STACKED)
    result = runner.invoke(
        app,
        ["admin", "repair-log", "my notes/log.md", "--source-dir", str(vault)],
    )
    assert result.exit_code == 0, result.output
    command = result.stdout.split("To remove it, run: ")[1].strip()
    assert command == (
        f"markdown-vault-mcp admin repair-log 'my notes/log.md' --source-dir "
        f"{vault} --apply {compute_etag(STACKED)}"
    )


@pytest.mark.usefixtures("vault")
def test_apply_beside_a_stopped_server_index_keeps_it_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "index.db"))
    assert runner.invoke(app, ["admin", "index"]).exit_code == 0
    result = _repair("--apply", compute_etag(STACKED))
    assert result.exit_code == 0, result.output
    reindexed = runner.invoke(app, ["admin", "reindex"])
    assert reindexed.exit_code == 0, reindexed.output
    # Apply leaves the index to the next reindex, which sees the changed log.
    assert "0 added, 1 modified, 0 deleted" in reindexed.stdout
