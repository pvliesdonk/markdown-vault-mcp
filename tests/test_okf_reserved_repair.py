"""The deliberate reserved-file placeholder migration."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp._okf_reserved_repair import ReservedFrontmatterRepair, _strip
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.exceptions import InvalidRequestError, ReadOnlyError
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_preview_and_apply_including_excluded_notes(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    root = tmp_path / "index.md"
    root.write_text(
        '---\nokf_version: "0.2"\ntitle: Index\ntype: null\nconflict_with: null\ncustom: null\n---\n# Index\n'
    )
    log = tmp_path / "guides" / "log.md"
    log.write_text("---\ntitle: Log\ntype: null\n---\n# Log\n")
    edited = tmp_path / "guides" / "index.md"
    edited.write_text("---\ntitle: Hand edited\ntype: null\n---\n# Guide\n")
    before = [p.read_bytes() for p in (root, log, edited)]
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            required_frontmatter=["title", "type", "conflict_with"],
            exclude_patterns=["guides/**"],
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        preview = repair.run()
        assert {x.path: x.keys for x in preview} == {
            "index.md": ("title", "type"),
            "guides/index.md": ("type",),
            "guides/log.md": ("title", "type"),
        }
        assert [p.read_bytes() for p in (root, log, edited)] == before
        assert repair.run(apply=True) == preview
        assert (
            root.read_text()
            == '---\nokf_version: "0.2"\nconflict_with: null\ncustom: null\n---\n# Index\n'
        )
        assert log.read_text() == "# Log\n"
        assert edited.read_text() == "---\ntitle: Hand edited\n---\n# Guide\n"
    finally:
        vault.close()


def test_read_only_allows_preview_but_refuses_apply(tmp_path: Path) -> None:
    (tmp_path / "log.md").write_text("---\ntitle: Log\n---\n# Log\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=True, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run()[0].keys == ("title",)
        with pytest.raises(ReadOnlyError):
            repair.run(apply=True)
    finally:
        vault.close()


def test_surgical_splice_preserves_crlf_and_comments() -> None:
    original = "---\r\ntitle: Log\r\ntype: null # user comment\r\nother: value\r\n---\r\n# Log\r\n"
    changed, keys = _strip(original, ["title"], "title", "Log")
    assert keys == ("title",)
    assert changed == original.replace("title: Log\r\n", "")


def test_cli_dry_run_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    log = root / "log.md"
    log.write_text("---\ntitle: Log\ntype: null\n---\n# Log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title,type")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    runner = CliRunner()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert result.exit_code == 0, result.output
    assert (
        "Would remove log.md: title" in result.stdout
        and "Would remove log.md: type" in result.stdout
    )
    assert "title: Log" in log.read_text()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Removed log.md: title" in result.stdout
    assert log.read_text() == "# Log\n"
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert "Nothing to repair" in result.stdout
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert "Nothing to repair" in result.stdout


def test_apply_designated_index_preserves_other_bytes(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf_capabilities import OkfCapabilities

    path = tmp_path / "index.md"
    original = b'---\r\nokf_version: "0.2"\r\ntitle: Index\r\ncustom: "preserve" # keep\r\n---\r\n# Index\r\n\r\nmanual content\r\n'
    path.write_bytes(original)
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        required_frontmatter=["title"],
        protected_paths=["index.md"],
        okf_capabilities=OkfCapabilities(
            author=False, verify="off", index=True, log="off", folders=(".",)
        ),
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run(apply=True, allow_protected=True)[0].keys == ("title",)
        assert path.read_bytes() == original.replace(b"title: Index\r\n", b"")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index.md"]
    finally:
        vault.close()


def test_custom_title_and_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "guides").mkdir()
    path = tmp_path / "guides" / "index.md"
    path.write_text("---\nname: guides\ntitle: null\n---\nbody\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            required_frontmatter=["name", "title"],
            title_field="name",
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run()[0].keys == ("name", "title")
        write = vault._doc_mgr.write

        def concurrent(*args: object, **kwargs: object) -> object:
            path.write_text("new content\n")
            return write(*args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "write", concurrent)
        assert repair.run(apply=True)[0].skip_reason
        assert path.read_text() == "new content\n"
    finally:
        vault.close()


def test_ambiguous_yaml_is_left_unchanged() -> None:
    original = "---\ntitle: Log\ntitle: Log\ntype: null\n---\nbody\n"
    with pytest.raises(InvalidRequestError, match="manual"):
        _strip(original, ["title", "type"], "title", "Log")


def test_empty_header_removed_and_audit_passes(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf import audit_bundle

    folder = tmp_path / "guides"
    folder.mkdir()
    path = folder / "index.md"
    path.write_bytes(b"\xef\xbb\xbf---\ntitle: guides\ntype: null\n---\n\n# guides\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title", "type"]
        ),
    )
    try:
        assert audit_bundle(tmp_path).index_frontmatter.count == 1
        assert ReservedFrontmatterRepair(vault).run(apply=True)
        assert path.read_bytes() == b"\xef\xbb\xbf\n# guides\n"
        assert audit_bundle(tmp_path).index_frontmatter.count == 0
    finally:
        vault.close()


@pytest.mark.parametrize("title", ["R&D", "Hello # World", "*star*"])
def test_literal_yaml_characters_in_title_are_removed(title: str) -> None:
    import yaml

    encoded = yaml.safe_dump({"title": title}, allow_unicode=True)
    original = f"---\n{encoded}---\n# Body\n"
    assert _strip(original, ["title"], "title", title) == ("# Body\n", ("title",))


def test_comment_survives_when_last_key_removed() -> None:
    original = "---\n# Keep this\ntitle: Log\n---\n# Body\n"
    assert _strip(original, ["title"], "title", "Log") == (
        "---\n# Keep this\n---\n# Body\n",
        ("title",),
    )


def test_apply_uses_exact_byte_write_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "log.md"
    path.write_bytes(b"---\r\ntitle: Log\r\nother: value\r\n---\r\n# Body\r\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        write = vault._doc_mgr.write

        def check(*args: object, **kwargs: object) -> object:
            assert vault._doc_mgr._exact_bytes
            return write(*args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "write", check)
        ReservedFrontmatterRepair(vault).run(apply=True)
        assert path.read_bytes() == b"---\r\nother: value\r\n---\r\n# Body\r\n"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "header", ["{title: Log}", "title: Log\ntitle: Log", "title: Log # review"]
)
def test_ambiguous_candidates_are_reported(tmp_path: Path, header: str) -> None:
    path = tmp_path / "log.md"
    path.write_text(f"---\n{header}\n---\n# Body\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        item = ReservedFrontmatterRepair(vault).run()[0]
        assert (
            item.path == "log.md" and item.skip_reason and "manual" in item.skip_reason
        )
    finally:
        vault.close()


def test_large_reserved_file_remains_eligible(tmp_path: Path) -> None:
    path = tmp_path / "log.md"
    body = "# Log\n" + "x" * 70000
    path.write_text("---\ntitle: Log\n---\n" + body)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        assert ReservedFrontmatterRepair(vault).run(apply=True)[0].keys == ("title",)
        assert path.read_text() == body
    finally:
        vault.close()


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_uninspectable_reserved_path_is_reported(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "log.md"
    if kind == "symlink":
        path.symlink_to("missing.md")
    else:
        path.mkdir()
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        item = ReservedFrontmatterRepair(vault).run()[0]
        assert item.path == "log.md" and item.skip_reason
    finally:
        vault.close()


def test_cli_read_only_reports_nontransactional_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "log.md").write_text("---\ntitle: Log\n---\nbody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 1
    assert "no rollback" not in result.stderr and "read-only" in result.stderr
    assert "Earlier files" not in result.stderr
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")
    result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 1 and "ERROR:" in result.stderr
    assert "no rollback" not in result.stderr


def test_batch_reports_skips_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    good = tmp_path / "a" / "log.md"
    unsafe = tmp_path / "b" / "log.md"
    good.write_text("---\ntitle: Log\n---\n# Log\n")
    unsafe.write_text("---\ntitle: Log # keep\n---\n# Log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    runner = CliRunner()
    for options, word in [([], "Would remove"), (["--apply"], "Removed")]:
        result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", *options])
        assert result.exit_code == 1
        assert f"{word} a/log.md: title" in result.stdout
        assert "Skipped b/log.md:" in result.stdout and "manual" in result.stdout
    assert good.read_text() == "# Log\n"
    assert unsafe.read_text() == "---\ntitle: Log # keep\n---\n# Log\n"


def test_unrelated_duplicate_keys_survive_splice() -> None:
    original = "---\nx: 1\nx: 2\nconflict_with: null\nconflict_with: null\ntitle: Log\n---\nbody\n"
    expected = original.replace("title: Log\n", "")
    assert _strip(original, ["title", "conflict_with"], "title", "Log") == (
        expected,
        ("title",),
    )


@pytest.mark.parametrize(
    "header,title",
    [
        ('title: "L\\\n  og"\nkeep: 1\n', "Log"),
        ("title: a long\n  plain title\nkeep: 1\n", "a long plain title"),
    ],
)
def test_multiline_titles_preserve_remaining_header(header: str, title: str) -> None:
    text = f"---\n{header}---\nbody\n"
    assert _strip(text, ["title"], "title", title) == (
        "---\nkeep: 1\n---\nbody\n",
        ("title",),
    )


def test_unreadable_batch_continues_in_preview_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for folder in ("a", "b", "c", "d"):
        (tmp_path / folder).mkdir()
    for folder in ("a", "d"):
        (tmp_path / folder / "log.md").write_text("---\ntitle: Log\n---\nbody\n")
    bad_yaml = tmp_path / "b" / "log.md"
    bad_utf8 = tmp_path / "c" / "log.md"
    bad_yaml.write_bytes(b"---\ntitle: [\n---\nbody\n")
    bad_utf8.write_bytes(b"---\ntitle: Log\n---\n\xff\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for options, action in [([], "Would remove"), (["--apply"], "Removed")]:
        result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter", *options])
        assert result.exit_code == 1
        for folder in ("a", "d"):
            assert f"{action} {folder}/log.md: title" in result.stdout
        for folder in ("b", "c"):
            assert f"Skipped {folder}/log.md:" in result.stdout
    assert bad_yaml.read_bytes() == b"---\ntitle: [\n---\nbody\n"
    assert bad_utf8.read_bytes() == b"---\ntitle: Log\n---\n\xff\n"
    assert (tmp_path / "d" / "log.md").read_text() == "body\n"


def test_designated_index_respects_operator_protection(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf_capabilities import OkfCapabilities

    path = tmp_path / "index.md"
    path.write_text("---\ntitle: Index\n---\nbody\n")
    caps = OkfCapabilities(
        author=False, verify="off", index=True, log="off", folders=(".",)
    )
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        required_frontmatter=["title"],
        okf_capabilities=caps,
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        assert ReservedFrontmatterRepair(vault).run(apply=True)[0].keys == ("title",)
        assert path.read_text() == "body\n"
    finally:
        vault.close()
    path.write_text("---\ntitle: Index\n---\nbody\n")
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        required_frontmatter=["title"],
        protected_paths=["index.md"],
        okf_capabilities=caps,
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run(apply=True)[0].skip_reason
        assert "title: Index" in path.read_text()
        assert repair.run(apply=True, allow_protected=True)[0].keys == ("title",)
    finally:
        vault.close()


def test_inventory_failure_precedes_all_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    path = tmp_path / "log.md"
    original = "---\ntitle: Log\n---\nbody\n"
    path.write_text(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        real = vault.reader.inspect_bundle_tree

        def pages(request: object) -> object:
            page = real(request)
            return SimpleNamespace(
                state=page.state,
                entries=page.entries,
                enumeration="unavailable",
                next_cursor=None,
            )

        monkeypatch.setattr(vault.reader, "inspect_bundle_tree", pages)
        with pytest.raises(InvalidRequestError, match="inventory is incomplete"):
            ReservedFrontmatterRepair(vault).run(apply=True)
        assert path.read_text() == original
    finally:
        vault.close()


def test_cli_reports_completed_writes_and_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.managers.document import DocumentManager

    for folder in ("a", "b", "c"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "log.md").write_text("---\ntitle: Log\n---\nbody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    write = DocumentManager.write

    def conflict(
        self: DocumentManager, path: str, *args: object, **kwargs: object
    ) -> object:
        if path == "b/log.md":
            (tmp_path / path).write_text("concurrent edit\n")
        return write(self, path, *args, **kwargs)

    monkeypatch.setattr(DocumentManager, "write", conflict)
    result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 1
    assert "Removed a/log.md: title" in result.stdout
    assert "Skipped b/log.md:" in result.stdout
    assert "Removed c/log.md: title" in result.stdout
    assert (tmp_path / "b" / "log.md").read_text() == "concurrent edit\n"
