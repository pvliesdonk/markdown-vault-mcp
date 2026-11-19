"""Regression coverage for independent OKF capability gates."""

from pathlib import Path

import pytest
from fastmcp import Client

from tests.conftest import wait_for_mcp_writer_drain
from tests.fixtures.review_binding import review_args
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
            await client.call_tool("okf_verify", review_args("note.md"))
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
        ("cafe\u0301", ("café",), True),
        ("café", ("cafe\u0301",), True),
        ("a\u0301\u0323", ("a[\u0301]\u0323",), False),
        ("a\u0301\u0323", ("a?\u0323",), False),
        ("a\u0301\u0323", ("\u1ea1\u0301",), True),
        ("\u1ea1\u0301", ("a\u0301\u0323",), True),
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
        await client.call_tool("okf_verify", review_args("note.md"))
    assert "verified:" in (root / "note.md").read_text()


def test_guidance_follows_independent_maintenance() -> None:
    from markdown_vault_mcp._instructions import GuidanceConfig, _domain_snippets
    from markdown_vault_mcp.config_sections.content import ContentConfig

    def guidance(**settings: object) -> str:
        caps = ContentConfig(**settings).okf_capabilities  # type: ignore[arg-type]
        cfg = GuidanceConfig(
            read_only=False,
            okf_mode="on",
            okf_write=False,
            okf_capabilities=caps,
        )
        return " ".join(item.text for item in _domain_snippets(cfg))

    index_only = guidance(okf_index=True)
    assert "maintains designated 'index.md'" in index_only
    assert "update 'log.md'" in index_only
    log_only = guidance(okf_log="writes")
    assert "Leave 'log.md' to the server" in log_only
    assert "update 'index.md'" in log_only
    both = guidance(okf_index=True, okf_log="writes", okf_folders=(".", "projects/*"))
    assert "Leave 'log.md' to the server" in both
    assert "maintains designated 'index.md'" in both
    assert "(., projects/*)" in both
    neither = guidance(okf_write=True, okf_index=False, okf_log="off")
    assert "update 'log.md'/'index.md'" in neither


@pytest.mark.parametrize(
    ("index", "log"), [(True, "off"), (False, "writes"), (True, "writes")]
)
def test_read_only_guidance_does_not_claim_upkeep(index: bool, log: str) -> None:
    from markdown_vault_mcp._instructions import GuidanceConfig, _domain_snippets
    from markdown_vault_mcp.config_sections.content import ContentConfig

    caps = ContentConfig(
        okf_mode="on", okf_index=index, okf_log=log, okf_folders=("**",)
    ).okf_capabilities
    config = GuidanceConfig(read_only=True, okf_mode="on", okf_capabilities=caps)
    text = " ".join(snippet.text for snippet in _domain_snippets(config))
    assert "server maintains" not in text
    assert "to the server" not in text
    assert "maintain both" not in text
    assert "maintain index.md yourself" not in text
    assert "maintain log.md yourself" not in text
    assert "update 'log.md'/'index.md'" not in text
    assert "weigh notes by trust tier" in text
    assert "may predate the current text" in text


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
        await client.call_tool("okf_verify", review_args("note.md"))
        reviewed = frontmatter.loads(note.read_text()).metadata
        assert reviewed.get("verified") and "generated" not in reviewed
        await client.call_tool(
            "edit", {"path": "note.md", "old_text": "# Note", "new_text": "# Edited"}
        )
        await wait_for_mcp_writer_drain(client)
    # Independent review retains the dated claim after content changes.
    assert (
        frontmatter.loads(note.read_text()).metadata["verified"] == reviewed["verified"]
    )

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
    async with Client(make_server()) as client:
        await client.call_tool("okf_verify", review_args("note.md"))
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


