"""Synthetic coverage for opt-in automatic commit subjects."""

import logging
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import _resolve_subject_writer
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.git._subject import SubjectWriter, _clean
from markdown_vault_mcp.okf import build_log_markdown
from markdown_vault_mcp.summarizer import OpenAISummarizer
from tests.test_summarize import FakeSummarizer, _install_fake_openai


def test_subject_generation_is_disabled_by_default() -> None:
    assert ProjectConfig().git_generate_commit_subjects is False


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    return tmp_path


def test_commit_and_seed_log_preserve_attribution(repo: Path) -> None:
    fake = FakeSummarizer('  "Raise retention period."\nignored')
    writer = SubjectWriter(fake)
    strategy = GitWriteStrategy(
        enable_pull=False, enable_push=False, git_lfs=False, subject_writer=writer
    )
    note = repo / "note.md"
    note.write_text("synthetic retention", encoding="utf-8")
    strategy(note, "", "write")
    message = git(repo, "log", "-1", "--format=%B")
    assert (
        message
        == "[AI summary] Raise retention period\n\nMechanical-Subject: write: note.md\n\n"
    )
    assert "synthetic retention" in fake.calls[0][1]
    assert "write: note.md" not in fake.calls[0][1]
    subject = git(repo, "log", "-1", "--format=%s").strip()
    body, _, _ = build_log_markdown(
        [SimpleNamespace(timestamp="2026-01-01", message=subject, short_sha="1234567")]
    )
    assert "**[AI summary] Raise retention period**" in body
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
    [(" \n `Hello world.` ", "Hello world"), ("\x01\x7f", ""), ("x" * 200, "x" * 87)],
)
def test_clean(value: str, expected: str) -> None:
    assert _clean(value) == expected


def test_fallbacks_and_no_sensitive_logs(
    repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    note = repo / "note.md"
    note.write_text("secret note", encoding="utf-8")
    git(repo, "add", "note.md")

    class Broken(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            raise RuntimeError(f"secret provider body {len(system) + len(user)}")

    writer = SubjectWriter(Broken())
    with caplog.at_level(logging.DEBUG):
        assert writer(str(repo), "write: note.md", ["note.md"]) == "write: note.md"
        assert writer(str(repo), "write:\nbody", ["note.md"]) == "write:\nbody"
    assert "secret" not in caplog.text
    note.write_text("x" * 17000, encoding="utf-8")
    git(repo, "add", "note.md")
    assert writer(str(repo), "write: note.md", ["note.md"]) == "write: note.md"


def test_backend_and_private_provider_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert _resolve_subject_writer(ProjectConfig(), None) is None
    assert (
        _resolve_subject_writer(ProjectConfig(git_generate_commit_subjects=True), None)
        is None
    )
    captured = _install_fake_openai(monkeypatch, raise_error="api")
    client = OpenAISummarizer(
        "fake", "fake", max_tokens=512, timeout=4, max_retries=0, private_logging=True
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        client.summarize("system", "synthetic")
    assert captured["max_retries"] == 0
    assert "upstream boom" not in caplog.text


def test_batch_fallback_and_no_diff(repo: Path) -> None:
    fake = FakeSummarizer("  ")
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    first, second = repo / "a.md", repo / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    items = [(first, "write", None, None), (second, "write", None, None)]
    strategy.on_write_batch(items, "convert")
    assert git(repo, "log", "-1", "--format=%B") == "convert: 2 files\n\n"
    assert set(git(repo, "show", "--format=", "--name-only", "HEAD").splitlines()) == {
        "a.md",
        "b.md",
    }
    strategy.on_write_batch(items, "convert")
    assert len(fake.calls) == 1
    strategy.close()


def test_rename_delete_and_mechanical_trailer(repo: Path) -> None:
    fake = FakeSummarizer("Update notes")
    strategy = GitWriteStrategy(
        enable_pull=False,
        enable_push=False,
        git_lfs=False,
        subject_writer=SubjectWriter(fake),
    )
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("synthetic", encoding="utf-8")
    strategy(old, "", "write")
    old.rename(new)
    strategy(new, "", "rename", old_path=old)
    assert "Mechanical-Subject: rename:" in git(repo, "log", "-1", "--format=%B")
    new.unlink()
    strategy(new, "", "delete")
    assert "Mechanical-Subject: delete: new.md" in git(repo, "log", "-1", "--format=%B")
    strategy.close()


def test_enabled_backend_builds_private_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_fake_openai(monkeypatch)
    config = ProjectConfig(
        git_generate_commit_subjects=True, summarize_openai_api_key="fake"
    )
    assert _resolve_subject_writer(config, FakeSummarizer()) is not None
    assert captured["timeout"] == 4.0
    assert captured["max_retries"] == 0


def test_private_refusal_does_not_log_model_prose(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_fake_openai(monkeypatch, refusal="private refusal text")
    client = OpenAISummarizer("fake", "fake", max_tokens=512, private_logging=True)
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        client.summarize("system", "synthetic")
    assert "private refusal text" not in caplog.text


def test_thread_start_failure_releases_flight(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = repo / "note.md"
    note.write_text("synthetic", encoding="utf-8")
    git(repo, "add", "note.md")
    writer = SubjectWriter(FakeSummarizer())

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("failed to start")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    assert writer(str(repo), "write: note.md", ["note.md"]) == "write: note.md"
    assert not writer._flight.locked()
