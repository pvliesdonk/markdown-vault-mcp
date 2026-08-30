import pytest

from markdown_vault_mcp.utils import resolve_inside


def test_resolves_relative_path_inside_base(tmp_path):
    assert resolve_inside("notes/a.md", tmp_path) == (tmp_path / "notes/a.md").resolve()


def test_accepts_base_itself(tmp_path):
    """Containment includes the base directory itself (callers that must
    reject the base add that check locally)."""
    assert resolve_inside(".", tmp_path) == tmp_path.resolve()


def test_resolves_internal_dotdot_that_stays_inside(tmp_path):
    assert resolve_inside("a/../b.md", tmp_path) == (tmp_path / "b.md").resolve()


def test_rejects_traversal_with_default_message(tmp_path):
    with pytest.raises(ValueError, match=r"Path traversal detected: \.\./escape\.md"):
        resolve_inside("../escape.md", tmp_path)


def test_rejects_traversal_naming_original_spelling(tmp_path):
    """The *original* override names the caller's pre-normalization spelling."""
    with pytest.raises(ValueError, match=r"Path traversal detected: /\.\./raw/"):
        resolve_inside("../raw", tmp_path, original="/../raw/")
