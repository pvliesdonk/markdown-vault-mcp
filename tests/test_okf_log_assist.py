"""Read-only, opt-in editorial log proposals."""

import subprocess
from pathlib import Path

import pytest
from fastmcp import Client

from markdown_vault_mcp.utils.fs import is_regular_file
from tests.server_factory import make_server


@pytest.mark.anyio
@pytest.mark.parametrize(
    "enabled,backend,visible",
    [
        (False, False, False),
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


def test_proposal_uses_scoped_diff_and_does_not_write(tmp_path: Path) -> None:
    """One backend call sees selected changes and returns its attribution."""
    from markdown_vault_mcp._okf_log_assist import (
        ProposalBackendError,
        propose_log_entry,
    )
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    class FailingSummarizer(FakeSummarizer):
        def summarize(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            raise RuntimeError("fixture failure")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

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
        assert not is_regular_file(tmp_path / "log.md")
        backend.text = "   "
        with pytest.raises(ProposalBackendError):
            propose_log_entry(
                vault, ProjectConfig(source_dir=tmp_path), ["a.md"], before
            )
        assert not is_regular_file(tmp_path / "log.md")
        vault._summarizer = FailingSummarizer()
        with pytest.raises(ProposalBackendError):
            propose_log_entry(
                vault, ProjectConfig(source_dir=tmp_path), ["a.md"], before
            )
        assert not is_regular_file(tmp_path / "log.md")
        vault._summarizer = backend
        backend.text = "Changed A"
        git("mv", "a.md", "b.md")
        git("commit", "-m", "rename")
        (tmp_path / "a.md").write_text("# Different occupant\nPrivate marker\n")
        git("add", "a.md")
        git("commit", "-m", "reuse name")
        propose_log_entry(vault, ProjectConfig(source_dir=tmp_path), ["b.md"], before)
        assert "+New" in backend.calls[-1][1]
        assert "Private marker" not in backend.calls[-1][1]
        propose_log_entry(vault, ProjectConfig(source_dir=tmp_path), ["a.md"], before)
        assert "+Private marker" in backend.calls[-1][1]
        assert "-Old" not in backend.calls[-1][1]
    finally:
        vault.close()


def test_invalid_second_path_never_reaches_backend(tmp_path: Path) -> None:
    """All paths are checked before the backend sees any content."""
    from markdown_vault_mcp._okf_log_assist import propose_log_entry
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.test_summarize import FakeSummarizer

    (tmp_path / "a.md").write_text("# A\n")
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    backend = FakeSummarizer()
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=True), summarizer=backend
    )
    try:
        with pytest.raises(InvalidRequestError):
            propose_log_entry(
                vault,
                ProjectConfig(source_dir=tmp_path),
                ["a.md", "../private.md"],
                "abcd",
            )
        assert backend.calls == []
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_proposal_and_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered tool returns a proposal and translates a backend failure."""
    from fastmcp.exceptions import ToolError

    from tests.test_summarize import FakeSummarizer

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "note.md").write_text("# Before\n")
    git("add", "note.md")
    git("commit", "-m", "before")
    before = git("rev-parse", "HEAD")
    (tmp_path / "note.md").write_text("# After\n")
    git("add", "note.md")
    git("commit", "-m", "after")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
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
        assert result.data == {
            "text": "Updated heading",
            "producer": "fake/gpt-5-mini",
            "basis": "diff",
        }
        fake.text = " "
        with pytest.raises(ToolError, match="could not produce"):
            await client.call_tool(
                "okf_propose_log_entry", {"paths": ["note.md"], "since": before}
            )
    assert not is_regular_file(tmp_path / "log.md")
