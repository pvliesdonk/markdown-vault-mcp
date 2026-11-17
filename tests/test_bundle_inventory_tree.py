"""Regression tests for pageable bundle-tree inspection."""

from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest


def test_tree_pages_resume_in_component_order(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "a").mkdir(parents=True)
    (bundle / "a" / "b.md").write_text("hello")
    (bundle / "a-c.md").write_text("hello")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=1))
    second = inspector.inspect(
        BundleTreeRequest("bundle", entry_limit=2, cursor=first.next_cursor)
    )
    assert [e.path for e in first.entries + second.entries] == [
        "bundle/a",
        "bundle/a/b.md",
        "bundle/a-c.md",
    ]


def test_many_pages_and_live_insertions(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for i in reversed(range(250)):
        (bundle / f"{i:03}.md").write_text("x")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)

    def pages(limit):
        cursor = None
        paths = []
        while True:
            page = inspector.inspect(
                BundleTreeRequest("bundle", entry_limit=limit, cursor=cursor)
            )
            paths.extend(e.path for e in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                return paths

    expected = [f"bundle/{i:03}.md" for i in range(250)]
    assert pages(7) == pages(50) == pages(1000) == expected
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=50))
    (bundle / "001a.md").write_text("x")
    (bundle / "249a.md").write_text("x")
    (bundle / "049.md").unlink()
    rest = inspector.inspect(
        BundleTreeRequest("bundle", entry_limit=1000, cursor=first.next_cursor)
    )
    assert [e.path for e in rest.entries] == [*expected[50:], "bundle/249a.md"]


def test_tree_internal_and_hidden_paths(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / ".git").mkdir()
    (bundle / ".git" / "secret.md").write_text("secret")
    (bundle / ".hidden.md").write_text("x")
    (bundle / "omitted.exe").write_text("x")
    inspector = BundleInspector(tmp_path, ("bundle/.hidden.md",), frozenset(), 0)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert [e.path for e in page.entries] == ["bundle/.hidden.md"]
    assert (
        page.entries[0].hidden_path and page.entries[0].visibility == "index_excluded"
    )


def test_cursor_validation_and_exact_boundary(tmp_path):
    import pytest

    for cursor in ("other/x", "bundle", "bundle/../x"):
        with pytest.raises(ValueError):
            BundleTreeRequest("bundle", cursor=cursor)
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "a.md").write_text("a")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=1))
    last = inspector.inspect(
        BundleTreeRequest("bundle", entry_limit=1, cursor=first.next_cursor)
    )
    assert first.enumeration == "entry_limit"
    assert (
        last.entries == ()
        and last.enumeration == "complete"
        and last.next_cursor is None
    )


def test_cursor_ancestor_descent_failures(tmp_path, monkeypatch):
    import errno
    import shutil

    import markdown_vault_mcp.bundle_inventory as inventory

    bundle = tmp_path / "bundle"
    (bundle / "a").mkdir(parents=True)
    (bundle / "a" / "child.md").write_text("x")
    (bundle / "z.md").write_text("z")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=2))
    shutil.rmtree(bundle / "a")
    (bundle / "a").symlink_to(tmp_path, target_is_directory=True)
    page = inspector.inspect(BundleTreeRequest("bundle", cursor=first.next_cursor))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]

    original = inventory.open_child_directory

    def missing(parent, name):
        if name == "a":
            raise OSError(errno.ENOENT, "simulated deletion")
        return original(parent, name)

    monkeypatch.setattr(inventory, "open_child_directory", missing)
    page = inspector.inspect(BundleTreeRequest("bundle", cursor=first.next_cursor))
    assert page.enumeration == "complete"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]


def test_denied_child_does_not_hide_siblings(tmp_path, monkeypatch):
    import errno

    bundle = tmp_path / "bundle"
    (bundle / "a").mkdir(parents=True)
    (bundle / "z.md").write_text("z")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    original = inspector._observe

    def refuse(parent, name, path, remaining):
        if name == "a":
            raise PermissionError(errno.EACCES, "denied")
        return original(parent, name, path, remaining)

    monkeypatch.setattr(inspector, "_observe", refuse)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]


