"""Selected-directory inventory stays bounded, private and index-independent."""

from __future__ import annotations

import errno
import json
import os
from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from markdown_vault_mcp import bundle_inventory as inventory
from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest, BundleInspector
from markdown_vault_mcp.scanner import scan_directory
from markdown_vault_mcp.utils import fs
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def inspector(
    root: Path, excludes: tuple[str, ...] = (), note_limit: int = 0
) -> BundleInspector:
    return BundleInspector(root, excludes, frozenset({"png"}), note_limit)


def test_public_reader_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    texts = {
        "index.md": "# Authored navigation\n[Untyped](plain.md)\n",
        "log.md": "# Authored history\n",
        "plain.md": "Untyped body",
        "typed.md": "---\ntype: concept\n---\nText",
        "invalid.md": "---\ntype: [concept]\n---\nText",
        "broken.md": "---\ntype: [\n---\nSecret body",
        "_conventions.md": "Secret convention",
    }
    for name, text in texts.items():
        (bundle / name).write_text(text)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in bundle.iterdir()}
    settings = VaultSettings(required_frontmatter=["type"])
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        monkeypatch.setattr(
            vault.reader,
            "_require_built",
            Mock(side_effect=AssertionError("index gate")),
        )
        monkeypatch.setattr(vault.reader._doc_mgr, "_fts", Mock(spec=[]))
        callback = Mock(side_effect=AssertionError("write"))
        monkeypatch.setattr(vault.reader._doc_mgr, "_mark_paths_dirty", callback)
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root="bundle"))
        items = {entry.path.rsplit("/", 1)[-1]: entry for entry in result.entries}
        assert result.state == "inspected"
        assert result.enumeration == "complete"
        assert result.search_state == "not_evaluated"
        assert result.interpretation == "caller_selected"
        assert items["index.md"].role == "navigation"
        assert items["log.md"].role == "history"
        assert items["plain.md"].type_state == "absent"
        assert items["typed.md"].type_state == "present"
        assert items["invalid.md"].type_state == "invalid"
        assert items["broken.md"].parse_state == "malformed_metadata"
        assert items["_conventions.md"].role == "support"
        assert items["_conventions.md"].visibility == "index_excluded"
        assert "Secret" not in json.dumps(asdict(result))
        callback.assert_not_called()
    finally:
        vault.close()
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in bundle.iterdir()
    }
    admitted = scan_directory(tmp_path, required_frontmatter=["type"])
    assert "bundle/plain.md" not in {note.path for note in admitted}


@pytest.mark.parametrize("field", ["root", "directory"])
@pytest.mark.parametrize(
    "path", ["/absolute", "../outside", "a/../b", "a/./b", "a//b", "nul\x00.md", "a\\b"]
)
def test_invalid_scope(field: str, path: str) -> None:
    with pytest.raises(ValueError):
        BundleInspectionRequest(**{"root": "", field: path})


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_invalid_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        BundleInspectionRequest(root="", entry_limit=limit)


def test_explicit_root_required() -> None:
    with pytest.raises(TypeError):
        BundleInspectionRequest()  # type: ignore[call-arg]


def test_index_excluded_directory_remains_inspectable(tmp_path: Path) -> None:
    folder = tmp_path / "private"
    folder.mkdir()
    (folder / "note.md").write_text("body")
    subject = inspector(tmp_path, ("private/**",))
    result = subject.inspect(BundleInspectionRequest(root="private"))
    assert result.entries[0].visibility == "index_excluded"


def test_roles_allowlist_and_nonrecursive(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "child.md").write_text("body")
    for name in ("photo.png", "secret.exe", ".private.md", "INDEX.md", "NOTE.MD"):
        (tmp_path / name).write_text("body")
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert {entry.path: entry.role for entry in result.entries} == {
        "folder": "directory",
        ".private.md": "concept_candidate",
        "photo.png": "supporting",
        "INDEX.md": "concept_candidate",
    }


