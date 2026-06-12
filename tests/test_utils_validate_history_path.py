import pytest

from markdown_vault_mcp.utils import validate_history_path


def test_accepts_md(tmp_path):
    assert (
        validate_history_path("notes/a.md", tmp_path, frozenset())
        == (tmp_path / "notes/a.md").resolve()
    )


def test_accepts_allowed_attachment(tmp_path):
    exts = frozenset({"png", "pdf"})
    assert (
        validate_history_path("assets/x.png", tmp_path, exts)
        == (tmp_path / "assets/x.png").resolve()
    )


def test_attachment_extension_is_case_insensitive(tmp_path):
    assert (
        validate_history_path("assets/X.PNG", tmp_path, frozenset({"png"}))
        == (tmp_path / "assets/X.PNG").resolve()
    )


def test_rejects_unknown_extension(tmp_path):
    with pytest.raises(ValueError, match="\\.md note or a configured attachment"):
        validate_history_path("a.exe", tmp_path, frozenset({"png"}))


def test_rejects_attachment_when_allowlist_empty(tmp_path):
    with pytest.raises(ValueError, match="\\.md note or a configured attachment"):
        validate_history_path("a.png", tmp_path, frozenset())


def test_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="traversal"):
        validate_history_path("../escape.png", tmp_path, frozenset({"png"}))


def test_accepts_any_attachment_under_wildcard(tmp_path):
    assert (
        validate_history_path("assets/photo.heic", tmp_path, frozenset({"*"}))
        == (tmp_path / "assets/photo.heic").resolve()
    )


def test_rejects_extensionless_path(tmp_path):
    """A path with no extension (suffix '') is rejected without a wildcard."""
    with pytest.raises(ValueError, match="\\.md note or a configured attachment"):
        validate_history_path("README", tmp_path, frozenset({"png"}))