def test_byte_limit_stops_page(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    for i in range(20):
        (bundle / f"{i:02}.md").write_text("x" * 65536)
    first = inspector.inspect(BundleTreeRequest("bundle"))
    assert first.enumeration == "entry_limit"
    assert first.next_cursor is not None
    assert all(e.reason != "byte_budget" for e in first.entries)


def test_depth_limit_remains_enforced_on_resume(tmp_path):
    directory = tmp_path / "bundle"
    directory.mkdir()
    for _ in range(33):
        directory = directory / "d"
        directory.mkdir()
    (directory / "secret.md").write_text("x")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    page = inspector.inspect(BundleTreeRequest("bundle", entry_limit=32))
    assert page.entries[-1].reason == "depth_limit"
    assert page.enumeration == "unavailable"
    resumed = inspector.inspect(BundleTreeRequest("bundle", cursor=page.next_cursor))
    assert resumed.enumeration == "unavailable" and resumed.entries == ()
    forged = inspector.inspect(
        BundleTreeRequest("bundle", cursor="bundle/" + "/".join(["d"] * 33))
    )
    assert forged.enumeration == "unavailable" and forged.entries == ()


def test_unrepresentable_name_is_hidden_and_incomplete(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "bad\\name.md").write_text("x")
    (bundle / "good.md").write_text("x")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/good.md"]


def test_reader_tree_before_index(tmp_path):
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "note.md").write_text("hello")
    vault = Vault(source_dir=tmp_path)
    try:
        page = vault.reader.inspect_bundle_tree(BundleTreeRequest(root=""))
        assert [entry.path for entry in page.entries] == ["note.md"]
    finally:
        vault.close()


def test_unavailable_policy_marks_enumeration(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "note.md").write_text("x")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    prepare = inspector._prepare_policy

    def unavailable(root):
        prepare(root)
        inspector._unavailable_policy_paths = {bundle / "private"}

    monkeypatch.setattr(inspector, "_prepare_policy", unavailable)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable" and len(page.entries) == 1


def test_directory_listing_refusal_marks_enumeration(tmp_path, monkeypatch):
    import errno
    import os

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)

    def refused(_fd):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "scandir", refused)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable" and page.entries == ()


def test_hidden_symlink_marks_both_enumerations_incomplete(tmp_path):
    from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "escape.dir").symlink_to(tmp_path, target_is_directory=True)
    (bundle / "escape").symlink_to(tmp_path, target_is_directory=True)
    (bundle / "link.md").symlink_to(tmp_path / "outside")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    for request in (BundleInspectionRequest("bundle"), BundleTreeRequest("bundle")):
        page = inspector.inspect(request)
        assert page.enumeration == "unavailable"
        assert [(e.path, e.reason) for e in page.entries] == [
            ("bundle/escape", "symlink"),
            ("bundle/link.md", "symlink"),
        ]


def test_exact_cursor_symlink_is_a_leaf(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.md").symlink_to(tmp_path, target_is_directory=True)
    (bundle / "z.md").write_text("z")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=1))
    page = inspector.inspect(BundleTreeRequest("bundle", cursor=first.next_cursor))
    assert page.enumeration == "complete"
    assert [e.path for e in page.entries] == ["bundle/z.md"]


def test_resume_skips_policy_for_earlier_siblings(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for i in range(100):
        (bundle / f"{i:03}.md").write_text("x")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    original = inspector._internal
    seen = []

    def record(path):
        seen.append(path)
        return original(path)

    monkeypatch.setattr(inspector, "_internal", record)
    page = inspector.inspect(BundleTreeRequest("bundle", cursor="bundle/095.md"))
    assert [e.path for e in page.entries] == [
        f"bundle/{i:03}.md" for i in range(96, 100)
    ]
    assert not any(
        path.startswith("bundle/0") and path < "bundle/095.md" for path in seen
    )


def test_cursor_stat_race(tmp_path, monkeypatch):
    import errno
    import os

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.md").write_text("a")
    (bundle / "z.md").write_text("z")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    original = os.stat
    failure = errno.ENOENT

    def stat_cursor(path, *args, **kwargs):
        if path == "a.md" and kwargs.get("dir_fd") is not None:
            raise OSError(failure, "simulated race")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_cursor)
    page = inspector.inspect(BundleTreeRequest("bundle", cursor="bundle/a.md"))
    assert page.enumeration == "complete"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]
    failure = errno.EACCES
    page = inspector.inspect(BundleTreeRequest("bundle", cursor="bundle/a.md"))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]