def test_symlinks_and_fifo_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "link.md").symlink_to(outside / "secret.md")
    (bundle / "dir.md").symlink_to(outside, target_is_directory=True)
    os.mkfifo(bundle / "pipe.md")
    subject = inspector(tmp_path)
    result = subject.inspect(BundleInspectionRequest(root="bundle"))
    assert {entry.path for entry in result.entries} == {
        "bundle/link.md",
        "bundle/dir.md",
        "bundle/pipe.md",
    }
    assert all(entry.state == "unavailable" for entry in result.entries)
    assert "secret" not in json.dumps(asdict(result))
    assert (
        subject.inspect(BundleInspectionRequest(root="bundle/dir.md")).state
        == "unavailable"
    )
    assert (
        subject.inspect(
            BundleInspectionRequest(root="bundle", directory="dir.md/sub")
        ).state
        == "unavailable"
    )


def test_swap_before_file_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    note = bundle / "note.md"
    note.write_text("safe")
    target = tmp_path / "secret.md"
    target.write_text("secret")
    original = inventory.read_regular_nofollow

    def swapped(parent: int, name: str, limit: int) -> bytes:
        note.unlink()
        note.symlink_to(target)
        return original(parent, name, limit)

    monkeypatch.setattr(inventory, "read_regular_nofollow", swapped)
    entry = (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="bundle")).entries[0]
    )
    assert entry.state == "unavailable"
    assert entry.parse_state == "not_inspected"


def test_swap_directory_keeps_descriptor_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "note.md").write_text("---\ntype: concept\n---\nSafe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.md").write_text("---\ntype: [wrong]\n---\nSecret")
    original = inventory.os.scandir

    def swapped(parent: int) -> os.ScandirIterator[str]:
        bundle.rename(tmp_path / "moved")
        bundle.symlink_to(outside, target_is_directory=True)
        return original(parent)

    monkeypatch.setattr(inventory.os, "scandir", swapped)
    entry = (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="bundle")).entries[0]
    )
    assert entry.type_state == "present"


@pytest.mark.parametrize(
    "error, expected",
    [
        (errno.ENOENT, "missing"),
        (errno.ENOTDIR, "missing"),
        (errno.EACCES, "unavailable"),
        (errno.EIO, "unavailable"),
        (errno.ELOOP, "unavailable"),
    ],
)
def test_scope_and_entry_io_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int, expected: str
) -> None:
    (tmp_path / "note.md").write_text("body")

    def refused(*_args: object, **_kwargs: object) -> bytes:
        raise OSError(error, "secret detail")

    monkeypatch.setattr(inventory, "read_regular_nofollow", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    if error == errno.EACCES:
        assert result.entries == ()
        assert result.enumeration == "unavailable"
    else:
        assert result.entries[0].state == expected
    assert "secret detail" not in json.dumps(asdict(result))
    monkeypatch.setattr(inventory, "open_directory_nofollow", refused)
    assert (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="")).state == expected
    )


def test_missing_source_is_unavailable_and_missing_child_is_missing(
    tmp_path: Path,
) -> None:
    assert (
        inspector(tmp_path / "absent").inspect(BundleInspectionRequest(root="")).state
        == "unavailable"
    )
    assert (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="absent")).state
        == "missing"
    )
    (tmp_path / "file").write_text("not a directory")
    assert (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="file/sub")).state
        == "missing"
    )


def test_no_platform_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fs, "_NOFOLLOW_SUPPORTED", False)
    assert (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="")).state
        == "unavailable"
    )


def test_parse_outcomes_and_bom(tmp_path: Path) -> None:
    content = {
        "encoding.md": b"\xff",
        "bom.md": b"\xef\xbb\xbf---\ntype: concept\n---\nBody",
        "null.md": b"---\ntype: null\n---\n",
        "empty.md": b"---\ntype: ''\n---\n",
        "json.md": b"{\ninvalid json\n}\nbody",
    }
    for name, raw in content.items():
        (tmp_path / name).write_bytes(raw)
    entries = {
        item.path: item
        for item in inspector(tmp_path)
        .inspect(BundleInspectionRequest(root=""))
        .entries
    }
    assert entries["encoding.md"].parse_state == "invalid_encoding"
    assert entries["bom.md"].type_state == "present"
    assert entries["null.md"].type_state == "invalid"
    assert entries["empty.md"].type_state == "invalid"
    assert entries["json.md"].parse_state == "malformed_metadata"


