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


class OrderedScan:
    def __init__(self, root: Path, last: str = "") -> None:
        self.root = root
        self.last = last
        self.scandir = os.scandir

    def __enter__(self) -> Iterator[os.DirEntry[str]]:
        with self.scandir(self.root) as children:
            return iter(
                sorted(
                    children, key=lambda child: (child.name == self.last, child.name)
                )
            )

    def __exit__(self, *args: object) -> None:
        pass


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
    "error, expected, entry_state",
    [
        (errno.ENOENT, "missing", "missing"),
        (errno.ENOTDIR, "missing", "missing"),
        (errno.EACCES, "unavailable", None),
        (errno.EIO, "unavailable", "unavailable"),
        (errno.ELOOP, "unavailable", "unavailable"),
    ],
)
def test_scope_and_entry_io_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: int,
    expected: str,
    entry_state: str | None,
) -> None:
    (tmp_path / "note.md").write_text("body")

    def refused(*_args: object, **_kwargs: object) -> bytes:
        raise OSError(error, "secret detail")

    monkeypatch.setattr(inventory, "read_regular_nofollow", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert [entry.state for entry in result.entries] == (
        [] if entry_state is None else [entry_state]
    )
    assert result.enumeration == ("unavailable" if entry_state is None else "complete")
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


def test_stat_permission_refusal_omits_entry(
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
        if limit == 0:
            return b""
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
            raise OSError(errno.EIO, "private detail")
        return original(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "stat", refused)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert all(e.path == "linked.exe" for e in result.entries)
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
            raise OSError(errno.EIO, "private detail")
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


def _alias_header(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\nBody"


_DOUBLING = [
    "a0: &a0 [x, x]",
    *(f"a{i + 1}: &a{i + 1} [*a{i}, *a{i}]" for i in range(8)),
]
_LONG = "x" * 600


@pytest.mark.parametrize(
    "lines",
    [
        ["title: &t Useful title", "other: *t"],
        ["tags: &t [a, b]", "other: *t"],
        ["base: &b {type: concept}", "merged:", "  <<: *b"],
        ["d: &d " + "[" * 30 + "0" + "]" * 30, "e: [*d]"],
    ],
)
def test_bounded_aliases_are_accepted_only_on_request(lines: list[str]) -> None:
    text = _alias_header(*lines)
    assert inventory._metadata_too_complex(text) is True
    assert inventory._metadata_too_complex(text, True) is False


@pytest.mark.parametrize(
    "lines",
    [
        ["b: *a", "a: &a 1"],
        ["a: &a [*a]"],
        ["a: &a {k: *a}"],
        _DOUBLING,
        [f"t: &t {_LONG}", "u: *t", "v: *t"],
        ["d: &d " + "[" * 30 + "0" + "]" * 30, "e: [[*d]]"],
    ],
)
def test_expanded_alias_work_is_bounded_before_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> None:
    text = _alias_header(*lines)
    assert inventory._metadata_too_complex(text, True) is True
    (tmp_path / "note.md").write_text(text)
    constructor = Mock(side_effect=AssertionError("Complex metadata constructed"))
    monkeypatch.setattr(inventory, "parse_frontmatter", constructor)
    entry = inspector(tmp_path).inspect(BundleInspectionRequest(root="")).entries[0]
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
    private = tmp_path / "private"
    private.mkdir()
    (private / "note.md").write_text("body")
    subject = inspector(tmp_path, ("excluded.md", "_conventions.md", "private/**"))
    root = {
        entry.path: entry
        for entry in subject.inspect(BundleInspectionRequest(root="")).entries
    }
    assert root["excluded.md"].visibility == "index_excluded"
    assert root["_conventions.md"].role == "support"
    assert (
        subject.inspect(BundleInspectionRequest(root="private")).entries[0].visibility
        == "index_excluded"
    )
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


@pytest.mark.parametrize(
    ("name", "role"),
    [("guidance.md", "support"), ("index.md", "navigation"), ("log.md", "history")],
)
def test_configured_conventions_role(tmp_path: Path, name: str, role: str) -> None:
    (tmp_path / name).write_text("Guidance")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(conventions_file=name))
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.entries[0].role == role
        assert result.entries[0].visibility == "index_excluded"
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


@pytest.mark.parametrize("kind", ["state", "internal"])
def test_internal_entries_are_never_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    state = tmp_path / "state.md"
    names = (
        (
            "state.md",
            "state.md-wal",
            "state.md-shm",
            "state.md-journal",
            "state.md.lock",
        )
        if kind == "state"
        else (".git", ".markdown_vault_mcp")
    )
    for name in names:
        path = tmp_path / name
        if kind == "internal":
            path.mkdir()
        else:
            path.write_text("secret")
    (tmp_path / "note.md").write_text("body")
    original = inventory.os.stat

    def checked(name: object, *args: object, **kwargs: object) -> os.stat_result:
        assert name not in names
        return original(name, *args, **kwargs)  # type: ignore[call-overload]

    subject = BundleInspector(
        tmp_path,
        (),
        frozenset({"md-wal", "md-shm", "md-journal", "lock"}),
        0,
        inventory.InspectionPolicy(index_path=state if kind == "state" else None),
    )
    monkeypatch.setattr(inventory.os, "stat", checked)
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert [entry.path for entry in result.entries] == ["note.md"]


def test_memory_state_does_not_hide_same_named_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SQLite's in-memory sentinel must not protect a real same-named directory.
    (tmp_path / ":memory:").mkdir()
    monkeypatch.chdir(tmp_path)
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset(),
        0,
        inventory.InspectionPolicy(index_path=type(tmp_path)(":memory:")),
    )
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert [entry.path for entry in result.entries] == [":memory:"]


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


def test_reader_vector_base_note_and_legacy_matrices(tmp_path: Path) -> None:
    (tmp_path / "vectors.md").write_text("---\ntype: concept\n---\nbody")
    for name in ("vectors.m3.npy", "vectors.v2.m4.npy", "vectors.mbad.npy"):
        (tmp_path / name).write_text("data")
    settings = VaultSettings(
        embeddings_path=tmp_path / "vectors.md", attachment_extensions=["npy"]
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        entries = vault.reader.inspect_bundle(BundleInspectionRequest(root="")).entries
        assert {entry.path for entry in entries} == {"vectors.md", "vectors.mbad.npy"}
    finally:
        vault.close()


def test_vector_base_also_configured_as_state_stays_hidden(tmp_path: Path) -> None:
    (tmp_path / "vectors.md").write_text("body")
    settings = VaultSettings(
        embeddings_path=tmp_path / "vectors.md", state_path=tmp_path / "vectors.md"
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        assert (
            vault.reader.inspect_bundle(BundleInspectionRequest(root="")).entries == ()
        )
    finally:
        vault.close()


def test_unstatable_state_preserves_unrelated_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    (tmp_path / "other").mkdir()
    state = tmp_path / "other" / "state.md"
    state.write_text("secret")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(state_path=state))
    resolved_state = state.resolve()
    original = type(state).stat

    def refused(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        if path == resolved_state:
            raise PermissionError(errno.EACCES, "private")
        return original(path, *args, **kwargs)  # type: ignore[call-arg]

    try:
        monkeypatch.setattr(type(state), "stat", refused)
        root = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert root.state == "inspected"
        assert "note.md" in {entry.path for entry in root.entries}
        nested = vault.reader.inspect_bundle(BundleInspectionRequest(root="other"))
        assert "other/state.md" not in {entry.path for entry in nested.entries}
        assert nested.enumeration == "unavailable"
    finally:
        vault.close()


def test_unresolvable_state_parent_does_not_block_unrelated_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    private = tmp_path / "private"
    private.mkdir()
    state = private / "state.md"
    state.write_text("secret")
    subject = BundleInspector(
        tmp_path, (), frozenset(), 0, inventory.InspectionPolicy(state_paths=(state,))
    )
    resolve = type(state).resolve

    def denied(path: Path, strict: bool = False) -> Path:
        if path in (state, private):
            raise PermissionError(errno.EACCES, "private")
        return resolve(path, strict=strict)

    monkeypatch.setattr(type(state), "resolve", denied)
    root = subject.inspect(BundleInspectionRequest(root=""))
    assert root.state == "inspected"
    assert "note.md" in {entry.path for entry in root.entries}
    nested = subject.inspect(BundleInspectionRequest(root="private"))
    assert "private/state.md" not in {entry.path for entry in nested.entries}


@pytest.mark.parametrize("inside", [True, False])
def test_policy_resolution_loses_all_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside: bool
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "note.md").write_text("body")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    state = (alias if inside else tmp_path) / "state.md"
    state.write_text("server state")
    subject = BundleInspector(
        alias, (), frozenset(), 0, inventory.InspectionPolicy(state_paths=(state,))
    )
    original = type(state).resolve
    source_resolutions = 0

    def lost_access(path: Path, strict: bool = False) -> Path:
        nonlocal source_resolutions
        if path == alias and strict:
            source_resolutions += 1
            if source_resolutions == 1:
                return original(path, strict=True)
        raise PermissionError(errno.EACCES, "ancestor unavailable")

    monkeypatch.setattr(type(state), "resolve", lost_access)
    result = subject.inspect(BundleInspectionRequest(root=""))
    assert result.state == "inspected"
    assert [entry.path for entry in result.entries] == ["note.md"]


def test_denial_remains_incomplete_at_entry_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "denied.md").write_text("body")
    (tmp_path / "other.md").write_text("body")
    ordered = OrderedScan(tmp_path)
    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: ordered)
    monkeypatch.setattr(
        inventory,
        "read_regular_nofollow",
        Mock(side_effect=PermissionError(errno.EACCES, "denied")),
    )
    result = inspector(tmp_path).inspect(
        BundleInspectionRequest(root="", entry_limit=1)
    )
    assert result.enumeration == "unavailable"
    assert result.entries == ()


