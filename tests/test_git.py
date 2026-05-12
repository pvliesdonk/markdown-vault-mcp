"""Tests for the git write strategy module."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.git import (
    GitWriteStrategy,
    _find_git_root,
    git_write_strategy,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository for testing."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )
    # Create an initial commit so HEAD exists.
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    return repo


@pytest.fixture
def git_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a working repo with a bare remote for push testing."""
    import subprocess

    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True,
        capture_output=True,
    )

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(
        ["git", "init", str(work)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "remote", "add", "origin", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "push.default", "current"],
        check=True,
        capture_output=True,
    )
    # Initial commit + push so upstream tracking exists.
    (work / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "-C", str(work), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    # Detect the default branch name (main or master).
    branch_result = subprocess.run(
        ["git", "-C", str(work), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    branch = branch_result.stdout.strip() or "main"
    subprocess.run(
        ["git", "-C", str(work), "push", "-u", "origin", branch],
        check=True,
        capture_output=True,
    )
    return work, bare


class TestFindGitRoot:
    def test_finds_root(self, git_repo: Path) -> None:
        """_find_git_root returns the repo root for a file inside it."""
        subdir = git_repo / "sub"
        subdir.mkdir()
        result = _find_git_root(subdir)
        assert result == git_repo

    def test_no_repo_returns_none(self, tmp_path: Path) -> None:
        """_find_git_root returns None when not in a git repo."""
        isolated = tmp_path / "no_git"
        isolated.mkdir()
        result = _find_git_root(isolated)
        assert result is None


class TestGitWriteStrategy:
    def test_commit_on_write(self, git_repo: Path) -> None:
        """Strategy commits after a write operation."""

        callback = git_write_strategy()
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        callback(test_file, "# Note\n", "write")

        # Verify commit was created.
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: note.md" in result.stdout

    def test_commit_on_edit(self, git_repo: Path) -> None:
        """Strategy commits after an edit operation."""

        callback = git_write_strategy()

        # First create the file and commit it.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Now edit it.
        test_file.write_text("# Edited Note\n")
        callback(test_file, "# Edited Note\n", "edit")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "edit: note.md" in result.stdout

    def test_commit_on_delete(self, git_repo: Path) -> None:
        """Strategy stages deletion after a delete operation."""

        callback = git_write_strategy()

        # README.md is already tracked. Delete it.
        readme = git_repo / "README.md"
        readme.unlink()

        callback(readme, "", "delete")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "delete: README.md" in result.stdout

    def test_commit_on_rename(self, git_repo: Path) -> None:
        """Strategy stages both old deletion and new addition on rename."""

        callback = git_write_strategy()

        # First create and track a file.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Simulate rename: move file on disk, then call callback with new path.
        new_file = git_repo / "renamed.md"
        test_file.rename(new_file)
        callback(new_file, "# Note\n", "rename")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "rename: renamed.md" in result.stdout

        # Verify the old file is not left as an unstaged deletion.
        status = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        assert status.stdout.strip() == ""

    def test_commit_on_rename_of_untracked_file(self, git_repo: Path) -> None:
        """Rename of a never-committed file: only new path is committed."""

        callback = git_write_strategy()

        # Create file on disk without going through the callback.
        untracked = git_repo / "untracked.md"
        untracked.write_text("# Untracked\n")

        # Simulate rename: move file, call callback with new path.
        new_file = git_repo / "renamed_untracked.md"
        untracked.rename(new_file)
        callback(new_file, "# Untracked\n", "rename")

        # Commit should succeed; new file is added.
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "rename: renamed_untracked.md" in result.stdout

        # Working tree is clean.
        status = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        assert status.stdout.strip() == ""

    def test_no_repo_logs_warning(self, tmp_path: Path) -> None:
        """Strategy logs warning and skips when not in a git repo."""
        isolated = tmp_path / "no_git"
        isolated.mkdir()
        test_file = isolated / "note.md"
        test_file.write_text("# Note\n")

        callback = git_write_strategy()

        # Should not raise, just log a warning.
        callback(test_file, "# Note\n", "write")

    def test_no_op_write_skips_commit(self, git_repo: Path) -> None:
        """Writing identical content should not produce an error commit."""

        callback = git_write_strategy()

        # Create and commit the file.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Write identical content again — should not error.
        callback(test_file, "# Note\n", "write")

        # Only one write commit should exist (not two).
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.count("write: note.md") == 1

    def test_push_failure_does_not_propagate(self, git_repo: Path) -> None:
        """Push failure is logged but does not raise."""
        callback = git_write_strategy()
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        # Push will fail (no remote configured) but should not raise.
        callback(test_file, "# Note\n", "write")

    def test_callback_with_token(self, git_repo: Path) -> None:
        """Strategy accepts a token parameter without error."""
        callback = git_write_strategy(token="ghp_test_token")
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        # Should commit successfully (push will fail — no remote).
        callback(test_file, "# Note\n", "write")


class TestGitWriteStrategyClass:
    """Tests for the GitWriteStrategy class directly."""

    def test_flush_pushes_to_remote(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """flush() pushes accumulated commits to the bare remote."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        # Not pushed yet (push_delay_s=0 means push only on close/flush).
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" not in result.stdout

        # Flush triggers push.
        strategy.flush()

        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

    def test_close_flushes(self, git_repo_with_remote: tuple[Path, Path]) -> None:
        """close() flushes pending push and marks strategy as closed."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        strategy.close()

        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

        # Further writes are ignored after close.
        md_file.write_text("# Updated\n")
        strategy(md_file, "# Updated\n", "edit")
        result2 = subprocess.run(
            ["git", "-C", str(work), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "edit: test.md" not in result2.stdout

    def test_deferred_push_fires_after_delay(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Timer-based push fires after push_delay_s of idle."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0.3)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        # Not pushed immediately.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" not in result.stdout

        # Poll until push lands (max 3s).
        for _ in range(30):
            time.sleep(0.1)
            result = subprocess.run(
                ["git", "-C", str(bare), "log", "--oneline"],
                capture_output=True,
                text=True,
            )
            if "write: test.md" in result.stdout:
                break
        else:
            pytest.fail("Deferred push did not fire within 3 seconds")

        strategy.close()

    def test_multiple_writes_single_push(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Multiple rapid writes result in a single deferred push."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0.3)

        for i in range(5):
            md_file = work / f"note_{i}.md"
            md_file.write_text(f"# Note {i}\n")
            strategy(md_file, f"# Note {i}\n", "write")

        # Not pushed yet.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "note_4.md" not in result.stdout

        # Poll until push lands (max 3s).
        for _ in range(30):
            time.sleep(0.1)
            result = subprocess.run(
                ["git", "-C", str(bare), "log", "--oneline"],
                capture_output=True,
                text=True,
            )
            if "note_4.md" in result.stdout:
                break
        else:
            pytest.fail("Deferred push did not fire within 3 seconds")

        # All 5 commits pushed in a single push.
        for i in range(5):
            assert f"write: note_{i}.md" in result.stdout

        strategy.close()

    def test_push_with_token_to_bare_remote(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Push with token uses GIT_ASKPASS against a local bare remote."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token="dummy_token", push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")
        strategy.flush()

        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

    def test_token_not_in_command_args(self, tmp_path: Path) -> None:
        """Token must not appear in any git command-line arguments."""

        from unittest.mock import patch

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        # Set up repo with remote inline so we can patch subprocess.
        bare = tmp_path / "bare.git"
        bare.mkdir()
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(
            ["git", "init", str(work)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "push.default", "current"],
            check=True,
            capture_output=True,
        )

        secret_token = "super_secret_pat_xyz"
        strategy = GitWriteStrategy(token=secret_token, push_delay_s=0)
        md_file = work / "check.md"
        md_file.write_text("# Check\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            strategy(md_file, "# Check\n", "write")
            strategy.flush()

        for cmd in recorded_cmds:
            for arg in cmd:
                assert secret_token not in arg, (
                    f"Token found in command argument: {cmd!r}"
                )

    def test_git_env_askpass_uses_configured_username(self) -> None:
        """Askpass helper returns username for username prompts."""

        strategy = GitWriteStrategy(token="topsecret", username="oauth2")
        env = strategy._git_env()
        assert env is not None
        script = env["GIT_ASKPASS"]
        try:
            username = subprocess.run(
                [script, "Username for 'https://example.com':"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            ).stdout.strip()
            password = subprocess.run(
                [script, "Password for 'https://example.com':"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            ).stdout.strip()
            assert username == "oauth2"
            assert password == "topsecret"
        finally:
            strategy._cleanup_git_env(env)

    def test_startup_recovery_pushes_unpushed(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """On first invocation, unpushed local commits are pushed."""

        work, bare = git_repo_with_remote

        # Create a local commit without pushing.
        md_file = work / "local_only.md"
        md_file.write_text("# Local\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "--", str(md_file)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local only"],
            check=True,
            capture_output=True,
        )

        # Verify not on remote.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "local only" not in result.stdout

        # Create strategy and trigger first invocation.
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file2 = work / "trigger.md"
        md_file2.write_text("# Trigger\n")
        strategy(md_file2, "# Trigger\n", "write")
        strategy.flush()

        # Both the old unpushed commit and the new one should be on remote.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "local only" in result.stdout
        assert "write: trigger.md" in result.stdout


class TestConfigIntegration:
    def test_git_token_wires_up_strategy(self, tmp_path: Path) -> None:
        """Legacy mode: token-only config still wires pull+push strategy."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" in kwargs
        assert isinstance(kwargs["on_write"], GitWriteStrategy)
        assert kwargs["git_pull_interval_s"] == 600

    def test_no_git_token_uses_local_only_mode(self, tmp_path: Path) -> None:
        """No token and no repo URL uses local-only mode with no pull loop."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" in kwargs
        assert kwargs["git_pull_interval_s"] == 0

    def test_git_repo_url_enables_managed_mode(self, tmp_path: Path) -> None:
        """Managed mode uses configured pull interval and write callback."""

        from markdown_vault_mcp.config import CollectionConfig

        bare = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )

        config = CollectionConfig(
            source_dir=tmp_path / "vault",
            read_only=False,
            git_repo_url=str(bare),
            git_token="ghp_test",
            git_pull_interval_s=321,
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" in kwargs
        assert kwargs["git_pull_interval_s"] == 321

    def test_push_delay_passed_to_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_push_delay_s to strategy."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
            git_token="ghp_test",
            git_push_delay_s=60.0,
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._push_delay_s == 60.0

    def test_load_config_reads_push_delay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() reads GIT_PUSH_DELAY_S from environment."""
        from markdown_vault_mcp.config import load_config

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "45")
        config = load_config()
        assert config.git_push_delay_s == 45.0

    def test_load_config_invalid_push_delay_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default on invalid GIT_PUSH_DELAY_S."""
        from markdown_vault_mcp.config import load_config

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "not_a_number")
        config = load_config()
        assert config.git_push_delay_s == 30.0


class TestCollectionCloseWiresStrategy:
    def test_collection_close_calls_strategy_close(self, tmp_path: Path) -> None:
        """Collection.close() calls on_write.close() if available."""
        from markdown_vault_mcp.collection import Collection

        closed = []

        class MockStrategy:
            def __call__(self, path, content, operation):  # type: ignore[no-untyped-def]
                pass

            def close(self) -> None:
                closed.append(True)

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "test.md").write_text("# Test\n")
        col = Collection(
            source_dir=vault,
            read_only=False,
            on_write=MockStrategy(),  # type: ignore[arg-type]
        )
        col.close()

        assert closed == [True]


class TestCheckIdentity:
    """Tests for the _check_identity() warning path."""

    def test_check_identity_warns_when_no_user_email(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity warns when git config has no user.email."""

        from unittest.mock import patch

        # Remove user.email from the repo config so git config returns empty.
        subprocess.run(
            ["git", "-C", str(git_repo), "config", "--unset", "user.email"],
            capture_output=True,
        )

        strategy = GitWriteStrategy()
        strategy._git_root = git_repo

        # Mock subprocess.run to return empty stdout (no user.email).
        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            strategy._check_identity()

        # Verify warning was logged with the expected message.
        assert any(
            "no user.email in git config" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        # Verify the default identity is mentioned in the warning.
        assert any(
            "markdown-vault-mcp" in record.message
            and "noreply@markdown-vault-mcp" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_check_identity_no_warning_when_user_email_set(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity does not warn when git config has user.email."""
        from unittest.mock import patch

        strategy = GitWriteStrategy()
        strategy._git_root = git_repo

        # Mock subprocess.run to return non-empty stdout (user.email is set).
        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "existing@example.com\n"
            strategy._check_identity()

        # Verify no warning was logged.
        assert not any(
            "no user.email in git config" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_check_identity_custom_name_and_email_in_warning(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity warning shows custom commit name and email."""
        from unittest.mock import patch

        strategy = GitWriteStrategy(
            commit_name="CustomBot", commit_email="bot@custom.local"
        )
        strategy._git_root = git_repo

        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            strategy._check_identity()

        # Verify the warning mentions the custom identity.
        assert any(
            "CustomBot" in record.message and "bot@custom.local" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )


class TestTokenRedactionInLogs:
    """Token must never appear in log output — even when it leaks via stderr."""

    def test_stage_and_commit_error_token_redacted(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CalledProcessError in __call__ redacts token from logged stderr."""

        from unittest.mock import patch

        secret = "ghp_supersecret_token_xyz"
        strategy = GitWriteStrategy(token=secret, push_delay_s=0)

        # Force _git_root to the repo so we skip the init path.
        strategy._git_root = git_repo
        strategy._checked = True

        fake_exc = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "commit"],
            stderr=f"remote: Invalid credentials {secret}",
        )

        with (
            patch("markdown_vault_mcp.git._stage_and_commit", side_effect=fake_exc),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            test_file = git_repo / "note.md"
            test_file.write_text("# Note\n")
            strategy(test_file, "# Note\n", "write")

        log_text = " ".join(r.message for r in caplog.records)
        assert secret not in log_text
        assert "***" in log_text

    def test_do_push_safe_called_process_error_token_redacted(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_do_push_safe redacts token when push raises CalledProcessError."""

        from unittest.mock import patch

        secret = "ghp_push_secret_abc123"
        strategy = GitWriteStrategy(token=secret, push_delay_s=0)
        strategy._git_root = git_repo
        strategy._push_pending = True

        fake_exc = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push", "origin"],
            stderr=f"fatal: authentication failed — token={secret}",
        )

        with (
            patch("markdown_vault_mcp.git._push", side_effect=fake_exc),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            strategy._do_push_safe()

        log_text = " ".join(r.message for r in caplog.records)
        assert secret not in log_text
        assert "***" in log_text

    def test_do_push_safe_generic_exception_caught(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_do_push_safe catches generic Exception and logs it without propagating."""

        from unittest.mock import patch

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        strategy._git_root = git_repo
        strategy._push_pending = True

        with (
            patch(
                "markdown_vault_mcp.git._push", side_effect=RuntimeError("network down")
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            # Must not raise.
            strategy._do_push_safe()

        assert any("Git push failed" in r.message for r in caplog.records)

    def test_push_if_unpushed_token_redacted_on_failure(
        self, git_repo_with_remote: tuple[Path, Path], caplog: pytest.LogCaptureFixture
    ) -> None:
        """_push_if_unpushed redacts token in logged error when startup push fails."""

        from unittest.mock import patch

        work, _bare = git_repo_with_remote

        # Create an unpushed local commit so _push_if_unpushed actually calls _push.
        md_file = work / "unpushed.md"
        md_file.write_text("# Unpushed\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "--", str(md_file)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "unpushed"],
            check=True,
            capture_output=True,
        )

        secret = "ghp_startup_token_999"
        strategy = GitWriteStrategy(token=secret, push_delay_s=0)
        strategy._git_root = work

        fake_exc = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push", "origin"],
            stderr=f"remote: bad credentials {secret}",
        )

        with (
            patch("markdown_vault_mcp.git._push", side_effect=fake_exc),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            strategy._push_if_unpushed()

        log_text = " ".join(r.message for r in caplog.records)
        assert secret not in log_text
        assert "***" in log_text


class TestGitLfsSupport:
    """Tests for the git_lfs parameter on GitWriteStrategy."""

    def test_git_lfs_pull_on_init(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When git_lfs=True, git lfs pull is called during first invocation."""

        from unittest.mock import patch

        strategy = GitWriteStrategy(git_lfs=True)
        strategy._git_root = git_repo
        strategy._checked = True  # skip _find_git_root; trigger lfs directly

        recorded_cmds: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
            from unittest.mock import MagicMock

            recorded_cmds.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch("markdown_vault_mcp.git.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.INFO, logger="markdown_vault_mcp.git"),
        ):
            strategy._lfs_pull()

        lfs_cmds = [c for c in recorded_cmds if "lfs" in c]
        assert len(lfs_cmds) == 1
        assert lfs_cmds[0] == [
            "git",
            "-C",
            str(git_repo),
            "lfs",
            "pull",
        ]
        assert any("LFS" in r.message for r in caplog.records)

    def test_git_lfs_disabled_skips_pull(self, git_repo: Path) -> None:
        """When git_lfs=False, no git lfs commands are issued."""
        from unittest.mock import patch

        strategy = GitWriteStrategy(git_lfs=False)
        strategy._git_root = git_repo

        recorded_cmds: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
            from unittest.mock import MagicMock

            recorded_cmds.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=mock_run):
            strategy._lfs_pull()

        lfs_cmds = [c for c in recorded_cmds if "lfs" in c]
        assert lfs_cmds == []

    def test_git_lfs_pull_failure_logged_not_raised(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """LFS pull failure is logged at ERROR but does not propagate."""

        from unittest.mock import patch

        strategy = GitWriteStrategy(git_lfs=True)
        strategy._git_root = git_repo

        fake_exc = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "-C", str(git_repo), "lfs", "pull"],
            stderr="error: failed to fetch some/object",
        )

        with (
            patch("markdown_vault_mcp.git.subprocess.run", side_effect=fake_exc),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            # Must not raise.
            strategy._lfs_pull()

        assert any("LFS pull failed" in r.message for r in caplog.records)

    def test_git_lfs_pull_file_not_found_logged_not_raised(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When git-lfs is not on PATH, logs ERROR and does not propagate."""

        from unittest.mock import patch

        strategy = GitWriteStrategy(git_lfs=True)
        strategy._git_root = git_repo

        with (
            patch(
                "markdown_vault_mcp.git.subprocess.run",
                side_effect=FileNotFoundError("git not found"),
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            # Must not raise.
            strategy._lfs_pull()

        assert any("LFS pull failed" in r.message for r in caplog.records)

    def test_git_lfs_default_is_true(self) -> None:
        """GitWriteStrategy defaults to git_lfs=True."""
        strategy = GitWriteStrategy()
        assert strategy._git_lfs is True

    def test_git_write_strategy_factory_passes_git_lfs(self) -> None:
        """git_write_strategy() passes git_lfs through to GitWriteStrategy."""
        strategy = git_write_strategy(git_lfs=False)
        assert strategy._git_lfs is False

    def test_lfs_pull_triggered_via_call(self, git_repo: Path) -> None:
        """__call__() triggers _lfs_pull() on first invocation when git_lfs=True."""
        from unittest.mock import MagicMock, patch

        strategy = GitWriteStrategy(git_lfs=True, push_delay_s=0)

        lfs_pull_mock = MagicMock()
        commit_mock = MagicMock()
        push_if_unpushed_mock = MagicMock()

        test_file = git_repo / "note.md"
        test_file.write_text("# Test\n")

        with (
            patch.object(strategy, "_lfs_pull", lfs_pull_mock),
            patch.object(strategy, "_push_if_unpushed", push_if_unpushed_mock),
            patch("markdown_vault_mcp.git._stage_and_commit", commit_mock),
        ):
            # First call — triggers lazy init including _lfs_pull.
            strategy(test_file, "# Test\n", "write")
            assert lfs_pull_mock.call_count == 1, (
                "_lfs_pull must be called on first __call__"
            )

            # Second call — lazy init is skipped; _lfs_pull not called again.
            strategy(test_file, "# Test\n", "write")
            assert lfs_pull_mock.call_count == 1, "_lfs_pull must not be called again"


class TestStageAndCommitPathHandling:
    """Edge cases in _stage_and_commit path handling."""

    def test_path_not_under_git_root_uses_full_path(self, git_repo: Path) -> None:
        """Commit message uses the full path when path is not under git_root."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from markdown_vault_mcp.git import _stage_and_commit

        # Path outside the git_root — path.relative_to(git_root) will raise ValueError.
        outside_path = Path("/tmp/some_other_file.md")

        recorded_msgs: list[str] = []

        def mock_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
            if isinstance(cmd, list) and "commit" in cmd:
                try:
                    idx = cmd.index("-m")
                    recorded_msgs.append(cmd[idx + 1])
                except (ValueError, IndexError):
                    pass
                result = MagicMock()
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
                return result

            if isinstance(cmd, list) and "diff" in cmd:
                # Return nonzero so the commit is not skipped.
                result = MagicMock()
                result.returncode = 1
                return result

            # All other calls (git add) succeed silently.
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=mock_run):
            _stage_and_commit(git_repo, outside_path, "write")

        # The commit message must use the full outside_path (not a relative path).
        assert len(recorded_msgs) == 1
        assert str(outside_path) in recorded_msgs[0]


class TestCommitterIdentityInCommit:
    """Tests that commit_name and commit_email appear in git commit commands."""

    def test_default_committer_in_commit_flags(self, git_repo: Path) -> None:
        """_stage_and_commit uses default committer identity in -c flags."""

        from unittest.mock import patch

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            from markdown_vault_mcp.git import _stage_and_commit

            _stage_and_commit(git_repo, test_file, "write")

        # Find the commit command (should have "commit" in it).
        commit_cmd = None
        for cmd in recorded_cmds:
            if "commit" in cmd:
                commit_cmd = cmd
                break

        assert commit_cmd is not None, "No commit command found"
        # Verify the default -c flags are present.
        assert "-c" in commit_cmd
        assert "user.name=markdown-vault-mcp" in commit_cmd
        assert "user.email=noreply@markdown-vault-mcp" in commit_cmd

    def test_custom_committer_in_commit_flags(self, git_repo: Path) -> None:
        """_stage_and_commit uses custom committer identity in -c flags."""

        from unittest.mock import patch

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            from markdown_vault_mcp.git import _stage_and_commit

            _stage_and_commit(
                git_repo,
                test_file,
                "write",
                commit_name="CustomBot",
                commit_email="bot@example.com",
            )

        # Find the commit command.
        commit_cmd = None
        for cmd in recorded_cmds:
            if "commit" in cmd:
                commit_cmd = cmd
                break

        assert commit_cmd is not None
        # Verify the custom -c flags are present.
        assert "-c" in commit_cmd
        assert "user.name=CustomBot" in commit_cmd
        assert "user.email=bot@example.com" in commit_cmd

    def test_strategy_passes_commit_identity_to_stage_and_commit(
        self, git_repo: Path
    ) -> None:
        """GitWriteStrategy passes commit_name and commit_email to _stage_and_commit."""
        from unittest.mock import patch

        recorded_calls: list[tuple] = []

        def recording_stage_and_commit(
            git_root, path, operation, commit_name="default", commit_email="default"
        ):
            recorded_calls.append(
                (git_root, path, operation, commit_name, commit_email)
            )

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        strategy = GitWriteStrategy(
            commit_name="BotName", commit_email="bot@test.local"
        )

        with patch(
            "markdown_vault_mcp.git._stage_and_commit",
            side_effect=recording_stage_and_commit,
        ):
            strategy(test_file, "# Note\n", "write")

        # Verify the custom identity was passed.
        assert len(recorded_calls) > 0
        call = recorded_calls[0]
        assert call[3] == "BotName"  # commit_name
        assert call[4] == "bot@test.local"  # commit_email


class TestGitSyncOnce:
    def test_sync_once_fast_forwards(
        self, tmp_path: Path, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """sync_once() fast-forwards when the remote has advanced."""

        work, bare = git_repo_with_remote

        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )

        (other / "README.md").write_text("# Remote advance\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote advance"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        did_advance = strategy.sync_once(work)
        assert did_advance is True
        assert "Remote advance" in (work / "README.md").read_text()

    def test_sync_once_passes_env_to_lfs_pull(
        self, tmp_path: Path, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """sync_once() forwards the auth env to git lfs pull."""

        work, bare = git_repo_with_remote

        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "README.md").write_text("# Remote advance\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote advance"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token="ghp_test", push_delay_s=0)
        captured: list[dict[str, str] | None] = []

        def fake_lfs_pull(env: dict[str, str] | None = None) -> None:
            captured.append(env)

        strategy._lfs_pull = fake_lfs_pull  # type: ignore[assignment]
        did_advance = strategy.sync_once(work)
        assert did_advance is True
        assert captured
        assert captured[-1] is not None
        assert "GIT_ASKPASS" in captured[-1]

    def test_sync_once_diverged_clean_rebases(
        self,
        tmp_path: Path,
        git_repo_with_remote: tuple[Path, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """sync_once() rebases local commits onto upstream when branches diverge cleanly.

        This is the common Obsidian scenario: both sides committed on different
        files so the rebase applies without conflict.
        """

        work, bare = git_repo_with_remote

        # Create a local-only commit on a different file (do not push).
        (work / "local-note.md").write_text("# Local note\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local note"],
            check=True,
            capture_output=True,
        )

        # Advance remote on a separate clone (different file).
        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "obsidian-note.md").write_text("# Obsidian note\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "obsidian note"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.git"):
            did_advance = strategy.sync_once(work)

        assert did_advance is True
        assert any(
            "rebased local commits onto upstream" in r.message for r in caplog.records
        )
        # Both files should be present after the rebase.
        assert (work / "local-note.md").exists()
        assert (work / "obsidian-note.md").exists()

    def test_sync_once_diverged_conflict_creates_conflict_file(
        self,
        tmp_path: Path,
        git_repo_with_remote: tuple[Path, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """sync_once() resolves same-file conflict by saving MCP version as conflict file."""
        import frontmatter as fm

        work, bare = git_repo_with_remote

        # Create a local-only commit on README.md (do not push).
        (work / "README.md").write_text("# Local diverge\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local diverge"],
            check=True,
            capture_output=True,
        )

        # Advance remote on a separate clone, also modifying README.md.
        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "README.md").write_text("# Remote diverge\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote diverge"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"):
            did_advance = strategy.sync_once(work)

        # Conflict resolved — HEAD advanced.
        assert did_advance is True
        assert any("conflict resolved" in r.message for r in caplog.records)

        # Original file has upstream content.
        assert "# Remote diverge" in (work / "README.md").read_text()

        # Conflict file exists with MCP content.
        conflict_files = list(work.glob("README.conflict-mcp-*.md"))
        assert len(conflict_files) == 1
        conflict_file = conflict_files[0]
        assert "# Local diverge" in conflict_file.read_text()

        # Both files have symmetric conflict_with frontmatter.
        orig_post = fm.loads((work / "README.md").read_text())
        conflict_post = fm.loads(conflict_file.read_text())

        assert orig_post.metadata["conflict_with"] == str(
            conflict_file.relative_to(work)
        )
        assert conflict_post.metadata["conflict_with"] == "README.md"
        assert "conflict_date" in orig_post.metadata
        assert "conflict_date" in conflict_post.metadata

    def test_sync_once_conflict_preserves_existing_frontmatter(
        self,
        tmp_path: Path,
        git_repo_with_remote: tuple[Path, Path],
    ) -> None:
        """Conflict resolution preserves existing frontmatter on both files."""
        import frontmatter as fm

        work, bare = git_repo_with_remote

        # Local commit with frontmatter.
        (work / "note.md").write_text(
            "---\ntitle: MCP Note\ntags: [test]\n---\n# MCP content\n"
        )
        subprocess.run(
            ["git", "-C", str(work), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local note"],
            check=True,
            capture_output=True,
        )

        # Remote commit on same file with different frontmatter.
        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "note.md").write_text(
            "---\ntitle: Obsidian Note\ntags: [vault]\n---\n# Obsidian content\n"
        )
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote note"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        did_advance = strategy.sync_once(work)
        assert did_advance is True

        # Original: upstream frontmatter preserved + conflict fields added.
        orig_post = fm.loads((work / "note.md").read_text())
        assert orig_post.metadata["title"] == "Obsidian Note"
        assert orig_post.metadata["tags"] == ["vault"]
        assert "conflict_with" in orig_post.metadata

        # Conflict file: MCP frontmatter preserved + conflict fields added.
        conflict_files = list(work.glob("note.conflict-mcp-*.md"))
        assert len(conflict_files) == 1
        conflict_post = fm.loads(conflict_files[0].read_text())
        assert conflict_post.metadata["title"] == "MCP Note"
        assert conflict_post.metadata["tags"] == ["test"]
        assert conflict_post.metadata["conflict_with"] == "note.md"

    def test_sync_once_conflict_non_conflicting_files_preserved(
        self,
        tmp_path: Path,
        git_repo_with_remote: tuple[Path, Path],
    ) -> None:
        """Non-conflicting local changes in the same commit survive the rebase."""

        work, bare = git_repo_with_remote

        # Local commit: modify README.md AND add a new file.
        (work / "README.md").write_text("# Local diverge\n")
        (work / "safe-note.md").write_text("# Safe note\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local changes"],
            check=True,
            capture_output=True,
        )

        # Remote: only modify README.md (conflict), leave safe-note.md alone.
        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "README.md").write_text("# Remote diverge\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote diverge"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        did_advance = strategy.sync_once(work)
        assert did_advance is True

        # Non-conflicting file preserved by the rebase.
        assert (work / "safe-note.md").exists()
        assert "# Safe note" in (work / "safe-note.md").read_text()

        # Conflict file created for README.md.
        conflict_files = list(work.glob("README.conflict-mcp-*.md"))
        assert len(conflict_files) == 1

    def test_sync_once_conflict_commit_failure_returns_false(
        self,
        tmp_path: Path,
        git_repo_with_remote: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """sync_once returns False + WARNING when _write_conflict_files commit fails.

        Mirrors test_force_pull_conflict_commit_failure_surfaces_resolution_failed
        on the force_pull side; covers the sync_once None-return path added in #462.
        The rebase --continue succeeds (so HEAD moves) before the conflict-commit
        fails, so we also assert HEAD actually advanced.
        """
        import subprocess as _real_subprocess

        from markdown_vault_mcp.git import subprocess as git_subprocess

        work, bare = git_repo_with_remote

        # Local-only divergent commit on README.md.
        (work / "README.md").write_text("# Local diverge\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local diverge"],
            check=True,
            capture_output=True,
        )

        # Advance remote via sibling clone, conflicting on the same file.
        other = tmp_path / "other"
        subprocess.run(
            ["git", "clone", str(bare), str(other)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (other / "README.md").write_text("# Remote diverge\n")
        subprocess.run(
            ["git", "-C", str(other), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "commit", "-m", "remote diverge"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(other), "push"],
            check=True,
            capture_output=True,
        )

        real_run = _real_subprocess.run

        def _patched_run(args: list[str], **kwargs: object) -> object:
            # Fail only the conflict-files commit; let everything else run.
            if (
                isinstance(args, list)
                and "commit" in args
                and any(isinstance(a, str) and a.startswith("conflict:") for a in args)
            ):
                return _real_subprocess.CompletedProcess(
                    args=args,
                    returncode=1,
                    stdout="",
                    stderr="error: pre-commit hook rejected the commit",
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(git_subprocess, "run", _patched_run)

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        head_before = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"):
            did_advance = strategy.sync_once(work)
        head_after = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Commit failed → sync_once reports no advance to the caller.
        assert did_advance is False
        # WARNING surfaced for operator visibility.
        assert any(
            "conflict commit failed, skipping" in r.message for r in caplog.records
        )
        # The rebase --continue ran before the failing commit, so HEAD did move.
        assert head_after != head_before

    def test_resolve_rebase_conflicts_max_iterations_returns_saved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_resolve_rebase_conflicts returns saved content when iteration limit hit."""
        from types import SimpleNamespace

        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        call_count = 0

        def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            # diff --name-only --diff-filter=U: always return a conflicting file.
            if "--diff-filter=U" in cmd:
                return SimpleNamespace(returncode=0, stdout="README.md\n", stderr="")
            # git show REBASE_HEAD:README.md: return fake MCP content.
            if "show" in cmd:
                return SimpleNamespace(
                    returncode=0, stdout="# MCP content\n", stderr=""
                )
            # git checkout --ours: succeed silently.
            if "checkout" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            # git add: succeed silently.
            if cmd[3] == "add" and "--diff-filter=U" not in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            # rebase --continue: always fail (simulates never-ending conflicts).
            if "rebase" in cmd and "--continue" in cmd:
                return SimpleNamespace(returncode=1, stdout="", stderr="conflict")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("markdown_vault_mcp.git.subprocess.run", fake_run)

        result = strategy._resolve_rebase_conflicts(tmp_path, env=None)

        # The same file conflicted in all 50 iterations; deduplication keeps only
        # the last version, so the result is a single unique entry.
        assert len(result) == 1
        assert result[0] == ("README.md", "# MCP content\n")

    def test_write_conflict_files_commit_failure_returns_none(
        self,
        git_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_write_conflict_files returns None and logs ERROR when git commit fails."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        # Create a real file for the original path.
        (git_repo / "note.md").write_text("# Original\n")

        saved = [("note.md", "# MCP version\n")]

        import subprocess as sp

        real_run = sp.run

        def patched_run(cmd: list[str], **kwargs: object) -> object:
            # Make git commit return failure.
            if isinstance(cmd, list) and "commit" in cmd:
                from types import SimpleNamespace

                return SimpleNamespace(
                    returncode=1, stdout="", stderr="nothing to commit"
                )
            return real_run(cmd, **kwargs)

        import unittest.mock as mock

        with (
            mock.patch(
                "markdown_vault_mcp.git.subprocess.run", side_effect=patched_run
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            written = strategy._write_conflict_files(git_repo, saved, env=None)

        # Commit failed: helper signals failure to its caller via None.
        assert written is None
        # The ERROR was logged.
        assert any("conflict commit failed" in r.message for r in caplog.records)

    def test_write_conflict_files_commit_failure_redacts_token(
        self,
        git_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Commit-failure stderr passes through _redact before being logged."""
        strategy = GitWriteStrategy(token="secret-pat-xyz", push_delay_s=0)

        (git_repo / "note.md").write_text("# Original\n")
        saved = [("note.md", "# MCP version\n")]

        import subprocess as sp

        real_run = sp.run

        def patched_run(cmd: list[str], **kwargs: object) -> object:
            if isinstance(cmd, list) and "commit" in cmd:
                from types import SimpleNamespace

                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="fatal: auth failed for secret-pat-xyz@host",
                )
            return real_run(cmd, **kwargs)

        import unittest.mock as mock

        with (
            mock.patch(
                "markdown_vault_mcp.git.subprocess.run", side_effect=patched_run
            ),
            caplog.at_level(logging.ERROR, logger="markdown_vault_mcp.git"),
        ):
            strategy._write_conflict_files(git_repo, saved, env=None)

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "secret-pat-xyz" not in joined
        assert "***" in joined

    def test_write_conflict_files_invalid_frontmatter_logs_warning(
        self,
        git_repo: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_write_conflict_files logs WARNING for unparseable frontmatter but still writes files."""
        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        # Write an original file with invalid YAML frontmatter.
        (git_repo / "broken.md").write_text("---\n{invalid: yaml: [\n---\n# Body\n")

        # The saved MCP content also has invalid frontmatter.
        saved = [("broken.md", "---\n{invalid: yaml: [\n---\n# MCP Body\n")]

        import unittest.mock as mock

        with (
            mock.patch(
                "markdown_vault_mcp.git.frontmatter.loads",
                side_effect=Exception("yaml parse error"),
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git"),
        ):
            written = strategy._write_conflict_files(git_repo, saved, env=None)

        # Conflict file was still written despite parse errors.
        assert len(written) == 1
        conflict_path = git_repo / written[0]
        assert conflict_path.exists()
        assert "conflict_with" in conflict_path.read_text()

        # Both frontmatter-parse warnings were logged.
        warnings = [r for r in caplog.records if "frontmatter" in r.message]
        assert len(warnings) >= 1


class TestRebaseInProgress:
    """Tests for the _rebase_in_progress helper (refs #466)."""

    def test_returns_false_on_clean_repo(self, git_repo: Path) -> None:
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        assert strategy._rebase_in_progress(git_repo, env=None) is False

    def test_detects_rebase_merge_directory(self, git_repo: Path) -> None:
        (git_repo / ".git" / "rebase-merge").mkdir()
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        assert strategy._rebase_in_progress(git_repo, env=None) is True

    def test_detects_rebase_apply_directory(self, git_repo: Path) -> None:
        (git_repo / ".git" / "rebase-apply").mkdir()
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        assert strategy._rebase_in_progress(git_repo, env=None) is True

    def test_ignores_stale_rebase_head_ref(self, git_repo: Path) -> None:
        """Stale REBASE_HEAD ref must not trip the in-progress check (refs #466)."""
        head_sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (git_repo / ".git" / "REBASE_HEAD").write_text(head_sha + "\n")

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        assert strategy._rebase_in_progress(git_repo, env=None) is False


class TestGitPullLoop:
    def test_start_runs_tick_with_pause_and_on_pull(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start() runs a tick, using pause_writes and calling on_pull."""
        import contextlib
        import time
        from types import SimpleNamespace

        calls: list[str] = []

        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        # Pretend tmp_path is a git repo with an upstream so start() launches the loop.
        monkeypatch.setattr(strategy, "_ensure_git_root", lambda _p: tmp_path)
        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            ),
        )

        def fake_sync_once(repo_path: Path) -> bool:  # noqa: ARG001
            calls.append("sync")
            return True

        monkeypatch.setattr(strategy, "sync_once", fake_sync_once)

        pause_calls: list[str] = []

        @contextlib.contextmanager
        def pause() -> None:
            pause_calls.append("pause")
            yield

        on_pull_calls: list[str] = []

        def on_pull() -> None:
            on_pull_calls.append("pull")

        strategy.start(
            repo_path=tmp_path,
            pull_interval_s=3600,
            pause_writes=pause,  # type: ignore[arg-type]
            on_pull=on_pull,
        )
        time.sleep(0.05)
        strategy.stop()

        assert calls
        assert pause_calls
        assert on_pull_calls

    def test_start_runs_tick_without_pause(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start() runs a tick when pause_writes is None."""
        import time
        from types import SimpleNamespace

        calls: list[str] = []

        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        monkeypatch.setattr(strategy, "_ensure_git_root", lambda _p: tmp_path)
        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            ),
        )

        def fake_sync_once(repo_path: Path) -> bool:  # noqa: ARG001
            calls.append("sync")
            return True

        monkeypatch.setattr(strategy, "sync_once", fake_sync_once)

        on_pull_calls: list[str] = []

        def on_pull() -> None:
            on_pull_calls.append("pull")

        strategy.start(
            repo_path=tmp_path,
            pull_interval_s=3600,
            pause_writes=None,
            on_pull=on_pull,
        )
        time.sleep(0.05)
        strategy.stop()

        assert calls
        assert on_pull_calls

    def test_tick_exceptions_do_not_kill_thread(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exceptions inside a pull tick are logged and the loop continues."""
        import contextlib
        import time
        from types import SimpleNamespace

        strategy = GitWriteStrategy(token=None, push_delay_s=0)

        # Pretend tmp_path is a git repo with an upstream so start() launches the loop.
        monkeypatch.setattr(strategy, "_ensure_git_root", lambda _p: tmp_path)
        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            ),
        )

        def boom(_repo_path: Path) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setattr(strategy, "sync_once", boom)

        @contextlib.contextmanager
        def pause() -> None:
            yield

        strategy.start(
            repo_path=tmp_path,
            pull_interval_s=3600,
            pause_writes=pause,  # type: ignore[arg-type]
            on_pull=lambda: None,
        )
        time.sleep(0.05)

        assert strategy._pull_thread is not None
        assert strategy._pull_thread.is_alive()

        strategy.stop()


class TestManagedGitMode:
    def test_managed_mode_clones_into_empty_source_dir(
        self, tmp_path: Path, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Managed mode clones when SOURCE_DIR exists but is empty."""
        _work, bare = git_repo_with_remote

        vault = tmp_path / "vault"
        vault.mkdir()

        strategy = GitWriteStrategy(
            repo_url=str(bare),
            managed=True,
            repo_path=vault,
            push_delay_s=0,
        )

        assert (vault / "README.md").exists()
        assert strategy._git_root == vault

    def test_managed_mode_remote_mismatch_raises(
        self, tmp_path: Path, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Managed mode rejects existing repos with a different origin URL."""

        work, _bare = git_repo_with_remote
        other_bare = tmp_path / "other.git"
        subprocess.run(
            ["git", "init", "--bare", str(other_bare)],
            check=True,
            capture_output=True,
        )

        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="remote mismatch"):
            GitWriteStrategy(
                repo_url=str(other_bare),
                managed=True,
                repo_path=work,
                push_delay_s=0,
            )

    def test_managed_mode_non_git_non_empty_dir_raises(self, tmp_path: Path) -> None:
        """Managed mode requires SOURCE_DIR to be empty or an existing git repo."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes.md").write_text("# Notes\n")

        with pytest.raises(ConfigurationError, match="empty or a git repository"):
            GitWriteStrategy(
                repo_url="https://github.com/acme/vault.git",
                managed=True,
                repo_path=vault,
            )

    def test_managed_mode_requires_directory_path(self, tmp_path: Path) -> None:
        """Managed mode rejects SOURCE_DIR that points to an existing file."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        target = tmp_path / "vault"
        target.write_text("not a directory")

        with pytest.raises(ConfigurationError, match="to be a directory"):
            GitWriteStrategy(
                repo_url="https://github.com/acme/vault.git",
                managed=True,
                repo_path=target,
            )

    def test_managed_mode_rejects_ssh_repo_url_with_token(self, tmp_path: Path) -> None:
        """Managed mode rejects SSH repo URLs when token auth is configured."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="requires HTTPS"):
            GitWriteStrategy(
                repo_url="git@github.com:owner/repo.git",
                token="ghp_secret",
                managed=True,
                repo_path=tmp_path / "vault",
            )

    def test_managed_mode_clone_file_not_found_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Managed clone raises ConfigurationError when git is unavailable."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        vault = tmp_path / "vault"
        vault.mkdir()

        def fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError("git not found")

        monkeypatch.setattr("markdown_vault_mcp.git.subprocess.run", fake_run)

        with pytest.raises(ConfigurationError, match="git is not installed"):
            GitWriteStrategy(
                repo_url="https://github.com/acme/vault.git",
                managed=True,
                repo_path=vault,
            )

    def test_managed_mode_requires_origin_remote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Managed mode errors when an existing git repo has no origin remote."""

        from types import SimpleNamespace

        from markdown_vault_mcp.exceptions import ConfigurationError

        vault = tmp_path / "vault"
        vault.mkdir()
        subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)

        original_run = subprocess.run

        def fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
            if cmd[:3] == ["git", "-C", str(vault)] and cmd[3:] == [
                "remote",
                "get-url",
                "origin",
            ]:
                return SimpleNamespace(returncode=2, stdout="", stderr="no origin")
            return original_run(cmd, **_kwargs)

        monkeypatch.setattr("markdown_vault_mcp.git.subprocess.run", fake_run)

        with pytest.raises(ConfigurationError, match="requires an 'origin'"):
            GitWriteStrategy(
                repo_url="https://github.com/acme/vault.git",
                managed=True,
                repo_path=vault,
            )

    def test_local_only_mode_commits_without_push(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Local-only mode commits writes and never pushes to origin."""

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(
            managed=False,
            enable_pull=False,
            enable_push=False,
            repo_path=work,
            push_delay_s=0,
        )
        md_file = work / "local_only.md"
        md_file.write_text("# Local\n")
        strategy(md_file, "# Local\n", "write")
        strategy.flush()

        local_log = subprocess.run(
            ["git", "-C", str(work), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_log = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "write: local_only.md" in local_log.stdout
        assert "write: local_only.md" not in remote_log.stdout


class TestCheckRemoteProtocol:
    """Tests for SSH remote validation when token auth is enabled."""

    @staticmethod
    def _make_run(url: str):
        from types import SimpleNamespace

        def fake_run(cmd, **_kwargs):
            if "get-url" in cmd:
                return SimpleNamespace(returncode=0, stdout=url + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return fake_run

    def test_ssh_git_at_raises_with_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.exceptions import ConfigurationError

        strategy = GitWriteStrategy(token="ghp_secret")
        strategy._git_root = tmp_path

        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            self._make_run("git@github.com:owner/repo.git"),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            strategy._check_remote_protocol(tmp_path)

        msg = str(exc_info.value)
        assert "SSH transport" in msg
        assert "remote set-url origin https://github.com/owner/repo.git" in msg

    def test_https_remote_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        strategy = GitWriteStrategy(token="ghp_secret")
        strategy._git_root = tmp_path

        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            self._make_run("https://github.com/owner/repo.git"),
        )

        strategy._check_remote_protocol(tmp_path)

    def test_no_token_does_not_raise_for_ssh_remote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        strategy = GitWriteStrategy(token=None)
        strategy._git_root = tmp_path

        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            self._make_run("git@github.com:owner/repo.git"),
        )

        strategy._check_remote_protocol(tmp_path)

    def test_startup_validation_raises_in_constructor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.exceptions import ConfigurationError

        monkeypatch.setattr(
            "markdown_vault_mcp.git._find_git_root",
            lambda _repo_path: tmp_path,
        )
        monkeypatch.setattr(
            "markdown_vault_mcp.git.subprocess.run",
            self._make_run("ssh://git@github.com/owner/repo.git"),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            GitWriteStrategy(token="ghp_secret", repo_path=tmp_path)

        msg = str(exc_info.value)
        assert "SSH transport" in msg
        assert "https://github.com/owner/repo.git" in msg


class TestGetFileHistory:
    """Tests for GitWriteStrategy.get_file_history()."""

    def _make_repo_with_commits(self, tmp_path: Path) -> Path:
        """Create a git repo with two commits touching note.md."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
            check=True,
        )
        # First commit
        (repo / "note.md").write_text("# Note v1\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "write: note.md"],
            capture_output=True,
            check=True,
        )
        # Second commit
        (repo / "note.md").write_text("# Note v2\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "edit: note.md"],
            capture_output=True,
            check=True,
        )
        return repo

    def test_vault_wide_history(self, tmp_path: Path) -> None:
        """get_file_history with path=None returns all vault commits."""
        repo = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        strategy._ensure_git_root(repo)
        entries = strategy.get_file_history(repo, path=None, since=None, limit=20)
        assert len(entries) == 2
        assert entries[0].message == "edit: note.md"
        assert entries[1].message == "write: note.md"
        assert len(entries[0].sha) == 40
        assert len(entries[0].short_sha) == 7

    def test_file_history(self, tmp_path: Path) -> None:
        """get_file_history with path filters to commits touching that file."""
        repo = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(
            repo, path=repo / "note.md", since=None, limit=20
        )
        assert len(entries) == 2
        assert all(e.message in {"write: note.md", "edit: note.md"} for e in entries)
        # paths_changed is empty for single-note queries
        assert entries[0].paths_changed == []

    def test_limit_is_respected(self, tmp_path: Path) -> None:
        """get_file_history respects the limit parameter."""
        repo = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(repo, path=None, since=None, limit=1)
        assert len(entries) == 1
        assert entries[0].message == "edit: note.md"

    def test_limit_capped_at_100(self, tmp_path: Path) -> None:
        """Limit is capped at 100 regardless of input."""
        repo = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        # Requesting 200 should not error; git returns at most 100 anyway.
        entries = strategy.get_file_history(repo, path=None, since=None, limit=200)
        assert isinstance(entries, list)

    def test_no_git_root_returns_empty(self, tmp_path: Path) -> None:
        """get_file_history returns empty list when not in a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(non_repo, path=None, since=None, limit=20)
        assert entries == []

    def test_history_entry_fields(self, tmp_path: Path) -> None:
        """HistoryEntry has all expected fields populated."""
        repo = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(repo, path=None, since=None, limit=1)
        entry = entries[0]
        assert entry.sha
        assert entry.short_sha
        assert entry.timestamp  # ISO 8601
        assert "Test" in entry.author
        assert entry.message


class TestGetFileDiff:
    """Tests for GitWriteStrategy.get_file_diff()."""

    def _make_repo_with_commits(self, tmp_path: Path) -> tuple[Path, str]:
        """Create a repo with two commits; return repo path and first commit SHA."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
            check=True,
        )
        (repo / "note.md").write_text("# Note v1\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "write: note.md"],
            capture_output=True,
            check=True,
        )
        first_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (repo / "note.md").write_text("# Note v2\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "edit: note.md"],
            capture_output=True,
            check=True,
        )
        return repo, first_sha

    def test_single_diff(self, tmp_path: Path) -> None:
        """get_file_diff with per_commit=False returns a unified diff string."""
        repo, first_sha = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        diff = strategy.get_file_diff(
            repo, repo / "note.md", first_sha, per_commit=False
        )
        assert isinstance(diff, str)
        assert "Note v1" in diff or "Note v2" in diff

    def test_per_commit_diff(self, tmp_path: Path) -> None:
        """get_file_diff with per_commit=True returns a list of CommitDiff."""
        from markdown_vault_mcp.types import CommitDiff

        repo, first_sha = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        diffs = strategy.get_file_diff(
            repo, repo / "note.md", first_sha, per_commit=True
        )
        assert isinstance(diffs, list)
        assert len(diffs) == 1
        assert isinstance(diffs[0], CommitDiff)
        assert diffs[0].message == "edit: note.md"
        assert diffs[0].diff

    def test_no_git_root_returns_empty(self, tmp_path: Path) -> None:
        """get_file_diff returns empty when not in a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        strategy = GitWriteStrategy()
        result = strategy.get_file_diff(
            non_repo, non_repo / "note.md", "abcd1234", per_commit=False
        )
        assert result == ""

    def test_invalid_ref_raises_value_error(self, tmp_path: Path) -> None:
        """get_file_diff raises ValueError for an unknown ref."""
        repo, _ = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        with pytest.raises(ValueError, match="not found in history"):
            strategy.get_file_diff(repo, repo / "note.md", "deadbeef", per_commit=False)

    def test_since_timestamp_single_diff(self, tmp_path: Path) -> None:
        """since_timestamp resolves to a commit SHA and returns a diff string."""
        repo, _ = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        # Get the first commit's ISO timestamp, then use it as `since_timestamp`.
        # rev-list --before=<first-commit-time> -1 HEAD returns the first commit.
        ts = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%aI", "--reverse", "-1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        diff = strategy.get_file_diff(
            repo,
            repo / "note.md",
            ref=None,
            per_commit=False,
            since_timestamp=ts,
        )
        assert isinstance(diff, str)

    def test_since_timestamp_no_commit_returns_empty(self, tmp_path: Path) -> None:
        """since_timestamp before all commits returns empty result."""
        repo, _ = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        result = strategy.get_file_diff(
            repo,
            repo / "note.md",
            ref=None,
            per_commit=False,
            since_timestamp="1970-01-01T00:00:00+00:00",
        )
        assert result == ""

    def test_since_timestamp_per_commit(self, tmp_path: Path) -> None:
        """since_timestamp with per_commit=True returns CommitDiff list."""
        from markdown_vault_mcp.types import CommitDiff

        repo, _ = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        # A far-future timestamp resolves to the latest commit; diff is empty list.
        diffs = strategy.get_file_diff(
            repo,
            repo / "note.md",
            ref=None,
            per_commit=True,
            since_timestamp="2099-01-01T00:00:00+00:00",
        )
        assert isinstance(diffs, list)
        assert all(isinstance(d, CommitDiff) for d in diffs)

    def test_per_commit_diff_no_leading_blank(self, tmp_path: Path) -> None:
        """per_commit diff entries must not start with a blank line."""
        repo, first_sha = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        diffs = strategy.get_file_diff(
            repo, repo / "note.md", first_sha, per_commit=True
        )
        assert isinstance(diffs, list)
        assert len(diffs) >= 1
        assert not diffs[0].diff.startswith("\n")

    def test_per_commit_diff_across_rename(self, tmp_path: Path) -> None:
        """Per-commit diffs are non-empty even for commits before a rename."""
        from markdown_vault_mcp.types import CommitDiff

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            capture_output=True,
            check=True,
        )
        # Commit 1: add note.md
        (repo / "note.md").write_text("# v1\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "add note"],
            capture_output=True,
            check=True,
        )
        first_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        # Commit 2: rename note.md -> renamed.md
        subprocess.run(
            ["git", "-C", str(repo), "mv", "note.md", "renamed.md"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "rename to renamed.md"],
            capture_output=True,
            check=True,
        )

        strategy = GitWriteStrategy()
        diffs = strategy.get_file_diff(
            repo, repo / "renamed.md", first_sha, per_commit=True
        )
        assert isinstance(diffs, list)
        assert len(diffs) == 1
        assert isinstance(diffs[0], CommitDiff)
        # The rename commit should produce a non-empty diff
        assert diffs[0].diff

    def test_git_log_failure_raises_value_error(self, tmp_path: Path) -> None:
        """get_file_history converts CalledProcessError to ValueError."""
        import unittest.mock as mock

        repo, _ = self._make_repo_with_commits(tmp_path)
        strategy = GitWriteStrategy()
        # Prime the git-root cache so _ensure_git_root doesn't call subprocess.
        strategy._ensure_git_root(repo)

        err = subprocess.CalledProcessError(
            128, ["git", "log"], stderr="fatal: bad date"
        )
        with (
            mock.patch.object(subprocess, "run", side_effect=err),
            pytest.raises(ValueError, match="git log failed"),
        ):
            strategy.get_file_history(repo, path=None, since=None, limit=20)


class TestGetFileHistoryVaultScope:
    """Tests that vault-wide history is scoped to repo_path."""

    def _make_repo_with_subdirectory_vault(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a repo where the vault is a subdirectory of the git root."""
        repo = tmp_path / "repo"
        repo.mkdir()
        vault = repo / "vault"
        vault.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
            check=True,
        )
        # Commit a file OUTSIDE the vault
        (repo / "outside.md").write_text("outside\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "outside commit"],
            capture_output=True,
            check=True,
        )
        # Commit a file INSIDE the vault
        (vault / "note.md").write_text("# Inside\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "vault commit"],
            capture_output=True,
            check=True,
        )
        return repo, vault

    def test_vault_wide_history_scoped_to_vault(self, tmp_path: Path) -> None:
        """Vault-wide history excludes commits that only touched files outside."""
        _, vault = self._make_repo_with_subdirectory_vault(tmp_path)
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(vault, path=None, since=None, limit=20)
        messages = [e.message for e in entries]
        assert "vault commit" in messages
        assert "outside commit" not in messages

    def test_vault_wide_paths_are_vault_relative(self, tmp_path: Path) -> None:
        """paths_changed entries are relative to the vault, not the git root."""
        _, vault = self._make_repo_with_subdirectory_vault(tmp_path)
        strategy = GitWriteStrategy()
        entries = strategy.get_file_history(vault, path=None, since=None, limit=20)
        assert len(entries) == 1
        assert entries[0].paths_changed == ["note.md"]


class TestCollectionGitHistoryMethods:
    """Tests for Collection.get_history / Collection.get_diff edge cases."""

    def _make_collection_no_git(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        """Return a Collection with no git strategy (plain directory vault)."""
        from markdown_vault_mcp.collection import Collection

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n")
        col = Collection(source_dir=vault, git_strategy=None)
        col.build_index()
        return col

    def _make_collection_with_git(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        """Return a Collection backed by a git repo with two commits."""
        from markdown_vault_mcp.collection import Collection

        vault = tmp_path / "vault"
        vault.mkdir()
        subprocess.run(
            ["git", "-C", str(vault), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.email", "t@t.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.name", "T"],
            capture_output=True,
            check=True,
        )
        (vault / "note.md").write_text("# v1\n")
        subprocess.run(
            ["git", "-C", str(vault), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "add note"],
            capture_output=True,
            check=True,
        )
        (vault / "note.md").write_text("# v2\n")
        subprocess.run(
            ["git", "-C", str(vault), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "update note"],
            capture_output=True,
            check=True,
        )
        strategy = GitWriteStrategy()
        col = Collection(source_dir=vault, git_strategy=strategy)
        col.build_index()
        return col, vault

    def test_get_history_no_git_strategy_returns_empty(self, tmp_path: Path) -> None:
        """get_history returns [] when _git_strategy is None."""
        col = self._make_collection_no_git(tmp_path)
        assert col.get_history() == []

    def test_get_diff_no_git_strategy_returns_empty_string(
        self, tmp_path: Path
    ) -> None:
        """get_diff returns '' when _git_strategy is None and per_commit=False."""
        col = self._make_collection_no_git(tmp_path)
        result = col.get_diff("note.md", since_sha="abcd1234")
        assert result == ""

    def test_get_diff_no_git_strategy_per_commit_returns_empty_list(
        self, tmp_path: Path
    ) -> None:
        """get_diff returns [] when _git_strategy is None and per_commit=True."""
        col = self._make_collection_no_git(tmp_path)
        result = col.get_diff("note.md", since_sha="abcd1234", per_commit=True)
        assert result == []

    def test_get_history_with_since_filter(self, tmp_path: Path) -> None:
        """get_history passes the since filter through to git log."""
        col, _ = self._make_collection_with_git(tmp_path)
        # A far-future date should still return results (all commits are before it).
        entries = col.get_history(since="2000-01-01")
        assert isinstance(entries, list)

    def test_get_file_diff_no_git_root_per_commit(self, tmp_path: Path) -> None:
        """get_file_diff returns [] for per_commit=True when no git root."""
        non_repo = tmp_path / "norepo"
        non_repo.mkdir()
        strategy = GitWriteStrategy()
        result = strategy.get_file_diff(
            non_repo, non_repo / "note.md", ref="abcd", per_commit=True
        )
        assert result == []

    # ------------------------------------------------------------------
    # `until` filter on get_history (issue #340)
    # ------------------------------------------------------------------

    def _make_collection_with_dated_commits(
        self,
        tmp_path: Path,
        dates: list[str],
    ):  # type: ignore[no-untyped-def]
        """Return a Collection where each commit has a pinned ISO author date.

        `GIT_AUTHOR_DATE` / `GIT_COMMITTER_DATE` are used so `--since` /
        `--until` boundary tests are deterministic, without sleeping.
        """
        from markdown_vault_mcp.collection import Collection

        vault = tmp_path / "vault_dated"
        vault.mkdir()
        subprocess.run(
            ["git", "-C", str(vault), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.email", "t@t.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.name", "T"],
            capture_output=True,
            check=True,
        )
        for i, iso_date in enumerate(dates):
            (vault / "note.md").write_text(f"# v{i}\n")
            subprocess.run(
                ["git", "-C", str(vault), "add", "."],
                capture_output=True,
                check=True,
            )
            env = {
                "GIT_AUTHOR_DATE": iso_date,
                "GIT_COMMITTER_DATE": iso_date,
                "GIT_AUTHOR_NAME": "T",
                "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "T",
                "GIT_COMMITTER_EMAIL": "t@t.com",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            }
            subprocess.run(
                ["git", "-C", str(vault), "commit", "-m", f"v{i}"],
                capture_output=True,
                check=True,
                env=env,
            )
        strategy = GitWriteStrategy()
        col = Collection(source_dir=vault, git_strategy=strategy)
        col.build_index()
        return col

    def test_get_history_with_until_filter(self, tmp_path: Path) -> None:
        """get_history passes `until` through to `git log --until`."""
        col = self._make_collection_with_dated_commits(
            tmp_path,
            dates=[
                "2026-01-01T12:00:00+0000",
                "2026-02-01T12:00:00+0000",
                "2026-03-01T12:00:00+0000",
            ],
        )
        # Cut off mid-February — should return Jan + (first) Feb but not Mar.
        entries = col.get_history(until="2026-02-15T00:00:00+0000")
        messages = [e.message for e in entries]
        assert messages == ["v1", "v0"], messages

    def test_get_history_with_since_and_until_window(self, tmp_path: Path) -> None:
        """`since` and `until` together bound the window."""
        col = self._make_collection_with_dated_commits(
            tmp_path,
            dates=[
                "2026-01-01T12:00:00+0000",
                "2026-02-01T12:00:00+0000",
                "2026-03-01T12:00:00+0000",
            ],
        )
        entries = col.get_history(
            since="2026-01-15T00:00:00+0000",
            until="2026-02-15T00:00:00+0000",
        )
        messages = [e.message for e in entries]
        assert messages == ["v1"], messages

    def test_get_history_until_no_match_returns_empty(self, tmp_path: Path) -> None:
        """`until` in the distant past returns an empty list."""
        col = self._make_collection_with_dated_commits(
            tmp_path,
            dates=["2026-01-01T00:00:00+0000"],
        )
        entries = col.get_history(until="2000-01-01T00:00:00+0000")
        assert entries == []

    def test_get_history_until_boundary_inclusive(self, tmp_path: Path) -> None:
        """A commit at exactly the `until` timestamp is included.

        Git's ``--until`` (a.k.a. ``--before``) semantics are inclusive at
        the boundary: a commit whose author date equals the cutoff is
        returned.  This test pins a single commit at a known instant and
        asserts that passing that exact instant as ``until`` still returns
        it (regression guard for the documented boundary behaviour).
        """
        col = self._make_collection_with_dated_commits(
            tmp_path,
            dates=["2026-02-01T12:00:00+0000"],
        )
        entries = col.get_history(until="2026-02-01T12:00:00+0000")
        assert len(entries) == 1, entries
        assert entries[0].message == "v0"

    # ------------------------------------------------------------------
    # `limit` on get_diff per_commit (issue #339)
    # ------------------------------------------------------------------

    def _make_collection_with_n_commits(self, tmp_path: Path, n: int):  # type: ignore[no-untyped-def]
        """Return (col, vault) with `n` commits touching note.md."""
        from markdown_vault_mcp.collection import Collection

        vault = tmp_path / f"vault_{n}"
        vault.mkdir()
        subprocess.run(
            ["git", "-C", str(vault), "init"], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.email", "t@t.com"],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(vault), "config", "user.name", "T"],
            capture_output=True,
            check=True,
        )
        for i in range(n):
            (vault / "note.md").write_text(f"# v{i}\n")
            subprocess.run(
                ["git", "-C", str(vault), "add", "."],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(vault), "commit", "-m", f"v{i}"],
                capture_output=True,
                check=True,
            )
        strategy = GitWriteStrategy()
        col = Collection(source_dir=vault, git_strategy=strategy)
        col.build_index()
        return col, vault

    def _oldest_sha(self, vault: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(vault), "log", "--format=%H", "--reverse"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().splitlines()[0]

    def test_get_diff_per_commit_respects_limit(self, tmp_path: Path) -> None:
        """get_diff(per_commit=True, limit=N) returns the N newest commits."""
        col, vault = self._make_collection_with_n_commits(tmp_path, 5)
        oldest = self._oldest_sha(vault)
        out = col.get_diff("note.md", since_sha=oldest, per_commit=True, limit=2)
        assert isinstance(out, list)
        assert len(out) == 2
        # Newest-first: the two most recent commit messages are v4 and v3.
        assert [c.message for c in out] == ["v4", "v3"]

    def test_get_diff_per_commit_limit_none_is_unlimited(self, tmp_path: Path) -> None:
        """limit=None (default) walks all intervening commits."""
        col, vault = self._make_collection_with_n_commits(tmp_path, 4)
        oldest = self._oldest_sha(vault)
        out = col.get_diff("note.md", since_sha=oldest, per_commit=True)
        assert isinstance(out, list)
        # oldest..HEAD spans 3 commits (the first is the `oldest` itself, excluded).
        assert len(out) == 3

    def test_get_diff_limit_ignored_when_not_per_commit(self, tmp_path: Path) -> None:
        """`limit` has no effect when per_commit=False (single unified diff)."""
        col, vault = self._make_collection_with_n_commits(tmp_path, 4)
        oldest = self._oldest_sha(vault)
        result = col.get_diff("note.md", since_sha=oldest, per_commit=False, limit=1)
        # Still a single unified-diff string.
        assert isinstance(result, str)
        assert "v3" in result  # last version ended up in the diff

    def test_get_diff_limit_clamped_low(self, tmp_path: Path) -> None:
        """limit <= 0 is clamped to 1."""
        col, vault = self._make_collection_with_n_commits(tmp_path, 3)
        oldest = self._oldest_sha(vault)
        out = col.get_diff("note.md", since_sha=oldest, per_commit=True, limit=0)
        assert isinstance(out, list)
        assert len(out) == 1

    def test_get_diff_limit_clamped_high(self, tmp_path: Path) -> None:
        """limit > 100 is clamped to 100 (matches get_history)."""
        col, vault = self._make_collection_with_n_commits(tmp_path, 3)
        oldest = self._oldest_sha(vault)
        # Only 2 intervening commits exist; clamp should not expand beyond reality.
        out = col.get_diff("note.md", since_sha=oldest, per_commit=True, limit=500)
        assert isinstance(out, list)
        assert len(out) == 2