@pytest.mark.parametrize("configured, cap", [(0, 65536), (200000, 65536), (10, 10)])
def test_note_cap_never_parses_partial(
    tmp_path: Path, configured: int, cap: int
) -> None:
    (tmp_path / "large.md").write_bytes(b"x" * (cap + 1))
    (tmp_path / "exact.md").write_bytes(b"x" * cap)
    entries = {
        item.path: item
        for item in inspector(tmp_path, note_limit=configured)
        .inspect(BundleInspectionRequest(root=""))
        .entries
    }
    assert entries["large.md"].reason == "note_limit"
    assert entries["large.md"].parse_state == "not_inspected"
    assert entries["exact.md"].parse_state == "valid"


def test_aggregate_budget_includes_lookahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(20):
        (tmp_path / f"{i}.md").write_bytes(b"x" * 65537)
    original = inventory.read_regular_nofollow
    reads: list[int] = []

    def measured(parent: int, name: str, limit: int) -> bytes:
        result = original(parent, name, limit)
        reads.append(len(result))
        return result

    monkeypatch.setattr(inventory, "read_regular_nofollow", measured)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert sum(reads) == 1024 * 1024
    assert len(result.entries) == 20
    assert result.enumeration == "complete"
    assert all(entry.parse_state == "not_inspected" for entry in result.entries)
    assert any(entry.reason == "byte_budget" for entry in result.entries)


def test_entry_cap_counts_omitted_and_only_one_lookahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(5):
        (tmp_path / f"secret{i}.md").write_text("hidden")
    original = inventory.os.scandir
    visits: list[str] = []

    class ObservedScan:
        def __enter__(self) -> Iterator[os.DirEntry[str]]:
            def entries() -> Iterator[os.DirEntry[str]]:
                with original(tmp_path) as children:
                    for child in children:
                        visits.append(child.name)
                        yield child

            return entries()

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: ObservedScan())
    result = inspector(tmp_path, ("secret*",)).inspect(
        BundleInspectionRequest(root="", entry_limit=2)
    )
    assert len(visits) == 3
    assert len(result.entries) == 2
    assert all(entry.visibility == "index_excluded" for entry in result.entries)
    assert result.enumeration == "entry_limit"


def test_iteration_error_retains_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    original = inventory.os.scandir

    class FailedScan:
        def __enter__(self) -> Iterator[os.DirEntry[str]]:
            def entries() -> Iterator[os.DirEntry[str]]:
                with original(tmp_path) as children:
                    yield next(children)
                raise OSError(errno.EIO, "secret detail")

            return entries()

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: FailedScan())
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.enumeration == "unavailable"
    assert len(result.entries) == 1
    assert result.entries[0].parse_state == "valid"


