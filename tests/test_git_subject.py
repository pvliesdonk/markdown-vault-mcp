"""Synthetic coverage for opt-in automatic commit subjects."""

import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from markdown_vault_mcp import __version__
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import (
    _resolve_subject_writer,
    to_vault_instances,
)
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.git._subject import SubjectWriter, _clean
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.git import _run_git as git
from tests.test_summarize import FakeSummarizer, _install_fake_openai


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    return tmp_path


@pytest.mark.parametrize("operation", ["write", "rename", "delete", "surrogate"])
def test_commit_and_seed_log_preserve_attribution(repo: Path, operation: str) -> None:
    fake = FakeSummarizer(
        "broken\ud800text"
        if operation == "surrogate"
        else '  "Raise retention period."\nignored'
    )
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    old, note = repo / "old.md", repo / "note.md"
    try:
        if operation in {"rename", "delete"}:
            old.write_text("synthetic retention", encoding="utf-8")
            strategy(old, "", "write")
        if operation == "rename":
            old.rename(note)
            strategy(note, "", "rename", old_path=old)
            mechanical = "rename: old.md -> note.md"
        elif operation == "delete":
            old.unlink()
            strategy(old, "", "delete")
            mechanical = "delete: old.md"
        else:
            note.write_text("synthetic retention", encoding="utf-8")
            strategy(note, "", "write")
            mechanical = "write: note.md"
        subject = (
            mechanical
            if operation == "surrogate"
            else "[AI summary] Raise retention period"
        )
        message = (
            f"{subject}\n\n"
            if operation == "surrogate"
            else f"{subject}\n\nMechanical-Subject: {mechanical}\n\n"
        )
        assert git(repo, "log", "-1", "--format=%B") == message.rstrip() + (
            ("\n\n" if operation == "surrogate" else "\n")
            + f"Vault-Tool: {operation if operation != 'surrogate' else 'write'}\n"
            f"Vault-Server: markdown-vault-mcp/{__version__}\n\n"
        )
        assert (
            "rename from old.md" if operation == "rename" else "synthetic retention"
        ) in fake.calls[-1][1]
        vault = Vault(
            source_dir=repo,
            git_strategy=strategy,
            settings=VaultSettings(read_only=False),
        )
        try:
            assert vault.reader.get_history()[0].message == subject
            result = vault.writer.okf_seed_log()
            assert result.path == "log.md"
            assert f"**{subject}**" in (repo / "log.md").read_text(encoding="utf-8")
        finally:
            vault.close()
    finally:
        strategy.close()


def test_partial_commit_does_not_include_unrelated_staged_work(repo: Path) -> None:
    other = repo / "other.md"
    other.write_text("other", encoding="utf-8")
    git(repo, "add", "other.md")
    note = repo / "note.md"
    note.write_text("synthetic", encoding="utf-8")
    fake = FakeSummarizer("Add note")
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    strategy(note, "", "write")
    assert git(repo, "show", "--format=", "--name-only", "HEAD").strip() == "note.md"
    assert "other.md" not in fake.calls[0][1]
    assert git(repo, "diff", "--cached", "--name-only").strip() == "other.md"
    strategy.close()


