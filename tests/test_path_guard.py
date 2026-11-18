"""Protected paths apply to direct and client-requested secondary writes."""

from pathlib import Path

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_bulk_retry_reaches_later_unprotected_notes(tmp_path: Path) -> None:
    for name in ("a.md", "policy.md", "y.md"):
        (tmp_path / name).write_text("[[z]]")
    (tmp_path / "z.md").write_text("# z")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["policy.md"]),
    )
    try:
        vault.index.build_index()
        for count in (2, 0):
            with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS") as exc:
                vault.writer.okf_convert_links()
            assert f"Converted {count} notes" in str(exc.value)
            assert "policy.md" in str(exc.value)
            assert (tmp_path / "a.md").read_text() == "[z](/z.md)"
            assert (tmp_path / "y.md").read_text() == "[z](/z.md)"
        assert (tmp_path / "policy.md").read_text() == "[[z]]"
    finally:
        vault.close()


def test_protected_backlink_is_not_rewritten(tmp_path: Path) -> None:
    (tmp_path / "target.md").write_text("target")
    linker = tmp_path / "policy.md"
    linker.write_text("[target](target.md)")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["policy.md"]),
    )
    try:
        vault.index.build_index()
        result = vault.writer.rename("target.md", "new.md", update_links=True)
        assert linker.read_text() == "[target](target.md)"
        assert result.hint and "PROTECTED_PATHS" in result.hint
    finally:
        vault.close()


@pytest.mark.parametrize(
    "operation",
    ["write", "edit", "append", "delete", "rename_from", "rename_to", "attachment"],
)
def test_direct_mutations_and_override(tmp_path: Path, operation: str) -> None:
    (tmp_path / "guard.md").write_text("original")
    (tmp_path / "other.md").write_text("other")
    (tmp_path / "guard.png").write_bytes(b"original")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["guard.*"]),
    )
    try:

        def run(allow: bool) -> object:
            actions = {
                "write": lambda: vault.writer.write(
                    "guard.md", "changed", allow_protected=allow
                ),
                "edit": lambda: vault.writer.edit(
                    "guard.md", "original", "changed", allow_protected=allow
                ),
                "append": lambda: vault.writer.append(
                    "guard.md", "changed", allow_protected=allow
                ),
                "delete": lambda: vault.writer.delete(
                    "guard.md", allow_protected=allow
                ),
                "rename_from": lambda: vault.writer.rename(
                    "guard.md", "new.md", allow_protected=allow
                ),
                "rename_to": lambda: vault.writer.rename(
                    "other.md", "guard.md", allow_protected=allow
                ),
                "attachment": lambda: vault.writer.write_attachment(
                    "guard.png", b"changed", allow_protected=allow
                ),
            }
            return actions[operation]()

        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            run(False)
        assert (tmp_path / "guard.md").read_text() == "original"
        if operation == "rename_to":
            (tmp_path / "guard.md").unlink()
        run(True)
        if operation == "delete":
            assert not (tmp_path / "guard.md").exists()
        elif operation == "rename_from":
            assert (tmp_path / "new.md").read_text() == "original"
        elif operation == "rename_to":
            assert (tmp_path / "guard.md").read_text() == "other"
        elif operation == "attachment":
            assert (tmp_path / "guard.png").read_bytes() == b"changed"
        else:
            assert "changed" in (tmp_path / "guard.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("pattern", "collision"),
    [
        ("from/two.md", False),
        ("to/two.md", False),
        ("from", False),
        ("to", False),
        ("to/two.md", True),
    ],
)
def test_folder_endpoints_and_files(
    tmp_path: Path, pattern: str, collision: bool
) -> None:
    (tmp_path / "from").mkdir()
    (tmp_path / "from/two.md").write_text("original")
    if collision:
        (tmp_path / "to").mkdir()
        (tmp_path / "to/two.md").write_text("existing")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=[pattern]),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.move_folder("from", "to")
        assert (tmp_path / "from/two.md").read_text() == "original"
        if collision:
            assert (tmp_path / "to/two.md").read_text() == "existing"
        else:
            vault.writer.move_folder("from", "to", allow_protected=True)
            assert (tmp_path / "to/two.md").read_text() == "original"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        ("private", "private/a.md"),
        ("private/", "private/a.md"),
        ("private/**", "private/a.md"),
        ("_CONVENTIONS.md", "_conventions.md"),
        ("café.md", "cafe\u0301.md"),
        ("dir/./x.md", "dir/x.md"),
        ("dir//x.md", "dir/x.md"),
    ],
)
def test_matching_patterns(tmp_path: Path, pattern: str, path: str) -> None:
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=[pattern]),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.write(path, "changed")
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("target", "pattern"), [("target.md", "target.md"), ("TARGET.md", "target.md")]
)
def test_symlink_alias_protected(tmp_path: Path, target: str, pattern: str) -> None:
    (tmp_path / target).write_text("original")
    (tmp_path / "alias.md").symlink_to(target)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=[pattern]),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.write("alias.md", "changed")
        assert (tmp_path / target).read_text() == "original"
    finally:
        vault.close()