def test_stat_io_error_reports_known_note_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.md").write_text("body")
    original = inventory.os.stat

    def failed(name: object, *args: object, **kwargs: object) -> os.stat_result:
        if name == "note.md":
            raise OSError(errno.EIO, "private detail")
        return original(name, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "stat", failed)
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=""))
    assert result.enumeration == "complete"
    assert result.entries[0].state == "unavailable"
    assert result.entries[0].reason == "io_error"
    assert "private detail" not in json.dumps(asdict(result))


def test_store_ignores_nonnumeric_matrix_names(tmp_path: Path) -> None:
    from markdown_vault_mcp.stores._legacy import numbered_files

    (tmp_path / "vectors.m3.npy").write_bytes(b"matrix")
    (tmp_path / "vectors.mbad.npy").write_bytes(b"unrelated")
    assert numbered_files(tmp_path / "vectors", ".m") == {
        3: tmp_path / "vectors.m3.npy"
    }


@pytest.mark.parametrize("kind", ["directory", "attachment", "budget_note"])
def test_denied_children_are_omitted_even_without_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    (tmp_path / "visible.md").write_text("body")
    denied = {
        "directory": ".denied",
        "attachment": ".denied.png",
        "budget_note": ".denied.md",
    }[kind]
    if kind == "directory":
        (tmp_path / denied).mkdir()
    else:
        (tmp_path / denied).write_text("secret")
    if kind == "budget_note":
        for i in range(16):
            (tmp_path / f"f{i:02}.md").write_bytes(b"x" * 65536)
    ordered = OrderedScan(tmp_path, denied)
    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: ordered)
    original_open = inventory.os.open

    def checked_open(name: object, flags: int, *args: object, **kwargs: object) -> int:
        if name == denied:
            raise PermissionError(errno.EACCES, "private")
        return original_open(name, flags, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "open", checked_open)
    result = inspector(tmp_path).inspect(
        BundleInspectionRequest(root="", entry_limit=100)
    )
    assert result.enumeration == "unavailable"
    assert denied not in {entry.path for entry in result.entries}
    assert "visible.md" in {entry.path for entry in result.entries}


