"""Regression coverage for independent OKF capability gates."""

from pathlib import Path

import pytest
from fastmcp import Client

from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server


@pytest.mark.anyio
async def test_verify_alone_exposes_tool_without_stamping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")
    async with Client(make_server()) as client:
        assert "okf_verify" in [tool.name for tool in await client.list_tools()]
        await wait_for_mcp_writer_drain(client)
        await client.call_tool("write", {"path": "note.md", "content": "# Note\n"})
        await wait_for_mcp_writer_drain(client)
    assert (root / "note.md").read_text() == "# Note\n"


def test_index_only_respects_designated_folders(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "guides").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "other" / "old.md").write_text("# Old\n")
    caps = ContentConfig(okf_mode="on", okf_index=True, okf_log="off").okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("root-note.md", "# Root note\n")
        vault.writer.write("guides/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert not (root / "guides" / "index.md").exists()
        assert not (root / "other" / "index.md").exists()
        assert not (root / "log.md").exists()
        assert not (root / "guides" / "log.md").exists()
        assert "root-note.md" in (root / "index.md").read_text()
        assert "/guides/index.md" not in (root / "index.md").read_text()
    finally:
        vault.close()


def test_designated_child_and_existing_undesignated_index(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "a" / "b").mkdir(parents=True)
    (root / "existing").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    original = "# Authored index\n"
    (root / "existing" / "index.md").write_text(original)
    (root / "existing" / "note.md").write_text("# Existing\n")
    (root / "a" / "b" / "note.md").write_text("# Nested\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "a")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("root.md", "# Root note\n")
        wait_for_writer_drain(vault)
        assert (root / "a" / "index.md").exists()
        assert not (root / "a" / "b" / "index.md").exists()
        assert "/a/b/index.md" not in (root / "a" / "index.md").read_text()
        assert "/existing/index.md" in (root / "index.md").read_text()
        assert (root / "existing" / "index.md").read_text() == original
    finally:
        vault.close()


def test_designated_folder_below_undesignated_parent(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "a" / "b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "a/b")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("a/b/note.md", "# Nested\n")
        wait_for_writer_drain(vault)
        assert (root / "a" / "b" / "index.md").exists()
        assert not (root / "a" / "index.md").exists()
        assert "/a/index.md" not in (root / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.anyio
async def test_verify_refuses_inactive_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp.exceptions import ToolError

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="OKF is not active"):
            await client.call_tool("okf_verify", {"path": "note.md"})
    assert note.read_text() == "# Note\n"


@pytest.mark.parametrize("log_mode", ["off", "events", "writes"])
def test_log_authority_for_server_writes(tmp_path: Path, log_mode: str) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "sub").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_mode="on", okf_log=log_mode).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("root.md", "# Root\n")
        vault.writer.write("sub/note.md", "# Nested\n")
        wait_for_writer_drain(vault)
        assert (root / "log.md").exists() == (log_mode == "writes")
        assert not (root / "sub" / "log.md").exists()
    finally:
        vault.close()


def test_author_alone_does_not_stamp(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_mode="on", okf_author=True).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (root / "note.md").read_text() == "# Note\n"
        assert not (root / "log.md").exists()
    finally:
        vault.close()


def test_explicit_generate_index_retains_recursive_behavior(tmp_path: Path) -> None:
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "note.md").write_text("# Note\n")
    vault = Vault(
        source_dir=root, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        assert (root / "a" / "index.md").exists()
        assert (root / "a" / "b" / "index.md").exists()
    finally:
        vault.close()
