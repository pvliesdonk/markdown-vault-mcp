"""Read-only, opt-in editorial log proposals."""

import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

import pytest
from fastmcp import Client

from tests.server_factory import make_server


class GitRunner(Protocol):
    def __call__(self, *args: str, date: str | None = None) -> str: ...


@pytest.fixture
def git(tmp_path: Path) -> GitRunner:
    """Run Git in the synthetic vault, optionally fixing commit dates."""

    def run(*args: str, date: str | None = None) -> str:
        env = dict(os.environ)
        if date is not None:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    return run


@pytest.mark.anyio
@pytest.mark.parametrize(
    "enabled,backend,visible",
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
async def test_proposal_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    backend: bool,
    visible: bool,
) -> None:
    """A read-only server exposes proposals only with consent and a backend."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG_ASSIST", str(enabled).lower())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", raising=False)
    if backend:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://localhost:11434/v1"
        )
    async with Client(make_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert ("okf_propose_log_entry" in tools) is visible
    if visible:
        assert tools["okf_propose_log_entry"].annotations.read_only_hint is True


def test_proposal_output_validation(tmp_path: Path, git: GitRunner) -> None:
    """Valid output retains attribution; blank and unsafe output are refused."""
    from markdown_vault_mcp._okf_log_assist import (
        ProposalBackendError,
        propose_log_entry,
    )
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "a.md").write_text("# A\nOld\n")
    git("add", "a.md")
    git("commit", "-m", "before")
    before = git("rev-parse", "HEAD")
    (tmp_path / "a.md").write_text("# A\nNew\n")
    git("add", "a.md")
    git("commit", "-m", "after")
    backend = FakeSummarizer("Changed A")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=backend,
    )
    try:
        result = propose_log_entry(
            vault,
            ProjectConfig(source_dir=tmp_path, summarize_openai_model="fixture-model"),
            ["a.md"],
            before,
        )
        assert result == {
            "text": "Changed A",
            "producer": "fake/fixture-model",
            "basis": "diff",
        }
        assert len(backend.calls) == 1
        assert "+New" in backend.calls[0][1]
        for text in (" ", "Bad \ud800 proposal"):
            backend.text = text
            with pytest.raises(ProposalBackendError, match="single-line"):
                propose_log_entry(
                    vault, ProjectConfig(source_dir=tmp_path), ["a.md"], before
                )
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_proposal_and_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: GitRunner
) -> None:
    """The registered tool returns a proposal and translates a backend failure."""
    from fastmcp.exceptions import ToolError

    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    log_path = tmp_path / "log.md"
    log_path.write_bytes(b"# Existing log\n\nEarlier entry\n")
    before_log = log_path.read_bytes()
    (tmp_path / "note.md").write_text("# Before\n")
    git("add", "note.md")
    git("commit", "-m", "before")
    before = git("rev-parse", "HEAD")
    (tmp_path / "note.md").write_text("# After\n")
    git("add", "note.md")
    git("commit", "-m", "after")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "events")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG_ASSIST", "true")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://localhost:11434/v1"
    )
    fake = FakeSummarizer("Updated heading")
    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", lambda _: fake)
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_propose_log_entry", {"paths": ["note.md"], "since": before}
        )
        assert result.data["text"] == "Updated heading"
        assert "+# After" in fake.calls[0][1]

        def fail(system: str, user: str) -> str:
            fake.calls.append((system, user))
            raise RuntimeError("fixture failure")

        monkeypatch.setattr(fake, "summarize", fail)
        with pytest.raises(ToolError, match="could not produce"):
            await client.call_tool(
                "okf_propose_log_entry", {"paths": ["note.md"], "since": before}
            )
        assert log_path.read_bytes() == before_log

    def unavailable(_: object) -> None:
        raise RuntimeError("fixture unavailable")

    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", unavailable)
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match=r"backend is unavailable.*operator"):
            await client.call_tool(
                "okf_propose_log_entry", {"paths": ["note.md"], "since": before}
            )


@pytest.mark.parametrize("since", ["", "  ", "HEAD~1", "2026-99-99", "not a date"])
def test_invalid_since_is_refused_before_backend(tmp_path: Path, since: str) -> None:
    """Unsupported date and revision input cannot reach the backend."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    (tmp_path / "note.md").write_text("# Note\n")
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    fake = FakeSummarizer()
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=True), summarizer=fake
    )
    try:
        with pytest.raises(InvalidRequestError, match="ISO 8601"):
            propose_log_entry(
                vault, ProjectConfig(source_dir=tmp_path), ["note.md"], since
            )
        assert fake.calls == []
    finally:
        vault.close()