def test_state_directory_created_after_policy_is_never_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state-dir"
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset(),
        0,
        inventory.InspectionPolicy(state_paths=(state_dir,)),
    )
    original = subject._prepare_policy

    def created(root: Path) -> None:
        original(root)
        state_dir.mkdir()
        (state_dir / "secret.md").write_text("secret")

    monkeypatch.setattr(subject, "_prepare_policy", created)
    opened = Mock(side_effect=AssertionError("state directory entered"))
    monkeypatch.setattr(inventory, "open_directory_nofollow", opened)
    assert (
        subject.inspect(BundleInspectionRequest(root="state-dir")).state
        == "unavailable"
    )
    opened.assert_not_called()


def test_case_variant_sidecar_obeys_filesystem_identity(tmp_path: Path) -> None:
    state = tmp_path / "State.md"
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset({"lock"}),
        0,
        inventory.InspectionPolicy(index_path=state),
    )
    (tmp_path / "state.md.lock").write_text("secret")
    result = subject.inspect(BundleInspectionRequest(root=""))
    probe = tmp_path / "State.md.lock"
    matches = probe.exists()
    assert ("state.md.lock" not in {entry.path for entry in result.entries}) == matches


def test_case_variant_git_uses_filesystem_identity(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "note.md").write_text("body")
    alternate = tmp_path / ".GIT"
    if not alternate.exists():
        alternate.mkdir()
        (alternate / "note.md").write_text("ordinary")
        expected = "inspected"
    else:
        expected = "unavailable"
    result = inspector(tmp_path).inspect(BundleInspectionRequest(root=".GIT"))
    assert result.state == expected


