"""Overwrite recovery when Git emits non-text filter diagnostics."""

import subprocess
from pathlib import Path

from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.git import _run_git


def test_overwrite_keeps_breadcrumb_with_non_utf8_filter_diagnostic(
    git_repo: Path,
) -> None:
    _run_git(
        git_repo, "config", "filter.diag.clean", "sh -c 'printf \"\\377\" >&2; cat'"
    )
    (git_repo / ".gitattributes").write_text("*.md filter=diag\n")
    (git_repo / "note.md").write_text("# Original\n")
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "--", ".gitattributes", "note.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-qm", "before"],
        check=True,
        capture_output=True,
    )
    expected = _run_git(git_repo, "rev-parse", "HEAD").strip()
    vault = Vault(
        source_dir=git_repo,
        git_strategy=GitWriteStrategy(),
        settings=VaultSettings(read_only=False),
    )
    try:
        assert (
            vault.writer.write("note.md", "# Overwritten\n").previous_revision
            == expected
        )
    finally:
        vault.close()
