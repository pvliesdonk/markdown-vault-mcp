"""Regression coverage for independent OKF capability gates."""

from pathlib import Path

import pytest
from fastmcp import Client

from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server


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


@pytest.mark.parametrize("folder", ["a/b", "a/b/", "/a/b", "./a/b"])
def test_designated_folder_below_undesignated_parent(
    tmp_path: Path, folder: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "a" / "b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", folder)
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
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_mode="on", okf_log=log_mode).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("root.md", "# Root\n")
        wait_for_writer_drain(vault)
        assert (root / "log.md").exists() == (log_mode == "writes")
        if log_mode == "events":
            assert vault._okf_convention is None
    finally:
        vault.close()
    readonly = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=True, okf_mode="on", okf_capabilities=caps),
    )
    try:
        assert readonly._okf_convention is None
    finally:
        readonly.close()


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


@pytest.mark.parametrize(
    ("folder", "patterns", "expected"),
    [
        ("docs", ("docs/",), True),
        ("docs", ("./docs",), True),
        ("docs", ("/docs",), True),
        ("", ("./",), True),
        ("", ("/",), True),
        ("", ("**",), True),
        ("docs/a", ("docs",), False),
        (".hidden", ("./.hidden/",), True),
        ("", (".", "*"), True),
        ("a", (".", "*"), True),
        ("a/b", (".", "*"), False),
        ("a/b", (".", "*", "*/*"), True),
        ("a/b/c", (".", "*", "*/*"), False),
        ("a/b/c", ("**",), True),
        ("a", ("a/**",), False),
        ("a/b/c", ("a/**",), True),
        ("a/c", ("a/**/c",), True),
        ("a/b/c", ("a/**/c",), True),
        ("a/b/d", ("a/**/c",), False),
    ],
)
def test_designation_glob_depth(
    folder: str, patterns: tuple[str, ...], expected: bool
) -> None:
    from markdown_vault_mcp._okf_convention import folder_is_designated

    assert folder_is_designated(folder, patterns) is expected


@pytest.mark.anyio
async def test_legacy_alias_verifies_undeclared_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    async def accept(
        _message: str, _response_type: Any, _params: Any, _context: Any
    ) -> bool:
        return True

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    async with Client(
        make_server(), mode="legacy", elicitation_handler=accept
    ) as client:
        await client.call_tool("okf_verify", {"path": "note.md"})
    assert "verified:" in (root / "note.md").read_text()


def test_guidance_follows_independent_maintenance() -> None:
    from markdown_vault_mcp._instructions import GuidanceConfig, _domain_snippets
    from markdown_vault_mcp.config_sections.content import ContentConfig

    def guidance(**settings: object) -> str:
        caps = ContentConfig(**settings).okf_capabilities  # type: ignore[arg-type]
        cfg = GuidanceConfig(
            read_only=False,
            okf_mode="on",
            okf_write=caps.legacy_stamping,
            okf_capabilities=caps,
        )
        return " ".join(item.text for item in _domain_snippets(cfg))

    index_only = guidance(okf_index=True)
    assert "server maintains index.md; maintain log.md yourself" in index_only
    log_only = guidance(okf_log="writes")
    assert "server maintains log.md; maintain index.md yourself" in log_only
    assert "Leave 'log.md'/'index.md' to the server" in guidance(
        okf_index=True, okf_log="writes", okf_folders=("**",)
    )
    neither = guidance(okf_write=True, okf_index=False, okf_log="off")
    assert "update 'log.md'/'index.md'" in neither


def test_nested_authored_index_survives_write_and_rename(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    nested = root / "projects" / "alpha" / "private"
    nested.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    authored = "# Authored navigation\n"
    (nested / "index.md").write_text(authored)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "projects/*")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("projects/alpha/private/note.md", "# Note\n")
        vault.writer.rename(
            "projects/alpha/private/note.md", "projects/alpha/private/renamed.md"
        )
        wait_for_writer_drain(vault)
        assert (nested / "index.md").read_text() == authored
        vault.writer.move_folder("projects/alpha/private", "projects/alpha/moved")
        wait_for_writer_drain(vault)
        assert (root / "projects/alpha/moved/index.md").read_text() == authored
    finally:
        vault.close()


@pytest.mark.anyio
async def test_independent_review_is_retained_after_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    import frontmatter

    async def accept(
        _message: str, _response_type: Any, _params: Any, _context: Any
    ) -> bool:
        return True

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    note = root / "note.md"

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")
    async with Client(
        make_server(), mode="legacy", elicitation_handler=accept
    ) as client:
        await wait_for_mcp_writer_drain(client)
        assert "okf_verify" in [tool.name for tool in await client.list_tools()]
        await client.call_tool("write", {"path": "note.md", "content": "# Note\n"})
        await wait_for_mcp_writer_drain(client)
        assert note.read_text() == "# Note\n"
        await client.call_tool("okf_verify", {"path": "note.md"})
        reviewed = frontmatter.loads(note.read_text()).metadata
        assert reviewed.get("verified") and "generated" not in reviewed
        await client.call_tool(
            "edit", {"path": "note.md", "old_text": "# Note", "new_text": "# Edited"}
        )
        await wait_for_mcp_writer_drain(client)
    # B7 owns changing retention of a review after content changes.
    assert (
        frontmatter.loads(note.read_text()).metadata["verified"] == reviewed["verified"]
    )

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
    async with Client(make_server()) as client:
        await client.call_tool("okf_verify", {"path": "note.md"})
        await wait_for_mcp_writer_drain(client)
    assert any(
        entry["by"] == "human:peter"
        for entry in frontmatter.loads(note.read_text()).metadata["verified"]
    )

    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    caps = ContentConfig(okf_mode="on", okf_author=True).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("author-only.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (root / "author-only.md").read_text() == "# Note\n"
    finally:
        vault.close()


def test_server_instructions_use_resolved_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "off")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_FOLDERS", ".,projects/*")
    text = make_server().instructions or ""
    assert "projects/*" in text