def test_timeout_is_bounded_and_single_flight(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("markdown_vault_mcp.git._subject._DEADLINE", 0.05)
    entered, release = threading.Event(), threading.Event()

    class Blocked(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            entered.set()
            release.wait(2)
            return "Add note"

    fake = Blocked()
    writer = SubjectWriter(fake)
    note = repo / "note.md"
    note.write_text("synthetic", encoding="utf-8")
    git(repo, "add", "note.md")
    start = time.monotonic()
    try:
        assert writer(str(repo), "write: note.md", ["note.md"]) == "write: note.md"
        assert time.monotonic() - start < 1
        assert entered.is_set()
        assert writer(str(repo), "write: note.md", ["note.md"]) == "write: note.md"
        assert len(fake.calls) == 1
    finally:
        release.set()
    for _ in range(100):
        if not writer._flight.locked():
            break
        time.sleep(0.01)
    assert writer(str(repo), "write: note.md", ["note.md"]).startswith("[AI summary]")


@pytest.mark.parametrize(
    "value,expected",
    [
        (" \n `Hello world.` ", "Hello world"),
        ("\x01\x7f", ""),
        ("x" * 200, "x" * 87),
        ("hello\u0081world", "helloworld"),
        ("hello\u200bworld", "helloworld"),
        ("[AI summary] [ai SUMMARY] Update note", "Update note"),
        ("[Ai summary]", ""),
        ("[AI [AI summary] summary] x", "x"),
        ("[AI  [ai summary]summary] y", "y"),
        ("[[AI summary]AI summary] z", "z"),
    ],
)
def test_clean(value: str, expected: str) -> None:
    assert _clean(value) == expected
    assert _clean(_clean(value)) == expected


@pytest.mark.parametrize(
    "case", ["exception", "multiline", "large", "empty_diff", "empty_reply"]
)
def test_fallbacks_and_no_sensitive_logs(
    repo: Path, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    note = repo / "note.md"
    fake = FakeSummarizer("" if case == "empty_reply" else "Valid subject")
    if case != "empty_diff":
        note.write_text(
            "x" * 17000 if case == "large" else "secret note", encoding="utf-8"
        )
        git(repo, "add", "note.md")

    class Broken(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            raise RuntimeError(f"secret provider body {len(system) + len(user)}")

    writer = SubjectWriter(Broken() if case == "exception" else fake)
    mechanical = "write:\nbody" if case == "multiline" else "write: note.md"
    with caplog.at_level(logging.DEBUG):
        assert writer(str(repo), mechanical, ["note.md"]) == mechanical
    assert "secret" not in caplog.text
    if case in {"empty_diff", "large", "multiline"}:
        assert fake.calls == []
    if case == "empty_reply":
        assert len(fake.calls) == 1


def test_batch_fallback_and_no_diff(repo: Path) -> None:
    fake = FakeSummarizer("Update notes")
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    first, second = repo / "a.md", repo / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    try:
        strategy.on_write_batch(
            [(first, "write", None, None), (second, "write", None, None)], "convert"
        )
        assert (
            git(repo, "log", "-1", "--format=%B")
            == "[AI summary] Update notes\n\nMechanical-Subject: convert: 2 files\n"
            f"Vault-Tool: convert\nVault-Server: markdown-vault-mcp/{__version__}\n\n"
        )
        assert set(
            git(repo, "show", "--format=", "--name-only", "HEAD").splitlines()
        ) == {"a.md", "b.md"}
        first.write_text("changed", encoding="utf-8")
        strategy.on_write_batch([(first, "write", None, None)], "convert")
        assert (
            git(repo, "log", "-1", "--format=%B")
            == "[AI summary] Update notes\n\nMechanical-Subject: write: a.md\n"
            f"Vault-Tool: convert\nVault-Server: markdown-vault-mcp/{__version__}\n\n"
        )
        assert len(fake.calls) == 2
    finally:
        strategy.close()


@pytest.mark.parametrize("mode", ["commit", "token", "managed"])
def test_enabled_backend_builds_private_client(
    mode: str,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert _resolve_subject_writer(ProjectConfig(), FakeSummarizer()) is None
    with caplog.at_level(logging.WARNING):
        assert (
            _resolve_subject_writer(
                ProjectConfig(git_generate_commit_subjects=True), None
            )
            is None
        )
    for text in (
        "MARKDOWN_VAULT_MCP_GIT_GENERATE_COMMIT_SUBJECTS",
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        "mechanical",
    ):
        assert text in caplog.text
    captured = _install_fake_openai(monkeypatch, raise_error="api")
    factory = MagicMock()
    monkeypatch.setattr("markdown_vault_mcp.git.GitWriteStrategy", factory)
    kwargs = (
        {"git_token": "fake"}
        if mode == "token"
        else {"git_repo_url": "https://example.invalid/repo"}
        if mode == "managed"
        else {}
    )
    config = ProjectConfig(
        source_dir=repo,
        git_generate_commit_subjects=True,
        summarize_openai_api_key="fake",
        **kwargs,
    )
    instances = to_vault_instances(config)
    writer = factory.call_args.kwargs["subject_writer"]
    assert instances.git_strategy is factory.return_value
    assert isinstance(writer, SubjectWriter)
    assert captured["timeout"] == 4.0
    assert captured["max_retries"] == 0
    note = repo / "privacy.md"
    note.write_text("synthetic", encoding="utf-8")
    git(repo, "add", "privacy.md")
    with caplog.at_level(logging.DEBUG):
        assert (
            writer(str(repo), "write: privacy.md", ["privacy.md"])
            == "write: privacy.md"
        )
    assert "upstream boom" not in caplog.text


@pytest.mark.parametrize("case", ["worktree", "recreated", "restaged"])
def test_changed_worktree_during_generation_falls_back(repo: Path, case: str) -> None:
    note = repo / "race.md"
    note.write_text("original synthetic content", encoding="utf-8")

    class Editor(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            if case != "recreated" or len(self.calls) == 2:
                note.write_text("later synthetic content", encoding="utf-8")
                if case == "restaged":
                    git(repo, "add", "race.md")
            return "Describe original"

    fake = Editor()
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    try:
        if case == "recreated":
            strategy(note, "", "write")
            note.unlink()
        operation = "delete" if case == "recreated" else "write"
        strategy(note, "", operation)
        assert git(repo, "show", "HEAD:race.md") == "later synthetic content"
        assert git(repo, "log", "-1", "--format=%s").strip() == f"{operation}: race.md"
    finally:
        strategy.close()