def test_default_selection_requires_shared_commit_and_clean_notes(
    tmp_path: Path, git: GitRunner
) -> None:
    """Group a creation and edit, refusing unrelated commits and pending edits."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "a.md").write_text("# Old\n")
    git("add", "a.md")
    git("commit", "-m", "first")
    initial = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "a.md").write_text("# Updated\n")
    (tmp_path / "b.md").write_text("# Created\n")
    git("add", "a.md", "b.md")
    git("commit", "-m", "together")
    fake = FakeSummarizer("Grouped change")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    config = ProjectConfig(source_dir=tmp_path)
    try:
        propose_log_entry(vault, config, ["a.md", "b.md"], None)
        assert "+# Updated" in fake.calls[-1][1]
        assert "+# Created" in fake.calls[-1][1]
        (tmp_path / "a.md").write_text("# Pending\n")
        with pytest.raises(InvalidRequestError, match="uncommitted"):
            propose_log_entry(vault, config, ["a.md"], None)
        assert len(fake.calls) == 1
        git("add", "a.md")
        git("commit", "-m", "separate")
        with pytest.raises(InvalidRequestError, match="different commit"):
            propose_log_entry(vault, config, ["a.md", "b.md"], None)
        assert len(fake.calls) == 1
        propose_log_entry(vault, config, ["a.md", "b.md"], initial)
        assert "+# Pending" in fake.calls[-1][1]
        assert "+# Created" in fake.calls[-1][1]
    finally:
        vault.close()


def test_selection_and_input_limits(tmp_path: Path, git: GitRunner) -> None:
    """An ISO timestamp selects the diff; invalid scopes never reach the model."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "note.md").write_text("# Old\n")
    git("add", "note.md")
    git("commit", "-m", "before", date="2020-01-01T00:00:00+00:00")
    (tmp_path / "note.md").write_text("# New\n")
    git("add", "note.md")
    git("commit", "-m", "after", date="2020-01-02T00:00:00+00:00")
    fake = FakeSummarizer("Changed")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True, summarize_max_notes=2),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    config = ProjectConfig(source_dir=tmp_path)
    try:
        (tmp_path / "untracked.md").write_text("# Untracked\n")
        for paths, message in (
            ([], "at least one"),
            (["note.md", "note.md", "note.md"], "Too many paths"),
            (["note.md", "../private.md"], "Path traversal"),
            (["log.md"], "reserved files"),
            (["missing.md"], "not found"),
            (["bad\n.md"], "control or line separator"),
            (["untracked.md"], "no committed history"),
        ):
            with pytest.raises(InvalidRequestError, match=message):
                propose_log_entry(vault, config, paths, None)
        assert fake.calls == []
        limited = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(read_only=True, summarize_max_input_chars=5),
            git_strategy=GitWriteStrategy(),
            summarizer=fake,
        )
        try:
            with pytest.raises(InvalidRequestError, match="input limit"):
                propose_log_entry(limited, config, ["note.md"], "2020-01-01")
            assert fake.calls == []
        finally:
            limited.close()
        (tmp_path / "note.md").write_text("# New\n" + "A" * 60000 + "\n")
        git("add", "note.md")
        git("commit", "-m", "large", date="2020-01-03T00:00:00+00:00")
        with pytest.raises(InvalidRequestError, match="diff is too large"):
            propose_log_entry(vault, config, ["note.md"], "2020-01-02T00:00:00+00:00")
        assert fake.calls == []
    finally:
        vault.close()


