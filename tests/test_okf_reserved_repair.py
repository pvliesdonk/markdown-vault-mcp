"""The deliberate reserved-file placeholder migration."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp._okf_reserved_repair import ReservedFrontmatterRepair, _strip
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.exceptions import InvalidRequestError, ReadOnlyError
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_public_writer_preview_and_apply(tmp_path: Path) -> None:
    path = tmp_path / "log.md"
    original = "---\ntitle: Log\n---\n# Log\n"
    path.write_text(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        preview = vault.writer.okf_strip_seeded_frontmatter()
        assert [(item.path, item.keys) for item in preview] == [("log.md", ("title",))]
        assert path.read_text() == original
        assert vault.writer.okf_strip_seeded_frontmatter(apply=True) == preview
        assert path.read_text() == "# Log\n"
        assert vault.writer.okf_strip_seeded_frontmatter() == []
    finally:
        vault.close()


def test_unwired_writer_refuses_repair(tmp_path: Path) -> None:
    from markdown_vault_mcp.facets.writer import WriterFacet

    vault = Vault(source_dir=tmp_path)
    try:
        with pytest.raises(RuntimeError, match="repair is not configured"):
            WriterFacet(vault._doc_mgr).okf_strip_seeded_frontmatter()
    finally:
        vault.close()


@pytest.mark.parametrize(
    "original",
    [
        b"---\ntitle: [\n---\n\xff",
        b"body only\n",
        b"---\nx-a: " + b"[" * 40 + b"]" * 40 + b"\n---\n",
    ],
)
def test_root_index_keeps_invalid_or_body_only_content(
    tmp_path: Path, original: bytes
) -> None:
    path = tmp_path / "index.md"
    path.write_bytes(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on"),
    )
    try:
        for apply in (False, True):
            item = vault.writer.okf_strip_seeded_frontmatter(apply=apply)[0]
            assert item.path == "index.md" and item.kept_reason
            assert item.skip_reason is None and item.keys == ()
            assert path.read_bytes() == original
    finally:
        vault.close()


def test_preview_and_apply_including_excluded_notes(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    root = tmp_path / "index.md"
    root.write_text(
        '\ufeff---\r\nokf_version: "0.3"\r\ntitle: Index\r\ndescription: Bundle\r\nlicense: MIT\r\ntype: null\r\nx-test: value\r\n---\r\n# Index\r\n'
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
            "index.md": (),
            "guides/index.md": ("type",),
            "guides/log.md": ("title", "type"),
        }
        assert [p.read_bytes() for p in (root, log, edited)] == before
        assert repair.run(apply=True) == preview
        assert next(x for x in preview if x.path == "index.md").kept_reason
        assert root.read_bytes() == before[0]
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


def test_cli_dry_run_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    log = root / "log.md"
    index = root / "index.md"
    original = b"---\ntitle: Index\n---\nbody\n"
    index.write_bytes(original)
    log.write_text("---\ntitle: Log\ntype: null\n---\n# Log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title,type")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    runner = CliRunner()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert result.exit_code == 0, result.output
    assert "Kept index.md:" in result.stdout
    assert (
        "Would remove log.md: title" in result.stdout
        and "Would remove log.md: type" in result.stdout
    )
    assert "title: Log" in log.read_text()
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Kept index.md:" in result.stdout
    assert index.read_bytes() == original
    assert "Removed log.md: title" in result.stdout
    assert log.read_text() == "# Log\n"
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert "Kept index.md:" in result.stdout
    result = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert "Kept index.md:" in result.stdout


def test_custom_title_field(tmp_path: Path) -> None:
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
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            b"\xef\xbb\xbf---\ntitle: guides\ntype: null\n---\n\n# guides\n",
            b"\xef\xbb\xbf\n# guides\n",
        ),
        (
            b"\xef\xbb\xbf---\n\ntitle: guides\ntype: null\n---\n# guides\n",
            b"\xef\xbb\xbf# guides\n",
        ),
    ],
)
def test_empty_header_removed_and_audit_passes(
    tmp_path: Path, original: bytes, expected: bytes
) -> None:
    from markdown_vault_mcp.okf import audit_bundle

    folder = tmp_path / "guides"
    folder.mkdir()
    path = folder / "index.md"
    path.write_bytes(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title", "type"]
        ),
    )
    try:
        assert audit_bundle(tmp_path).index_frontmatter.count == 1
        assert ReservedFrontmatterRepair(vault).run(apply=True)
        assert path.read_bytes() == expected
        assert audit_bundle(tmp_path).index_frontmatter.count == 0
    finally:
        vault.close()


@pytest.mark.parametrize("title", ["R&D", "Hello # World", "*star*"])
def test_literal_yaml_characters_in_title_are_removed(title: str) -> None:
    import yaml

    encoded = yaml.safe_dump({"title": title}, allow_unicode=True)
    original = f"---\n{encoded}---\n# Body\n"
    assert _strip(original, ["title"], "title", title) == ("# Body\n", ("title",))


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
        assert "Cannot inspect reserved file" in item.skip_reason
    finally:
        vault.close()


def test_cli_apply_refusals_exit_with_error(
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


@pytest.mark.parametrize(
    "original,required,title,expected",
    [
        (
            "---\r\ntitle: Log\r\ntype: null # user comment\r\nother: value\r\n---\r\n# Log\r\n",
            ["title"],
            "Log",
            "---\r\ntype: null # user comment\r\nother: value\r\n---\r\n# Log\r\n",
        ),
        (
            "---\n# Keep this\ntitle: Log\n---\n# Body\n",
            ["title"],
            "Log",
            "---\n# Keep this\n---\n# Body\n",
        ),
        (
            "---\nx: 1\nx: 2\nconflict_with: null\nconflict_with: null\ntitle: Log\n---\nbody\n",
            ["title", "conflict_with"],
            "Log",
            "---\nx: 1\nx: 2\nconflict_with: null\nconflict_with: null\n---\nbody\n",
        ),
        (
            '---\ntitle: "L\\\n  og"\nkeep: 1\n---\nbody\n',
            ["title"],
            "Log",
            "---\nkeep: 1\n---\nbody\n",
        ),
        (
            "---\ntitle: a long\n  plain title\nkeep: 1\n---\nbody\n",
            ["title"],
            "a long plain title",
            "---\nkeep: 1\n---\nbody\n",
        ),
    ],
)
def test_splice_preserves_unselected_bytes(
    original: str, required: list[str], title: str, expected: str
) -> None:
    assert _strip(original, required, "title", title) == (expected, ("title",))


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

    (tmp_path / "guides").mkdir()
    path = tmp_path / "guides" / "index.md"
    path.write_text("---\ntitle: guides\n---\nbody\n")
    caps = OkfCapabilities(
        author=False, verify="off", index=True, log="off", folders=("guides",)
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
    path.write_text("---\ntitle: guides\n---\nbody\n")
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        required_frontmatter=["title"],
        protected_paths=["guides/index.md"],
        okf_capabilities=caps,
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        repair = vault.writer.okf_strip_seeded_frontmatter
        assert repair(apply=True)[0].skip_reason
        assert "title: guides" in path.read_text()
        assert repair(apply=True, allow_protected=True)[0].keys == ("title",)
        assert sorted(p.name for p in (tmp_path / "guides").iterdir()) == ["index.md"]
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


def test_four_dash_frontmatter_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "log.md"
    original = b"----\r\ntitle: Log\r\nkeep: value # comment\r\n----\r\n# Log\r\n"
    path.write_bytes(original)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    runner = CliRunner()
    preview = runner.invoke(app, ["okf", "strip-seeded-frontmatter"])
    assert preview.exit_code == 0 and "Would remove log.md: title" in preview.stdout
    assert path.read_bytes() == original
    applied = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert applied.exit_code == 0 and "Removed log.md: title" in applied.stdout
    assert path.read_bytes() == original.replace(b"title: Log\r\n", b"")
    assert (
        "Nothing to repair"
        in runner.invoke(app, ["okf", "strip-seeded-frontmatter"]).stdout
    )
    unsafe = b"----\r\ntitle: Log # keep\r\n----\r\n# Log\r\n"
    path.write_bytes(unsafe)
    skipped = runner.invoke(app, ["okf", "strip-seeded-frontmatter", "--apply"])
    assert skipped.exit_code == 1 and "Skipped log.md:" in skipped.stdout
    assert path.read_bytes() == unsafe


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("---\n\ntitle: Log\n---\n# Log\n", "# Log\n"),
        ("---\n  \ntitle: Log\n---\n# Log\n", "# Log\n"),
        ("\ufeff---\n\ntitle: Log\n---\n# Log\n", "\ufeff# Log\n"),
        ("\n\n---\n\ntitle: Log\n---\n# Log\n", "\n\n# Log\n"),
        ("\ufeff\n---\n\ntitle: Log\n---\n# Log\n", "\ufeff\n# Log\n"),
        ("  ---\n\ntitle: Log\n---\n# Log\n", "  # Log\n"),
        ("\t---\n\ntitle: Log\n---\n# Log\n", "\t# Log\n"),
        ("---\r\n\r\ntitle: Log\r\n---\r\n# Log\r\n", "# Log\r\n"),
        ("----\n\ntitle: Log\n----\n# Log\n", "# Log\n"),
        ("---\n\ntitle: Log\n---", ""),
        ("---\n\ntitle: Log\nkeep: 1\n---\nbody\n", "---\n\nkeep: 1\n---\nbody\n"),
    ],
)
def test_empty_header_removal_keeps_prefix_and_body(
    original: str, expected: str
) -> None:
    assert _strip(original, ["title"], "title", "Log") == (expected, ("title",))


def test_nonstring_yaml_key_does_not_abort_batch(tmp_path: Path) -> None:
    for folder in ("a", "b", "c"):
        (tmp_path / folder).mkdir()
    bad = tmp_path / "b" / "log.md"
    original = b"---\ntitle: Log\nyes: null\n---\n# Bad\n"
    bad.write_bytes(original)
    for folder in ("a", "c"):
        (tmp_path / folder / "log.md").write_text("---\ntitle: Log\n---\n# Good\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        for apply in (False, True):
            outcomes = {item.path: item for item in repair.run(apply=apply)}
            assert outcomes["b/log.md"].skip_reason
            assert outcomes["a/log.md"].keys == ("title",)
            assert outcomes["c/log.md"].keys == ("title",)
        assert bad.read_bytes() == original
        assert (tmp_path / "a" / "log.md").read_text() == "# Good\n"
        assert (tmp_path / "c" / "log.md").read_text() == "# Good\n"
    finally:
        vault.close()


def test_large_log_exceeds_interactive_limit_but_is_repaired(tmp_path: Path) -> None:
    path = tmp_path / "log.md"
    body = b"# Log\n" + b"x" * 270000
    original = b"---\ntitle: Log\n---\n" + body
    path.write_bytes(original)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", required_frontmatter=["title"]
        ),
    )
    try:
        repair = ReservedFrontmatterRepair(vault)
        assert repair.run()[0].keys == ("title",)
        assert path.read_bytes() == original
        assert repair.run(apply=True)[0].keys == ("title",)
        assert path.read_bytes() == body
    finally:
        vault.close()


def test_large_recursive_alias_skips_file_and_repairs_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    bad = tmp_path / "a" / "log.md"
    original = b"---\ntitle: Log\ncycle: &loop [*loop]\n---\n" + b"x" * 70000
    bad.write_bytes(original)
    good = tmp_path / "b" / "log.md"
    good.write_bytes(b"---\ntitle: Log\n---\n# Log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for options, action in [([], "Would remove"), (["--apply"], "Removed")]:
        result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter", *options])
        assert result.exit_code == 1
        assert "Skipped a/log.md:" in result.stdout
        assert f"{action} b/log.md: title" in result.stdout
    assert bad.read_bytes() == original
    assert good.read_bytes() == b"# Log\n"


def test_cli_escapes_controls_in_repair_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for folder, header in [
        ("safe\x1b[31m", "title: Log"),
        ("skip\x1b[32m", "title: Log # comment"),
    ]:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "log.md").write_text(f"---\n{header}\n---\nbody\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    result = CliRunner().invoke(app, ["okf", "strip-seeded-frontmatter"], color=True)
    assert result.exit_code == 1
    assert "\x1b" not in result.stdout
    assert r"safe\x1b[31m/log.md" in result.stdout
    assert r"skip\x1b[32m/log.md" in result.stdout
