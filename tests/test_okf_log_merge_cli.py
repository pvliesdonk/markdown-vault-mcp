"""Migration of descendant logs into designated ancestors."""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from markdown_vault_mcp.utils.fs import path_exists
from tests.fixtures.designated_index import designated_vault


def test_entries_keep_literal_links_and_code(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    entry = (
        "- [link](note.md) `[link](note.md)` ![img](image.png) [note](note.md#part)\n"
        "- [[./note#part|Name]]\n- [this](log.md#2025-01-01)\n"
        "- [section](#2025-01-01)\n"
    )
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n" + entry)
    vault = designated_vault(root, (".",))
    try:
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        assert entry in (root / "log.md").read_text()
    finally:
        vault.close()


def test_existing_target_above_read_limit_is_merged(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text("# Log\n\n## 2024-01-01\n\n- " + "x" * 1000 + "\n")
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault._doc_mgr._max_note_read_bytes = 100
        assert len(vault.writer.okf_merge_logs(apply=True).merged) == 1
        assert "- source" in (root / "log.md").read_text()
    finally:
        vault.close()


def test_multiple_sources_keep_entries_and_trailing_definitions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
    index = root / "index.md"
    index.write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    index_bytes = index.read_bytes()
    (root / "log.md").write_text("# Log\n\n[r]: /root.md\n\n## 2024-01-01\n\n- root\n")
    for name in ("a", "b"):
        (root / name / "log.md").write_text(
            f"# Log\n\n  [r]: {name}.md\n\n## 2025-01-01\n\n- [{name}][r]\n"
        )
    (root / "c/log.md").write_text("# Log\n\n## 2025-01-01\n\n- c\n\n[r]: note.md\n")
    vault = designated_vault(root, (".",))
    try:
        result = vault.writer.okf_merge_logs(apply=True)
        assert {m.source for m in result.merged} == {"a/log.md", "b/log.md"}
        assert dict(result.kept)["c/log.md"] == "unaccounted_content"
        merged = (root / "log.md").read_text()
        assert "  [r]: a.md" in merged and "  [r]: b.md" in merged
        assert "- [a][r]" in merged and "- [b][r]" in merged
        assert merged.index("## 2025-01-01") < merged.index("## 2024-01-01")
        assert index.read_bytes() == index_bytes
    finally:
        vault.close()


def test_preview_and_apply_keep_repeated_entries(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text("# Log\n\n## 2025-01-01\n\n- old\n")
    (root / "deep/log.md").write_text("# Log\n\n## 2025-02-01\n\n- same\n- same\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        preview = vault.writer.okf_merge_logs()
        assert [(x.source, x.target, x.entries) for x in preview.merged] == [
            ("deep/log.md", "log.md", 2)
        ]
        assert path_exists(root / "deep/log.md")
        vault.writer.okf_merge_logs(apply=True)
        text = (root / "log.md").read_text()
        assert text.count("- same") == 2
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_apply_notifies_target_write_and_source_deletion(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- item\n")
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    events: list[tuple[str, str]] = []
    capabilities = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=capabilities
        ),
        on_write=lambda path, _content, operation: events.append(
            (path.relative_to(root).as_posix(), operation)
        ),
    )
    try:
        vault.writer.okf_merge_logs(apply=True)
        assert vault._write_callback.drain(timeout=10)
        assert ("log.md", "write") in events
        assert ("deep/log.md", "delete") in events
    finally:
        vault.close()


def test_cli_reports_malformed_source(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "bad").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "bad/log.md").write_text("not a log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    result = CliRunner().invoke(
        app, ["okf", "merge-logs", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "Keep bad/log.md:" in result.output
    assert "Relative destinations and reference definitions" not in result.output


def test_routing_and_header_reporting(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in ("projects/a/deep", "other"):
        (root / folder).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "projects/a/log.md").write_text(
        "---\ntitle: existing\n---\n# Log\n\n## 2024-01-01\n\n- target\n"
    )
    (root / "projects/a/deep/log.md").write_text(
        "---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- entry\n"
    )
    (root / "other/log.md").write_text("# Log\n\n## 2026-01-01\n\n- other\n")
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert {(m.source, m.target) for m in result.merged} == {
            ("projects/a/deep/log.md", "projects/a/log.md"),
            ("other/log.md", "log.md"),
        }
        assert result.source_frontmatter == ("projects/a/deep/log.md",)
        target = (root / "projects/a/log.md").read_text()
        assert "title: source" not in target
    finally:
        vault.close()


def test_hidden_and_excluded_logs_are_considered(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in (".hidden", "excluded"):
        (root / folder).mkdir(parents=True)
        (root / folder / "log.md").write_text("# Log\n\n## 2025-01-01\n\n- entry\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",), exclude=("excluded/**",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs()
        assert {move.source for move in result.merged} == {
            ".hidden/log.md",
            "excluded/log.md",
        }
    finally:
        vault.close()


@pytest.mark.parametrize(
    "kind", ["directory", "fifo", "invalid_utf8", "malformed_yaml", "symlink"]
)
def test_unsafe_target_is_kept_without_changing_source(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    target = root / "log.md"
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "invalid_utf8":
        target.write_bytes(b"\xffinvalid")
    elif kind == "symlink":
        target.symlink_to("other.md")
    else:
        target.write_bytes(b"---\ntitle: [\n---\n# Log\n\n## 2024-01-01\n\n- target\n")
    vault = designated_vault(root, (".",))
    try:
        if kind == "malformed_yaml":
            original = target.read_bytes()
            preview = vault.writer.okf_merge_logs()
            assert not preview.merged
            assert "frontmatter" in dict(preview.kept)["deep/log.md"]
            assert target.read_bytes() == original
        result = vault.writer.okf_merge_logs(apply=True)
        assert not result.merged
        assert "deep/log.md" in dict(result.kept)
        if kind == "symlink":
            assert dict(result.kept)["deep/log.md"] == "aliased log"
            assert not path_exists(root / "other.md")
        assert "- source" in source.read_text()
        if kind == "invalid_utf8":
            assert target.read_bytes() == b"\xffinvalid"
        if kind == "malformed_yaml":
            assert "frontmatter" in dict(result.kept)["deep/log.md"]
            assert target.read_bytes() == original
    finally:
        vault.close()


def test_cli_preview_and_apply(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text(
        '---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- ![image](a.png) <img src="a.png">\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    args = ["okf", "merge-logs", "--source-dir", str(root)]
    from markdown_vault_mcp.facets.index import IndexFacet

    def fail_build(*_args, **_kwargs):
        raise AssertionError("preview built index")

    with monkeypatch.context() as patch:
        patch.setattr(IndexFacet, "build_index", fail_build)
        preview = CliRunner().invoke(app, args)
    assert preview.exit_code == 0, preview.output
    assert "deep/log.md -> log.md: 1 entries" in preview.output
    assert "Source frontmatter encountered" in preview.output
    assert (
        "Relative destinations and reference definitions can change meaning"
        in preview.output
    )
    assert path_exists(root / "deep/log.md")
    applied = CliRunner().invoke(app, [*args, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "Merged deep/log.md" in applied.output
    assert (
        "Relative destinations and reference definitions can change meaning"
        in applied.output
    )
    assert not path_exists(root / "deep/log.md")


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_target_frontmatter_bytes_survive(tmp_path: Path, bom: bytes) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    header = (
        bom
        + b"---\r\ntitle: 'Existing'\r\n"
        + (b"" if bom else b"# comment\r\n")
        + b"---\r\n"
    )
    (root / "log.md").write_bytes(
        header + b"# Log\r\n\r\n## 2024-01-01\r\n\r\n- target\r\n"
    )
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        assert result.target_frontmatter == ("log.md",)
        assert (root / "log.md").read_bytes().startswith(header)
    finally:
        vault.close()


@pytest.mark.parametrize("existing", [False, True])
def test_failed_delete_restores_target_and_retry_keeps_repeats(
    tmp_path: Path, monkeypatch, existing: bool
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    original = "# Log\n\n## 2024-01-01\n\n- old\n"
    target = root / "log.md"
    source = root / "deep/log.md"
    if existing:
        target.write_text(original)
    source.write_text("# Log\n\n## 2025-01-01\n\n- repeat\n- repeat\n")
    vault = designated_vault(root, (".",))
    try:
        real_delete = vault._doc_mgr.delete

        def fail_source(path, *args, **kwargs):
            if path == "deep/log.md":
                raise PermissionError("denied")
            return real_delete(path, *args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_source)
        with pytest.raises(InvalidRequestError, match="target restored"):
            vault.writer.okf_merge_logs(apply=True)
        if existing:
            assert target.read_text() == original
        else:
            assert not path_exists(target)
        monkeypatch.setattr(vault._doc_mgr, "delete", real_delete)
        assert len(vault.writer.okf_merge_logs(apply=True).merged) == 1
        assert target.read_text().count("- repeat") == 2
        assert not path_exists(source)
    finally:
        vault.close()


@pytest.mark.parametrize(
    "scenario", ["source_changed", "target_changed", "restore_fails"]
)
def test_failed_delete_reports_uncertain_state(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    target = root / "log.md"
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- repeat\n- repeat\n")
    vault = designated_vault(root, (".",))
    try:

        def fail_delete(path, *_args, **_kwargs):
            if path == "deep/log.md":
                if scenario == "source_changed":
                    source.write_text("changed externally\n")
                if scenario == "target_changed":
                    target.write_text("changed externally\n")
                raise PermissionError("denied")
            raise OSError("restore failed")

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_delete)
        with pytest.raises(InvalidRequestError, match="target may have changed"):
            vault.writer.okf_merge_logs(apply=True)
        if scenario == "source_changed":
            assert source.read_text() == "changed externally\n"
        else:
            assert "- repeat" in source.read_text()
        if scenario == "target_changed":
            assert target.read_text() == "changed externally\n"
        else:
            assert "- repeat" in target.read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("target_intro", "source_intros", "expected_blocks"),
    [
        (
            "",
            ("Shared introduction.", "Shared introduction."),
            ("Shared introduction.",),
        ),
        ("", ("Custom introduction.", ""), ("Custom introduction.",)),
        (
            "Target introduction.\n\n[root]: /root.md",
            (
                "First introduction.\n\n[first]: /first.md",
                "Second introduction.\n\n[second]: /second.md",
            ),
            (
                "Target introduction.",
                "First introduction.",
                "Second introduction.",
                "[root]: /root.md",
                "[first]: /first.md",
                "[second]: /second.md",
            ),
        ),
        (
            "Longer introduction includes short.",
            ("short.",),
            ("Longer introduction includes short.", "short."),
        ),
    ],
    ids=("repeated-intro", "plain-after-custom", "distinct-with-refs", "substring"),
)
def test_migration_retains_distinct_preamble_blocks(
    tmp_path: Path,
    target_intro: str,
    source_intros: tuple[str, ...],
    expected_blocks: tuple[str, ...],
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text(
        f"# Log\n\n{target_intro}\n\n## 2024-01-01\n\n- target\n"
    )
    for i, intro in enumerate(source_intros):
        folder = root / f"source-{i}"
        folder.mkdir()
        (folder / "log.md").write_text(
            f"# Log\n\n{intro}\n\n## 2025-01-01\n\n- entry-{i}\n"
        )
    vault = designated_vault(root, (".",))
    try:
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == len(source_intros)
        merged = (root / "log.md").read_text()
        assert merged.splitlines().count("# Log") == 1
        for block in expected_blocks:
            assert merged.splitlines().count(block) == 1
        for i in range(len(source_intros)):
            assert merged.splitlines().count(f"- entry-{i}") == 1
        assert merged.index("## 2025-01-01") < merged.index("## 2024-01-01")
    finally:
        vault.close()


def test_distinct_fenced_preambles_remain_complete(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    target_block = "```\nshared\n\nleft\n```"
    source_block = "```\nshared\n\nright\n```"
    (root / "log.md").write_text(
        f"# Log\n\n{target_block}\n\n## 2024-01-01\n\n- target\n"
    )
    (root / "deep/log.md").write_text(
        f"# Log\n\n{source_block}\n\n## 2025-01-01\n\n- source\n"
    )
    vault = designated_vault(root, (".",))
    try:
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        merged = (root / "log.md").read_text()
        assert target_block in merged
        assert source_block in merged
        assert merged.splitlines().count("# Log") == 1
        assert merged.splitlines().count("- target") == 1
        assert merged.splitlines().count("- source") == 1
    finally:
        vault.close()


def test_merge_keeps_links_and_different_preamble(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text(
        "# Root log\n\ntarget intro\n\n## 2024-01-01\n\n- old\n"
    )
    (root / "deep/log.md").write_text(
        "# Deep log\n\nsource intro\n\n## 2025-01-01\n\n- [note](note.md#part)\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        target = (root / "log.md").read_text()
        assert "source intro" in target and "# Deep log" in target
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_interruption_reports_completed_and_pending(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp._okf_log_merge import _LogMigrationInterrupted

    root = tmp_path / "vault"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
        (root / name / "log.md").write_text(f"# Log\n\n## 2025-01-01\n\n- {name}\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        real_delete = vault._doc_mgr.delete

        def fail_b(path, *args, **kwargs):
            if path == "b/log.md":
                raise PermissionError("denied")
            return real_delete(path, *args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_b)
        with pytest.raises(_LogMigrationInterrupted) as caught:
            vault.writer.okf_merge_logs(apply=True)
        assert [move.source for move in caught.value.partial.merged] == ["a/log.md"]
        assert caught.value.pending == ("b/log.md", "c/log.md")
        assert "- a" in (root / "log.md").read_text()
        assert "- b" not in (root / "log.md").read_text()
    finally:
        vault.close()


def test_cli_interruption_reports_kept_and_warning(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.managers.document import DocumentManager

    root = tmp_path / "vault"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "a/log.md").write_text("not a log\n")
    (root / "b/log.md").write_text("# Log\n\n## 2025-01-01\n\n- [note](n.md)\n")
    (root / "c/log.md").write_text("# Log\n\n## 2025-01-01\n\n- c\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    delete = DocumentManager.delete

    def fail_c(self, path, *args, **kwargs):
        if path == "c/log.md":
            raise PermissionError("denied")
        return delete(self, path, *args, **kwargs)

    monkeypatch.setattr(DocumentManager, "delete", fail_c)
    result = CliRunner().invoke(
        app, ["okf", "merge-logs", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 1
    assert "Keep a/log.md:" in result.output
    assert "Merged b/log.md" in result.output
    assert (
        "Relative destinations and reference definitions can change meaning"
        in result.output
    )
    assert "Pending or uncertain: c/log.md" in result.output