def test_timestamp_before_history_includes_selected_current_notes(
    tmp_path: Path, git: GitRunner
) -> None:
    """An early timestamp includes current selected content across commits."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "a.md").write_text("# Initial A\n")
    (tmp_path / "b.md").write_text("# Initial B\n")
    git("add", "a.md", "b.md")
    git("commit", "-m", "initial", date="2020-01-01T00:00:00+00:00")
    (tmp_path / "a.md").write_text("# Later A\n")
    (tmp_path / "b.md").write_text("# Later B\n")
    git("add", "a.md", "b.md")
    git("commit", "-m", "later", date="2020-01-02T00:00:00+00:00")
    fake = FakeSummarizer("Selected changes")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    try:
        propose_log_entry(
            vault,
            ProjectConfig(source_dir=tmp_path),
            ["a.md", "b.md"],
            "2019-01-01T00:00:00+00:00",
        )
        assert len(fake.calls) == 1
        diff = fake.calls[0][1]
        expected = "Later"
        assert f"+# {expected} A" in diff
        assert f"+# {expected} B" in diff
    finally:
        vault.close()


@pytest.mark.parametrize("kind", ["empty", "rename", "binary"])
def test_empty_change_is_omitted_from_mixed_selection(
    tmp_path: Path, kind: str, git: GitRunner
) -> None:
    """Only the edited note is submitted when a selected note has no content diff."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "edit.md").write_text("Before\n")
    if kind == "rename":
        (tmp_path / "old.md").write_text("Unchanged content\n")
    elif kind == "binary":
        (tmp_path / "empty.md").write_bytes(b"old\0content\n")
    git("add", ".")
    git("commit", "-m", "before")
    before = git("rev-parse", "HEAD")
    (tmp_path / "edit.md").write_text("After\n")
    if kind == "rename":
        git("mv", "old.md", "empty.md")
    else:
        (tmp_path / "empty.md").write_bytes(
            b"new\0content\n" if kind == "binary" else b""
        )
    git("add", ".")
    git("commit", "-m", "change")
    fake = FakeSummarizer()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    try:
        config = ProjectConfig(source_dir=tmp_path)
        if kind != "empty":
            with pytest.raises(InvalidRequestError, match="No committed text changes"):
                propose_log_entry(vault, config, ["empty.md"], before)
            assert fake.calls == []
        paths = ["empty.md", "edit.md"]
        propose_log_entry(vault, config, paths, before if kind != "empty" else None)
        content = fake.calls[0][1]
        assert "+After" in content
        assert "empty.md" not in content
        assert not content.startswith("\n\n") and not content.endswith("\n\n\n")
    finally:
        vault.close()


def test_large_multibyte_creation_and_timeout(tmp_path: Path, git: GitRunner) -> None:
    """The byte limit applies to creation diffs and timeout advice survives."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError, SummarizeTimeoutError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    class TimeoutSummarizer(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            raise SummarizeTimeoutError("Narrow the request — fewer paths.")

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "large.md").write_text("é" * 26000 + "\n")
    (tmp_path / "small.md").write_text("Small\n")
    git("add", ".")
    git("commit", "-m", "initial")
    fake = TimeoutSummarizer()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True, summarize_max_input_chars=100000),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    config = ProjectConfig(source_dir=tmp_path)
    try:
        with pytest.raises(SummarizeTimeoutError, match="fewer paths"):
            propose_log_entry(vault, config, ["small.md"], None)
        with pytest.raises(InvalidRequestError, match="diff is too large"):
            propose_log_entry(vault, config, ["large.md"], None)
        assert len(fake.calls) == 1
    finally:
        vault.close()


def test_date_since_uses_a_fixed_utc_instant(
    tmp_path: Path,
    git: GitRunner,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Dates and equivalent timestamps select the same committed range."""

    def restore_timezone() -> None:
        monkeypatch.undo()
        time.tzset()

    request.addfinalizer(restore_timezone)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "note.md").write_text("Before\n")
    git("add", "note.md")
    git("commit", "-m", "before", date="2026-11-10T10:00:00+00:00")
    (tmp_path / "note.md").write_text("After\n")
    git("add", "note.md")
    git("commit", "-m", "after", date="2026-11-15T00:00:01+00:00")
    fake = FakeSummarizer()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    try:
        config = ProjectConfig(source_dir=tmp_path)
        for since in (
            "2026-11-15",
            "2026-11-15T00:00:00Z",
            "2026-11-15T00:00:00+00:00",
            "2026-11-15T00:00:00",
        ):
            propose_log_entry(vault, config, ["note.md"], since)
            assert "+After" in fake.calls[-1][1]
        for since in ("2026-11-15T02:00:01+02:00", "2026-11-15T00:00:01"):
            with pytest.raises(InvalidRequestError, match="No committed text changes"):
                propose_log_entry(vault, config, ["note.md"], since)
        assert len(fake.calls) == 4
    finally:
        vault.close()