@pytest.mark.anyio
async def test_review_refuses_when_bundle_deactivated_during_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from fastmcp.exceptions import ToolError

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    note = root / "note.md"
    note.write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")

    async def accept(
        _message: str, _response_type: Any, _params: Any, _context: Any
    ) -> bool:
        index.write_text("# Declaration removed\n")
        return True

    async with Client(
        make_server(), mode="legacy", elicitation_handler=accept
    ) as client:
        with pytest.raises(ToolError, match="OKF is not active"):
            await client.call_tool("okf_verify", review_args("note.md"))
    assert note.read_text() == "# Note\n"


@pytest.mark.parametrize("operation", ["write", "edit", "append", "delete", "rename"])
@pytest.mark.parametrize("alias", ["dotdot", "symlink"])
def test_aliased_note_operations_preserve_undesignated_index(
    tmp_path: Path, operation: str, alias: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "team" / "links").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "team" / "links" / "private").symlink_to(
        root / "private", target_is_directory=True
    )
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    index = root / "private" / "index.md"
    original = "# Private authored navigation\n"
    index.write_text(original)
    (root / "private" / "one.md").write_text("# One\n")
    prefix = "team/../private" if alias == "dotdot" else "team/links/private"
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team/*/*",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        path = f"{prefix}/one.md"
        if operation == "write":
            vault.writer.write(f"{prefix}/new.md", "# New\n")
        elif operation == "edit":
            vault.writer.edit(path, old_text="One", new_text="Edited")
        elif operation == "append":
            vault.writer.append(path, "More\n")
        elif operation == "delete":
            vault.writer.delete(path)
        else:
            vault.writer.rename(path, f"{prefix}/two.md")
        wait_for_writer_drain(vault)
        note = root / "private" / "one.md"
        if operation == "write":
            assert (root / "private" / "new.md").read_text() == "# New\n"
        elif operation == "edit":
            assert note.read_text() == "# Edited\n"
        elif operation == "append":
            assert "More" in note.read_text()
        elif operation == "delete":
            assert not note.exists()
        else:
            assert not note.exists()
            assert (root / "private" / "two.md").read_text() == "# One\n"
        assert index.read_text() == original
        assert not (root / "private" / "log.md").exists()
    finally:
        vault.close()


def test_dot_path_maintains_designated_folder(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "team").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("./team/one.md", "# One\n")
        wait_for_writer_drain(vault)
        assert (root / "team" / "index.md").exists()
        assert (root / "team" / "log.md").exists()
    finally:
        vault.close()


@pytest.mark.parametrize("alias", ["dotdot", "symlink"])
def test_aliased_folder_move_preserves_undesignated_index(
    tmp_path: Path, alias: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "team" / "links").mkdir(parents=True)
    (root / "private" / "old").mkdir(parents=True)
    (root / "team" / "links" / "private").symlink_to(
        root / "private", target_is_directory=True
    )
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    original = "# Private authored navigation\n"
    (root / "private" / "old" / "index.md").write_text(original)
    (root / "private" / "old" / "one.md").write_text("# One\n")
    prefix = "team/../private" if alias == "dotdot" else "team/links/private"
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team/*/*/*",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.move_folder(f"{prefix}/old", f"{prefix}/new")
        wait_for_writer_drain(vault)
        assert (root / "private" / "new" / "index.md").read_text() == original
        assert not (root / "private" / "new" / "log.md").exists()
    finally:
        vault.close()


@pytest.mark.parametrize("reserved", ["index.md", "log.md"])
def test_reserved_file_symlink_write_does_not_trigger_maintenance(
    tmp_path: Path, reserved: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    folder = root / "team"
    folder.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (folder / "index.md").write_text("# Original index\n")
    (folder / "log.md").write_text("# Original log\n")
    (folder / "alias.md").symlink_to(folder / reserved)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write(
            "team/alias.md",
            "# Manual reserved content\n",
            allow_protected=reserved == "index.md",
        )
        wait_for_writer_drain(vault)
        assert (folder / reserved).read_text() == "# Manual reserved content\n"
        other = "log.md" if reserved == "index.md" else "index.md"
        assert (folder / other).read_text() == (
            "# Original log\n" if other == "log.md" else "# Original index\n"
        )
    finally:
        vault.close()


def test_rename_from_reserved_file_symlink_does_not_trigger_maintenance(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    folder = root / "team"
    folder.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (folder / "index.md").write_text("# Authored index\n")
    (folder / "log.md").write_text("# Authored log\n")
    (folder / "alias.md").symlink_to(folder / "index.md")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.rename("team/alias.md", "team/renamed.md", allow_protected=True)
        wait_for_writer_drain(vault)
        assert (folder / "renamed.md").read_text() == "# Authored index\n"
        assert not (folder / "index.md").exists()
        assert (folder / "log.md").read_text() == "# Authored log\n"
    finally:
        vault.close()


@pytest.mark.parametrize("reserved", ["index.md", "log.md"])
@pytest.mark.parametrize("operation", ["write", "edit", "append"])
def test_reserved_spelling_symlink_to_concept_preserves_primary_write(
    tmp_path: Path, reserved: str, operation: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    folder = root / "team"
    folder.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    note = folder / "concept.md"
    note.write_text("# Before\n")
    (folder / reserved).symlink_to(note)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="writes", okf_folders=("team",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        path = f"team/{reserved}"
        if operation == "write":
            vault.writer.write(path, "# User content\n")
        elif operation == "edit":
            vault.writer.edit(path, old_text="Before", new_text="Edited")
        else:
            vault.writer.append(path, "More\n")
        wait_for_writer_drain(vault)
        assert (
            note.read_text()
            == {
                "write": "# User content\n",
                "edit": "# Edited\n",
                "append": "# Before\nMore\n",
            }[operation]
        )
        other = "log.md" if reserved == "index.md" else "index.md"
        assert not (folder / other).exists()
    finally:
        vault.close()


@pytest.mark.parametrize("reserved", ["index.md", "log.md"])
@pytest.mark.parametrize(
    "target",
    [
        "private/note.md",
        "team/concept.md",
        "private/missing.md",
        "team/missing.md",
        "private/reserved",
        "private/missing-reserved",
        "team/sub/reserved",
        "team/sub/missing-reserved",
        "outside/note.md",
        "outside/missing.md",
    ],
)
def test_automatic_reserved_symlink_does_not_modify_concept(
    tmp_path: Path, reserved: str, target: str
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "team" / "sub").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "outside").mkdir()
    missing = "missing" in target
    base = tmp_path if target.startswith("outside/") else root
    destination = base / (
        Path(target).parent / reserved if target.endswith("reserved") else target
    )
    if not missing:
        destination.write_text("# Protected\n")
    (root / "team" / reserved).symlink_to(destination)
    caps = ContentConfig(
        okf_mode="on",
        okf_index=reserved == "index.md",
        okf_log="writes" if reserved == "log.md" else "off",
        okf_folders=("team", "team/sub"),
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("team/new.md", "# New\n")
        wait_for_writer_drain(vault)
        assert (root / "team" / "new.md").read_text() == "# New\n"
        if missing:
            assert not destination.exists()
        else:
            assert destination.read_text() == "# Protected\n"
    finally:
        vault.close()


@pytest.mark.parametrize("missing", [False, True])
def test_recursive_index_generation_does_not_modify_symlinked_concept(
    tmp_path: Path, missing: bool
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings
    from tests.conftest import wait_for_writer_drain

    root = tmp_path / "vault"
    (root / "team" / "sub").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "team" / "sub" / "one.md").write_text("# One\n")
    destination = root / "private" / "protected.md"
    if not missing:
        destination.write_text("# Protected\n")
    (root / "team" / "sub" / "index.md").symlink_to(destination)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("team", "team/sub")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        vault.writer.write("team/new.md", "# New\n")
        wait_for_writer_drain(vault)
        assert (root / "team" / "new.md").read_text() == "# New\n"
        if missing:
            assert not destination.exists()
        else:
            assert destination.read_text() == "# Protected\n"
    finally:
        vault.close()