@pytest.mark.parametrize("folder_move", [False, True])
@pytest.mark.parametrize("allow", [False, True])
def test_backlink_external_change_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder_move: bool, allow: bool
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/target.md").write_text("target")
    link = tmp_path / "policy.md"
    link.write_text("[target](src/target.md)")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["policy.md"]),
    )
    try:
        vault.index.build_index()
        rewrite = vault._doc_mgr._rewrite_link_sources

        def external_edit(*args: object, **kwargs: object) -> object:
            link.write_text("external [target](src/target.md)")
            return rewrite(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "_rewrite_link_sources", external_edit)
        if folder_move:
            result = vault.writer.move_folder("src", "dest", allow_protected=allow)
        else:
            result = vault.writer.rename(
                "src/target.md",
                "dest/target.md",
                update_links=True,
                allow_protected=allow,
            )
        assert (tmp_path / "dest/target.md").exists()
        if allow:
            assert "dest/target.md" in link.read_text()
            assert result.hint is None
        else:
            assert link.read_text() == "external [target](src/target.md)"
            assert result.hint and "PROTECTED_PATHS" in result.hint
            if folder_move:
                assert "policy.md" in result.failed_links
    finally:
        vault.close()


def test_maintenance_respects_protection(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_write import okf_write_suppressed

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "log.md").write_text("original log")
    (tmp_path / "private").mkdir()
    (tmp_path / "private/note.md").write_text("private")
    original = (tmp_path / "index.md").read_bytes()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_write=True,
            protected_paths=["index.md", "log.md", "private"],
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.write("new.md", "# New")
        assert (tmp_path / "new.md").exists()
        assert (tmp_path / "index.md").read_bytes() == original
        assert (tmp_path / "log.md").read_text() == "original log"
        assert not (tmp_path / "private/index.md").exists()
        with (
            okf_write_suppressed(),
            pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"),
        ):
            vault.writer.write("log.md", "client")
    finally:
        vault.close()


def test_index_generation_reports_skips_and_progress(tmp_path: Path) -> None:
    for folder in ("a", "z"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "note.md").write_text("# Note")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["a/index.md"]),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS") as exc:
            vault.writer.okf_generate_index()
        assert "a/index.md" in str(exc.value) and "z/index.md" in str(exc.value)
        assert not (tmp_path / "a/index.md").exists()
        assert (tmp_path / "z/index.md").exists()
        assert "/a/index.md" not in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


async def test_mcp_read_and_conventions_remain_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _clear_env: None
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    (tmp_path / "_conventions.md").write_text("Follow these rules")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROTECTED_PATHS", "_conventions.md")
    async with Client(make_server()) as client:
        assert "Follow these rules" in str(
            (await client.call_tool("get_conventions", {})).structured_content
        )
        assert "Follow these rules" in str(
            (
                await client.call_tool("read", {"path": "_conventions.md"})
            ).structured_content
        )
        with pytest.raises(ToolError, match="PROTECTED_PATHS"):
            await client.call_tool(
                "write", {"path": "_conventions.md", "content": "changed"}
            )