@pytest.mark.parametrize("kind", ["nul", "utf16", "frontmatter", "empty"])
@pytest.mark.parametrize("since", [None, "2000-01-01"])
def test_creation_basis_preserves_readable_content(
    tmp_path: Path, kind: str, since: str | None, git: GitRunner
) -> None:
    """Readable creation snapshots supply their content; unreadable ones fail."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import (
        DocumentUnreadableError,
        InvalidRequestError,
    )
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    content = {
        "nul": b"new\0content\n",
        "utf16": "new\n".encode("utf-16"),
        "frontmatter": b"---\nbroken: [\n---\nCreated content\n",
        "empty": b"",
    }
    (tmp_path / "binary.md").write_bytes(content[kind])
    git("add", ".")
    git("commit", "-m", "initial")
    fake = FakeSummarizer()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    try:
        if kind == "empty":
            with pytest.raises(InvalidRequestError, match="No committed text changes"):
                propose_log_entry(
                    vault, ProjectConfig(source_dir=tmp_path), ["binary.md"], since
                )
            assert fake.calls == []
        elif kind == "utf16":
            with pytest.raises(DocumentUnreadableError):
                propose_log_entry(
                    vault, ProjectConfig(source_dir=tmp_path), ["binary.md"], since
                )
            assert fake.calls == []
        else:
            propose_log_entry(
                vault, ProjectConfig(source_dir=tmp_path), ["binary.md"], since
            )
            assert len(fake.calls) == 1
            expected = "+new\0content" if kind == "nul" else "+Created content"
            assert expected in fake.calls[0][1]
            assert "Binary files" not in fake.calls[0][1]
    finally:
        vault.close()


def test_producer_must_fit_recording_contract(tmp_path: Path, git: GitRunner) -> None:
    """Return truthful attribution only when it fits the recording limit."""
    from markdown_vault_mcp._okf_log_assist import (
        ProposalBackendError,
        propose_log_entry,
    )
    from markdown_vault_mcp._okf_log_event import _validate_input
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "note.md").write_text("Created content\n")
    git("add", "note.md")
    git("commit", "-m", "create")
    fake = FakeSummarizer("Created note")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=True),
        git_strategy=GitWriteStrategy(),
        summarizer=fake,
    )
    try:
        model = "m" * 95
        result = propose_log_entry(
            vault,
            ProjectConfig(source_dir=tmp_path, summarize_openai_model=model),
            ["note.md"],
            None,
        )
        assert result["producer"] == f"fake/{model}"
        assert len(result["producer"]) == 100
        _validate_input(["note.md"], result["text"], result["producer"])
        with pytest.raises(InvalidRequestError, match="at most 100"):
            _validate_input(["note.md"], result["text"], result["producer"] + "m")
        with pytest.raises(ProposalBackendError):
            propose_log_entry(
                vault,
                ProjectConfig(source_dir=tmp_path, summarize_openai_model=model + "m"),
                ["note.md"],
                None,
            )
        for unsafe_model in ("bad\nmodel", "bad\ud800model"):
            with pytest.raises(ProposalBackendError, match="single-line"):
                propose_log_entry(
                    vault,
                    ProjectConfig(
                        source_dir=tmp_path, summarize_openai_model=unsafe_model
                    ),
                    ["note.md"],
                    None,
                )
        assert len(fake.calls) == 1
    finally:
        vault.close()


@pytest.mark.anyio
async def test_reviewed_proposal_is_recorded_with_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: GitRunner
) -> None:
    """Reviewed text records its producer without losing the existing log."""
    from tests.test_summarize import FakeSummarizer

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "note.md").write_text("# Created\n")
    log_path = tmp_path / "log.md"
    log_path.write_bytes(b"# Existing log\n\nEarlier entry\n")
    git("add", "index.md", "note.md", "log.md")
    git("commit", "-m", "create")
    before = log_path.read_bytes()
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "events")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG_ASSIST", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL", "fixture-model")
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://localhost:11434/v1"
    )
    fake = FakeSummarizer("Suggested explanation")
    monkeypatch.setattr("markdown_vault_mcp.summarizer.get_summarizer", lambda _: fake)
    async with Client(make_server()) as client:
        proposal = await client.call_tool(
            "okf_propose_log_entry", {"paths": ["note.md"]}
        )
        assert proposal.data["text"] == "Suggested explanation"
        assert proposal.data["producer"] == "fake/fixture-model"
        assert log_path.read_bytes() == before
        reviewed = "Author-reviewed explanation"
        result = await client.call_tool(
            "okf_log_event",
            {
                "paths": ["note.md"],
                "description": reviewed,
                "proposed_by": proposal.data["producer"],
            },
        )
        assert result.data["path"] == "log.md"
    log = log_path.read_text()
    assert reviewed in log
    assert log.rstrip().endswith("(proposed by fake/fixture-model)")