def test_configured_state_unicode_and_case_match_filesystem(tmp_path: Path) -> None:
    directory = tmp_path / "Café"
    directory.mkdir()
    state = directory / "State.md"
    state.write_text("private")
    (directory / "note.md").write_text("body")
    configured = tmp_path / "Cafe\u0301" / "state.md"
    actual_match = configured.exists()
    policy = inventory.InspectionPolicy(state_paths=(configured,))
    subject = BundleInspector(tmp_path, (), frozenset(), 0, policy)
    result = subject.inspect(BundleInspectionRequest(root="Café"))
    assert (
        "Café/State.md" not in {entry.path for entry in result.entries}
    ) == actual_match
    assert "Café/note.md" in {entry.path for entry in result.entries}


def test_numbered_vector_parent_case_matches_filesystem(tmp_path: Path) -> None:
    directory = tmp_path / "Vectors"
    directory.mkdir()
    (directory / "Data.v2.m3.npy").write_text("state")
    configured = tmp_path / "Vectors" / "Data"
    alias = tmp_path / "vectors"
    if not alias.exists():
        alias.mkdir()
        (alias / "Data.v2.m3.npy").write_text("ordinary")
        expected = {"vectors/Data.v2.m3.npy"}
    else:
        expected = set()
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset({"npy"}),
        0,
        inventory.InspectionPolicy(embeddings_path=configured),
    )
    result = subject.inspect(BundleInspectionRequest(root="vectors"))
    assert {entry.path for entry in result.entries} == expected


def test_late_state_directory_case_uses_filesystem_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "StateDir"
    actual = tmp_path / "statedir"
    subject = BundleInspector(
        tmp_path,
        (),
        frozenset(),
        0,
        inventory.InspectionPolicy(state_paths=(configured,)),
    )
    original = subject._prepare_policy

    def created(root: Path) -> None:
        original(root)
        actual.mkdir()
        (actual / "note.md").write_text("body")

    monkeypatch.setattr(subject, "_prepare_policy", created)
    result = subject.inspect(BundleInspectionRequest(root="statedir"))
    assert result.state == ("unavailable" if configured.exists() else "inspected")


def test_reader_omits_chmod_denied_directory_and_attachment(tmp_path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses mode permissions")
    directory = tmp_path / ".denied"
    directory.mkdir()
    attachment = tmp_path / ".denied.png"
    attachment.write_text("body")
    (tmp_path / "visible.md").write_text("body")
    directory.chmod(0)
    attachment.chmod(0)
    try:
        try:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        except PermissionError:
            pass
        else:
            os.close(fd)
            pytest.skip("filesystem permits directory access despite chmod")
        try:
            fd = os.open(attachment, os.O_RDONLY)
        except PermissionError:
            pass
        else:
            os.close(fd)
            pytest.skip("filesystem permits file access despite chmod")
        vault = Vault(
            source_dir=tmp_path, settings=VaultSettings(attachment_extensions=["png"])
        )
        try:
            result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
            assert result.enumeration == "unavailable"
            assert [entry.path for entry in result.entries] == ["visible.md"]
            assert (
                vault.reader.inspect_bundle(
                    BundleInspectionRequest(root=".denied")
                ).state
                == "unavailable"
            )
        finally:
            vault.close()
    finally:
        directory.chmod(0o700)
        attachment.chmod(0o600)


@pytest.mark.parametrize("state_is_ancestor", [False, True])
@pytest.mark.parametrize("scope", ["", "sub"])
def test_reader_refuses_protected_vault_root_and_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_is_ancestor: bool, scope: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "sub").mkdir()
    (source / "note.md").write_text("body")
    state = tmp_path if state_is_ancestor else source
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    opener = Mock(side_effect=AssertionError("protected root entered"))
    monkeypatch.setattr(inventory, "open_directory_nofollow", opener)
    try:
        result = vault.reader.inspect_bundle(
            BundleInspectionRequest(root=scope, entry_limit=1)
        )
        assert result.state == "unavailable"
        assert result.enumeration == "not_inspected"
        assert result.entries == ()
        opener.assert_not_called()
    finally:
        vault.close()


