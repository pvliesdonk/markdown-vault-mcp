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


def test_symlinks_and_internal_paths(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    (bundle / "escape").symlink_to(outside, target_is_directory=True)
    (bundle / "link.md").symlink_to(outside / "secret.md")
    (bundle / "linked.exe").symlink_to(outside / "secret.md")
    (bundle / ".git").mkdir()
    (bundle / ".git" / "secret.md").write_text("secret")
    (bundle / ".hidden.md").write_text("x")
    inspector = BundleInspector(tmp_path, ("bundle/.hidden.md",), frozenset(), 0)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert [e.path for e in page.entries] == [
        "bundle/.hidden.md",
        "bundle/escape",
        "bundle/link.md",
    ]
    assert (
        page.entries[0].hidden_path and page.entries[0].visibility == "index_excluded"
    )
    assert all(e.reason == "symlink" for e in page.entries[1:])


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


def test_replaced_cursor_directory_and_denied_child(tmp_path, monkeypatch):
    import errno
    import shutil

    bundle = tmp_path / "bundle"
    (bundle / "a").mkdir(parents=True)
    (bundle / "a" / "child.md").write_text("x")
    (bundle / "z.md").write_text("z")
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(BundleTreeRequest("bundle", entry_limit=1))
    shutil.rmtree(bundle / "a")
    (bundle / "a").symlink_to(tmp_path, target_is_directory=True)
    page = inspector.inspect(BundleTreeRequest("bundle", cursor=first.next_cursor))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]
    original = inspector._observe

    def refuse(parent, name, path, remaining):
        if name == "a":
            raise PermissionError(errno.EACCES, "denied")
        return original(parent, name, path, remaining)

    monkeypatch.setattr(inspector, "_observe", refuse)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable"
    assert [entry.path for entry in page.entries] == ["bundle/z.md"]


def test_depth_and_byte_limits(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    directory = bundle
    for _ in range(33):
        directory = directory / "d"
        directory.mkdir()
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable"
    assert page.entries[-1].reason == "depth_limit"
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
        assert page.search_state == "not_evaluated"
    finally:
        vault.close()


def test_unavailable_policy_and_directory_listing(tmp_path, monkeypatch):
    import errno
    import os

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

    def refused(_fd):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "scandir", refused)
    page = inspector.inspect(BundleTreeRequest("bundle"))
    assert page.enumeration == "unavailable" and page.entries == ()