def test_exact_directory_exclude_does_not_exclude_descendants(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "sub"
    nested.mkdir(parents=True)
    (nested / "note.md").write_text("body")
    subject = inspector(tmp_path, ("a",))
    assert subject.inspect(BundleInspectionRequest(root="a")).state == "inspected"
    result = subject.inspect(BundleInspectionRequest(root="a/sub"))
    assert result.entries[0].path == "a/sub/note.md"
    assert result.entries[0].visibility == "visible"


def test_stat_error_is_visible_without_content_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    original = inventory.os.stat

    def refused(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == "note.md":
            raise OSError(errno.EACCES, "secret detail")
        return original(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "stat", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.entries == ()
    assert result.enumeration == "unavailable"


def test_swap_to_fifo_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "index.md"
    note.write_text("body")
    original = inventory.read_regular_nofollow

    def swapped(parent: int, name: str, limit: int) -> bytes:
        note.unlink()
        os.mkfifo(note)
        return original(parent, name, limit)

    monkeypatch.setattr(inventory, "read_regular_nofollow", swapped)
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    assert entry.state == "unavailable"
    assert entry.role == "navigation"


def test_ancestor_swap_before_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    original = fs._open_directory_at

    def swapped(name: str, parent: int | None = None) -> int:
        if name == "bundle":
            bundle.rmdir()
            bundle.symlink_to(outside, target_is_directory=True)
        return original(name, parent)

    monkeypatch.setattr(fs, "_open_directory_at", swapped)
    assert (
        inspector(tmp_path).inspect(BundleInspectionRequest(root="bundle")).state
        == "unavailable"
    )


def test_configured_source_alias_resolves_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.md").write_text("body")
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    assert (
        inspector(alias).inspect(BundleInspectionRequest(root="")).entries[0].path
        == "note.md"
    )


def test_failed_reads_reserve_aggregate_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(20):
        (tmp_path / f"{i}.md").write_text("body")
    attempts: list[int] = []

    def refused(_parent: int, _name: str, limit: int) -> bytes:
        attempts.append(limit)
        raise OSError(errno.EIO, "failure after unknown partial read")

    monkeypatch.setattr(inventory, "read_regular_nofollow", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert sum(attempts) == 1024 * 1024
    assert any(entry.reason == "byte_budget" for entry in result.entries)


def test_parser_recursion_remains_uninspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    monkeypatch.setattr(
        inventory, "parse_frontmatter", Mock(side_effect=RecursionError)
    )
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    assert entry.state == "not_inspected"
    assert entry.parse_state == "not_inspected"
    assert entry.reason == "metadata_complexity"


def test_subtree_rule_does_not_exclude_regular_prefix_names(tmp_path: Path) -> None:
    for name in ("folder.md", "photo.png"):
        (tmp_path / name).write_text("body")
    (tmp_path / "hidden-subtree").mkdir()
    subject = inspector(tmp_path, ("folder.md/**", "photo.png/**", "hidden-subtree/**"))
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert {entry.path for entry in result.entries} == {
        "folder.md",
        "photo.png",
        "hidden-subtree",
    }


def test_allowlist_also_hides_nonregular_and_stat_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ordinary.exe").write_text("secret")
    (tmp_path / "linked.exe").symlink_to(tmp_path / "ordinary.exe")
    os.mkfifo(tmp_path / "pipe.exe")
    (tmp_path / "unreadable.exe").write_text("secret")
    original = inventory.os.stat

    def refused(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == "unreadable.exe":
            raise OSError(errno.EACCES, "private detail")
        return original(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "stat", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.entries == ()
    assert result.enumeration == "unavailable"


@pytest.mark.parametrize("metadata", ["42: value", "content: value"])
def test_metadata_shape_errors_preserve_valid_siblings(
    tmp_path: Path, metadata: str
) -> None:
    (tmp_path / "bad.md").write_text(f"---\n{metadata}\n---\nBody")
    (tmp_path / "good.md").write_text("---\ntype: concept\n---\nBody")
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["bad.md"].parse_state == "malformed_metadata"
    assert entries["good.md"].type_state == "present"


def test_source_ancestor_swap_after_resolution_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ancestor = tmp_path / "inside"
    source = ancestor / "vault"
    source.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "vault").mkdir(parents=True)
    (outside / "vault" / "secret.md").write_text("---\ntype: secret\n---\nSecret")
    original = type(source).resolve

    def swapped(path: Path, strict: bool = False) -> Path:
        resolved = original(path, strict=strict)
        if path == source:
            ancestor.rename(tmp_path / "moved")
            ancestor.symlink_to(outside, target_is_directory=True)
        return resolved

    monkeypatch.setattr(type(source), "resolve", swapped)
    result = inspector(source).inspect(BundleInspectionRequest(root=""))
    assert result.state == "unavailable"
    assert result.entries == ()
    assert "secret" not in json.dumps(asdict(result))


def test_unknown_directory_stat_error_prevents_complete_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "private-folder").mkdir()
    original = inventory.os.stat

    def refused(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == "private-folder":
            raise OSError(errno.EACCES, "private detail")
        return original(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "stat", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.state == "inspected"
    assert result.enumeration == "unavailable"
    assert result.entries == ()
    assert "private" not in json.dumps(asdict(result))


def test_merge_alias_expansion_never_reaches_metadata_constructor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = ["---", "a0: &a0 {type: concept}"]
    for depth in range(1, 23):
        lines.append(f"a{depth}: &a{depth} {{<<: [*a{depth - 1}, *a{depth - 1}]}}")
    lines.extend(["---", "Body"])
    (tmp_path / "complex.md").write_text("\n".join(lines))
    (tmp_path / "good.md").write_text("---\ntype: concept\n---\nBody")
    original = inventory.parse_frontmatter

    def guarded(text: str) -> object:
        assert "&a0" not in text, "Alias expansion reached metadata construction"
        return original(text)

    monkeypatch.setattr(inventory, "parse_frontmatter", guarded)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["complex.md"].state == "not_inspected"
    assert entries["complex.md"].reason == "metadata_complexity"
    assert entries["complex.md"].parse_state == "not_inspected"
    assert entries["complex.md"].type_state == "not_inspected"
    assert entries["good.md"].type_state == "present"


@pytest.mark.parametrize(
    "header",
    [
        "items: " + "[" * 33 + "0" + "]" * 33,
        "\n".join(f"k{i}: value" for i in range(2100)),
    ],
)
def test_metadata_complexity_bounds_precede_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    (tmp_path / "note.md").write_text(f"---\n{header}\n---\nBody")
    constructor = Mock(side_effect=AssertionError("Complex metadata constructed"))
    monkeypatch.setattr(inventory, "parse_frontmatter", constructor)
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    assert entry.state == "not_inspected"
    assert entry.reason == "metadata_complexity"
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "# Markdown\n*a0 &a0 [ordinary body]",
        "---\ntype: concept\n---\n*a0 &a0 [ordinary body]",
        "---\ntype: concept\nexample: '*a0 &a0'\n---\nBody",
        '---\nexample: "*a0 &a0"\n---\nBody',
        "  \n----\nitems: " + "[" * 30 + "0" + "]" * 30 + "\n----\nBody",
        "---\n*a0 &a0\nUnclosed block",
        '{"type": "concept", "example": "*a0 &a0"}\nBody',
        '+++\ntype = "concept"\nexample = "*a0 &a0"\n+++\nBody',
    ],
)
def test_complexity_guard_uses_only_actual_yaml_metadata(
    tmp_path: Path, text: str
) -> None:
    (tmp_path / "note.md").write_text(text)
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    assert entry.state == "inspected"
    assert entry.parse_state == "valid"
    assert entry.reason is None


def test_directory_visibility_does_not_depend_on_attachment_extension(
    tmp_path: Path,
) -> None:
    (tmp_path / "directory.exe").mkdir()
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.enumeration == "complete"
    assert [(entry.path, entry.role) for entry in result.entries] == [
        ("directory.exe", "directory")
    ]


def test_invalid_yaml_unicode_escape_preserves_valid_sibling(tmp_path: Path) -> None:
    (tmp_path / "invalid.md").write_text(
        '---\ntype: "' + "\\" + 'U00110000"\n---\nBody'
    )
    (tmp_path / "valid.md").write_text("---\ntype: concept\n---\nBody")
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["invalid.md"].state == "inspected"
    assert entries["invalid.md"].parse_state == "malformed_metadata"
    assert entries["invalid.md"].type_state == "not_inspected"
    assert entries["valid.md"].parse_state == "valid"
    assert entries["valid.md"].type_state == "present"


@pytest.mark.parametrize("depth", [32, 33])
def test_yaml_collection_depth_exact_boundary(tmp_path: Path, depth: int) -> None:
    # The outer mapping contributes one collection; the rest are nested sequences.
    header = "items: " + "[" * (depth - 1) + "0" + "]" * (depth - 1)
    (tmp_path / "note.md").write_bytes(f"---\n{header}\n---\nBody".encode())
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    if depth == 32:
        assert entry.state == "inspected"
        assert entry.parse_state == "valid"
        assert entry.reason is None
    else:
        assert entry.state == "not_inspected"
        assert entry.parse_state == "not_inspected"
        assert entry.reason == "metadata_complexity"


@pytest.mark.parametrize("event_count", [4096, 4097])
def test_yaml_event_count_exact_boundary(tmp_path: Path, event_count: int) -> None:
    # Stream/document/mapping/sequence boundaries and the key contribute nine
    # events; each scalar sequence item contributes one additional event.
    header = "items: [" + ",".join("0" for _ in range(event_count - 9)) + "]"
    assert sum(1 for _ in inventory.yaml.parse(header)) == event_count
    (tmp_path / "note.md").write_bytes(f"---\n{header}\n---\nBody".encode())
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
    if event_count == 4096:
        assert entry.state == "inspected"
        assert entry.parse_state == "valid"
        assert entry.reason is None
    else:
        assert entry.state == "not_inspected"
        assert entry.parse_state == "not_inspected"
        assert entry.reason == "metadata_complexity"


@pytest.mark.parametrize(
    "tagged_value",
    [
        "!!timestamp nonsense",
        "!!bool nonsense",
        "!!float ''",
        "!!int ''",
    ],
)
def test_invalid_yaml_tagged_scalars_preserve_valid_sibling(
    tmp_path: Path, tagged_value: str
) -> None:
    (tmp_path / "invalid.md").write_bytes(
        f"---\ntype: {tagged_value}\n---\nBody".encode()
    )
    (tmp_path / "valid.md").write_text("---\ntype: concept\n---\nBody")
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["invalid.md"].state == "inspected"
    assert entries["invalid.md"].parse_state == "malformed_metadata"
    assert entries["invalid.md"].type_state == "not_inspected"
    assert entries["valid.md"].parse_state == "valid"
    assert entries["valid.md"].type_state == "present"


def test_unexpected_parser_failure_preserves_siblings_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bad.md").write_text("---\ntype: broken\n---\nBody")
    (tmp_path / "good.md").write_text("---\ntype: concept\n---\nBody")
    original = inventory.parse_frontmatter

    def failed(text: str) -> object:
        if "broken" in text:
            raise RuntimeError("private internal detail")
        return original(text)

    monkeypatch.setattr(inventory, "parse_frontmatter", failed)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["bad.md"].state == "unavailable"
    assert entries["bad.md"].reason == "unexpected_parser_error"
    assert entries["bad.md"].parse_state == "not_inspected"
    assert entries["good.md"].type_state == "present"
    assert "private" not in json.dumps(asdict(result))


def test_numeric_parser_limit_remains_uninspected_with_valid_sibling(
    tmp_path: Path,
) -> None:
    # Mathematically zero, but PyYAML converts the sexagesimal base to a float.
    value = ":".join(["0"] * 180)
    (tmp_path / "large.md").write_text(f'---\ntype: !!float "{value}"\n---\nBody')
    (tmp_path / "good.md").write_text("---\ntype: concept\n---\nBody")
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    entries = {entry.path: entry for entry in result.entries}
    assert result.enumeration == "complete"
    assert entries["large.md"].state == "not_inspected"
    assert entries["large.md"].reason == "metadata_complexity"
    assert entries["large.md"].parse_state == "not_inspected"
    assert entries["good.md"].type_state == "present"


def test_inventory_includes_index_excluded_hidden_and_conventions(
    tmp_path: Path,
) -> None:
    (tmp_path / "excluded.md").write_text("---\ntype: concept\n---\nBody")
    (tmp_path / "_conventions.md").write_text("Guidance")
    hidden = tmp_path / ".claude" / "notes"
    hidden.mkdir(parents=True)
    (hidden / "n.md").write_text("---\ntype: concept\n---\nBody")
    subject = inspector(tmp_path, ("excluded.md", "_conventions.md"))
    root = {
        entry.path: entry
        for entry in subject.inspect(BundleInspectionRequest(root="")).entries
    }
    assert root["excluded.md"].visibility == "index_excluded"
    assert root["_conventions.md"].role == "support"
    note = subject.inspect(BundleInspectionRequest(root=".claude/notes")).entries[0]
    assert note.hidden_path is True
    assert note.parse_state == "valid"
    assert note.type_state == "present"


def test_internal_scopes_are_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = Mock(side_effect=AssertionError("internal scope opened"))
    monkeypatch.setattr(inventory, "open_directory_nofollow", opened)
    for path in (".git", ".markdown_vault_mcp", "nested/.git/objects"):
        result = inspector(tmp_path).inspect(BundleInspectionRequest(root=path))
        assert result.state == "unavailable"
    opened.assert_not_called()


def test_configured_conventions_and_state_directory(tmp_path: Path) -> None:
    (tmp_path / "guidance.md").write_text("Guidance")
    internal = tmp_path / "server-data"
    internal.mkdir()
    (internal / "secret.md").write_text("secret")
    settings = VaultSettings(
        conventions_file="guidance.md", state_path=internal / "state.json"
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        root = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        entries = {entry.path: entry for entry in root.entries}
        assert entries["guidance.md"].role == "support"
        assert entries["guidance.md"].visibility == "index_excluded"
        assert "server-data" in entries
        nested = vault.reader.inspect_bundle(
            BundleInspectionRequest(root="server-data")
        )
        assert nested.state == "inspected"
        assert [entry.path for entry in nested.entries] == ["server-data/secret.md"]
    finally:
        vault.close()


def test_permission_refusal_does_not_hide_readable_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "denied.md").write_text("secret")
    (tmp_path / "readable.md").write_text("---\ntype: concept\n---\nbody")
    original = inventory.read_regular_nofollow
    scandir = inventory.os.scandir

    class OrderedScan:
        def __enter__(self) -> Iterator[os.DirEntry[str]]:
            with scandir(tmp_path) as children:
                return iter(sorted(children, key=lambda child: child.name))

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: OrderedScan())

    def refused(parent: int, name: str, limit: int) -> bytes:
        if name == "denied.md":
            raise PermissionError(errno.EACCES, "denied")
        return original(parent, name, limit)

    monkeypatch.setattr(inventory, "read_regular_nofollow", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.enumeration == "unavailable"
    assert [entry.path for entry in result.entries] == ["readable.md"]
    assert result.entries[0].type_state == "present"


def test_state_file_at_vault_root_does_not_hide_vault(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("body")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(state_path=tmp_path / "state.json")
    )
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.state == "inspected"
        assert [entry.path for entry in result.entries] == ["note.md"]
    finally:
        vault.close()


@pytest.mark.parametrize("source_alias", [True, False])
@pytest.mark.parametrize("parent_segments", [True, False])
def test_reader_state_boundary_with_aliases_and_parent_segments(
    tmp_path: Path, source_alias: bool, parent_segments: bool
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    state_dir = real / "server-state"
    state_dir.mkdir()
    (state_dir / "state.md").write_text("---\ntype: secret\n---\nbody")
    (state_dir / "note.md").write_text("---\ntype: concept\n---\nbody")
    source = alias if source_alias else real
    state_base = real if source_alias else alias
    if parent_segments:
        state_base = state_base / "server-state" / ".."
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(state_path=state_base / "server-state" / "state.md"),
    )
    try:
        root = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert "server-state" in {entry.path for entry in root.entries}
        nested = vault.reader.inspect_bundle(
            BundleInspectionRequest(root="server-state")
        )
        assert nested.state == "inspected"
        assert [entry.path for entry in nested.entries] == ["server-state/note.md"]
    finally:
        vault.close()


def test_explicit_internal_directory_is_never_entered(tmp_path: Path) -> None:
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "hidden.md").write_text("body")
    policy = inventory.InspectionPolicy(state_paths=(internal,))
    subject = BundleInspector(tmp_path, (), frozenset(), 0, policy)
    assert subject.inspect(BundleInspectionRequest(root="")).entries == ()
    assert (
        subject.inspect(BundleInspectionRequest(root="internal")).state == "unavailable"
    )


def test_state_sidecars_are_not_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.md"
    names = (
        "state.md",
        "state.md-wal",
        "state.md-shm",
        "state.md-journal",
        "state.md.lock",
    )
    for name in names:
        (tmp_path / name).write_text("secret")
    (tmp_path / "state-other.md").write_text("body")
    original = inventory.os.stat

    def checked(name: object, *args: object, **kwargs: object) -> os.stat_result:
        assert name not in names
        return original(name, *args, **kwargs)  # type: ignore[call-overload]

    subject = BundleInspector(
        tmp_path,
        (),
        frozenset({"md-wal", "md-shm", "md-journal", "lock"}),
        0,
        inventory.InspectionPolicy(state_paths=(state,)),
    )
    monkeypatch.setattr(inventory.os, "stat", checked)
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert [entry.path for entry in result.entries] == ["state-other.md"]


def test_memory_state_and_unexpected_child_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset(),
        0,
        inventory.InspectionPolicy(state_paths=(type(tmp_path)(":memory:"),)),
    )
    assert (
        subject.inspect(BundleInspectionRequest(root="")).entries[0].path == "note.md"
    )
    monkeypatch.setattr(
        subject, "_observe", Mock(side_effect=OSError(errno.EIO, "failure"))
    )
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert result.enumeration == "unavailable"
    assert result.entries == ()


def test_vector_sidecars_do_not_hide_neighboring_notes(tmp_path: Path) -> None:
    base = tmp_path / "vectors"
    for name in (
        "vectors.v2.json",
        "vectors.v2.m1.npy",
        "vectors.npy",
        "vectors.lock",
        "vectors.sqlite3",
        "vectors.sqlite3-wal",
        "vectors-notes.md",
    ):
        (tmp_path / name).write_text("body")
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset({"json", "npy", "lock", "sqlite3", "sqlite3-wal"}),
        0,
        inventory.InspectionPolicy(embeddings_path=base),
    )
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert [entry.path for entry in result.entries] == ["vectors-notes.md"]