def test_reader_keeps_tracker_lock_sibling(tmp_path: Path) -> None:
    (tmp_path / "state.json.lock").write_text("ordinary attachment")
    (tmp_path / "index.sqlite.lock").write_text("owned lock")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            state_path=tmp_path / "state.json",
            index_path=tmp_path / "index.sqlite",
            attachment_extensions=["lock"],
        ),
    )
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.enumeration == "complete"
        assert [entry.path for entry in result.entries] == ["state.json.lock"]
    finally:
        vault.close()


@pytest.mark.parametrize("kind", ["directory", "attachment", "note"])
def test_reader_denied_opens_do_not_consume_note_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    suffix = {"directory": "", "attachment": ".png", "note": ".md"}[kind]
    denied = {f"a{i:02}{suffix}" for i in range(20)}
    for name in denied:
        path = tmp_path / name
        if kind == "directory":
            path.mkdir()
        else:
            path.write_text("secret")
    (tmp_path / "z-readable.md").write_text("---\ntype: concept\n---\nbody")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(attachment_extensions=["png"])
    )
    ordered = OrderedScan(tmp_path)
    original_open = inventory.os.open

    def refused(name: object, flags: int, *args: object, **kwargs: object) -> int:
        if name in denied:
            raise PermissionError(errno.EACCES, "denied")
        return original_open(name, flags, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(inventory.os, "scandir", lambda _parent: ordered)
    monkeypatch.setattr(inventory.os, "open", refused)
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.enumeration == "unavailable"
        assert [entry.path for entry in result.entries] == ["z-readable.md"]
        assert result.entries[0].reason is None
        assert result.entries[0].type_state == "present"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            b"---\ntitle: A\n---",
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (
            b"---\ntitle: [\n---\nbody",
            "bc9f9742cd571d939143e10bbe153b523a0ce547bf05b5e81efd8d4eafbdd5f8",
        ),
        (
            "---\ntitle: A\n---\u00a0\nbody".encode(),
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (
            b"---\ntitle: A\n" + b"-" * 65536,
            "da6eac1ad99fe92a67f818ea399b7c73a7fd3901214b0d668e950022082f7831",
        ),
        (
            b"---\n" + b"key: value\n" * 30000 + b"---\nbody",
            "98b9ccd17d932a3a7ad1b786daeff123d94819924911206dbd33f8d73c69a3c1",
        ),
        (b"---\ntitle: A\nunterminated", None),
        (b"plain note", None),
        (b"\xffbad", None),
        (
            b"\xef\xbb\xbf---\r\ntitle: A\r\n---\r\nbody",
            "fa129686f04e013e227d827a7fb63ea7e8533260eaa507b7213df1df54233891",
        ),
        (
            b'{\n"a": 1\n}\n\nbody',
            "9c3d952771c2e9a93796f796f4b1d2fe947ff0d1f3e15063da2ff9e953871a6d",
        ),
        (b"+++\ntitle=1\n+++\nbody", None),
        (
            b"---\ntitle: A\n---\n\xff",
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (
            b"---\ntitle: A\n---\n" + b"x\n" * 40000 + b"\xff",
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (b"---\ntitle: \xff\n---\nbody", None),
        (b"---\n" + b"key: value\n" * 7000 + b"bad: \xff\n---\nbody", None),
        (
            b" " * 65534 + b"--" + b"-\ntitle: A\n---\nbody",
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (
            b"---\n" + b"a" * 65528 + b"\n---x\n---\nbody",
            "eb15f57bb4ad4458cd6e8443532eb85882867691e7549d5210527786a77500b1",
        ),
        (
            b" " * 65535 + "\u00a0".encode() + b"---\ntitle: A\n---\nbody",
            "5a21b538cb4eb30cd562a2d44fa89289dfa302f8ab8912951178bf2eec81014d",
        ),
        (b"x" * (8 * 1024 * 1024), None),
    ],
)
def test_digest_uses_parser_boundary_and_full_content(
    raw: bytes, expected: str | None
) -> None:
    import hashlib
    import io
    import tracemalloc

    from markdown_vault_mcp.bundle_inventory import _observation_digests

    class Limited(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(size)

    stream = Limited(raw)
    tracemalloc.start()
    try:
        digest, header = _observation_digests(stream)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert digest == hashlib.sha256(raw).hexdigest()
    assert header == expected
    if len(raw) == 8 * 1024 * 1024:
        assert peak < 2 * 1024 * 1024