def test_protected_parent_index_after_children(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "child/note.md").write_text("# Note")
    (tmp_path / "index.md").write_text("original")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["index.md"]),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS") as exc:
            vault.writer.okf_generate_index()
        assert "child/index.md" in str(exc.value)
        assert (tmp_path / "index.md").read_text() == "original"
        assert (tmp_path / "child/index.md").exists()
    finally:
        vault.close()
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        assert "/child/index.md" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_seed_protected_log(tmp_path: Path) -> None:
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["log.md"]),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.okf_seed_log()
        assert not (tmp_path / "log.md").exists()
    finally:
        vault.close()


def test_protected_note_with_no_convertible_links_does_not_block(
    tmp_path: Path,
) -> None:
    (tmp_path / "policy.md").write_text("no links")
    (tmp_path / "a.md").write_text("[[z]]")
    (tmp_path / "z.md").write_text("# z")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["policy.md"]),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_convert_links()
        assert (tmp_path / "a.md").read_text() == "[z](/z.md)"
        assert (tmp_path / "policy.md").read_text() == "no links"
    finally:
        vault.close()


def test_generated_child_over_read_cap_does_not_abort_parent(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "child/note.md").write_text("# A")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, max_note_read_bytes=10),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        assert "/child/index.md" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize("operation", ["convert", "index", "log"])
def test_okf_library_override(tmp_path: Path, operation: str) -> None:
    (tmp_path / "note.md").write_text("[[target]]")
    (tmp_path / "target.md").write_text("# target")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, protected_paths=["note.md", "index.md", "log.md"]
        ),
    )
    try:
        vault.index.build_index()
        if operation == "convert":
            vault.writer.okf_convert_links(allow_protected=True)
            assert (tmp_path / "note.md").read_text() == "[target](/target.md)"
        elif operation == "index":
            vault.writer.okf_generate_index(allow_protected=True)
            assert (tmp_path / "index.md").exists()
        else:
            vault.writer.okf_seed_log(allow_protected=True)
            assert (tmp_path / "log.md").exists()
    finally:
        vault.close()


async def test_okf_tool_schemas_do_not_expose_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _clear_env: None
) -> None:
    from fastmcp import Client

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as client:
        tools = await client.list_tools()
    for tool in tools:
        if tool.name in {"okf_convert_links", "okf_generate_index", "okf_seed_log"}:
            assert "allow_protected" not in tool.input_schema.get("properties", {})


def test_folder_hint_does_not_blame_protection_under_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["sub"]),
    )
    try:
        monkeypatch.setattr(vault._doc_mgr, "in_submodule", lambda _path: True)
        assert (
            vault._doc_mgr._submodule_links_hint(
                ["sub/a.md"], allow_protected=True, include_submodules=False
            )
            is None
        )
        hint = vault._doc_mgr._submodule_links_hint(["sub/a.md"], allow_protected=True)
        assert hint and "submodule" in hint and "PROTECTED_PATHS" not in hint
    finally:
        vault.close()


def test_empty_policy_folder_move_has_no_submodule_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/note.md").write_text("# note")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        monkeypatch.setattr(
            vault._doc_mgr,
            "_rewrite_link_sources",
            lambda *_args, **_kwargs: ([], [], ["sub/link.md"]),
        )
        monkeypatch.setattr(vault._doc_mgr, "in_submodule", lambda _path: True)
        result = vault.writer.move_folder("src", "dst")
        assert result.failed_links == ["sub/link.md"]
        assert result.hint is None
    finally:
        vault.close()


def test_glob_double_star_requires_folder(tmp_path: Path) -> None:
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, protected_paths=["**/_conventions.md"]),
    )
    try:
        vault.writer.write("_conventions.md", "root allowed")
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.write("folder/_conventions.md", "refused")
    finally:
        vault.close()
