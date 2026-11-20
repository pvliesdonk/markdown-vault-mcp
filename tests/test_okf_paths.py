"""Tests for the pure OKF URI-reference resolver."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from markdown_vault_mcp._okf_paths import resolve_okf_path


@pytest.mark.parametrize(
    ("value", "version", "files", "status", "path", "suffix"),
    [
        (
            "next.md#part",
            "0.3",
            {"notes/next.md"},
            "resolved",
            "notes/next.md",
            "#part",
        ),
        (
            "/next%20one.md?q=1#part",
            "0.3",
            {"next one.md"},
            "resolved",
            "next one.md",
            "?q=1#part",
        ),
        ("#part", "0.3", {"notes/current.md"}, "resolved", "notes/current.md", "#part"),
        ("https://example.org/a", "0.3", set(), "external", None, ""),
        ("custom:opaque", "0.3", set(), "external", None, ""),
        ("//example.org/a", "0.3", set(), "external", None, ""),
        ("../../secret", "0.2", {"secret"}, "external", None, ""),
        ("/%2e%2e/secret", None, {"secret"}, "external", None, ""),
        ("next.md", "0.2", {"next.md"}, "root_fallback", "next.md", ""),
        ("next.md", "0.1", {"next.md"}, "root_fallback", "next.md", ""),
        ("next.md", None, {"next.md"}, "root_fallback", "next.md", ""),
        ("next.md", "0.3", {"next.md"}, "unresolved", None, ""),
        ("next.md", "0.4", {"next.md"}, "unresolved", None, ""),
        (
            "next.md",
            "0.2",
            {"next.md", "notes/next.md"},
            "resolved",
            "notes/next.md",
            "",
        ),
        ("Next.md", "0.3", {"notes/next.md"}, "unresolved", None, ""),
        ("/next.md", "0.2", {"notes/next.md"}, "unresolved", None, ""),
        ("a%23b.md", "0.3", {"notes/a#b.md"}, "resolved", "notes/a#b.md", ""),
    ],
)
def test_resolution(
    value: str,
    version: str | None,
    files: set[str],
    status: str,
    path: str | None,
    suffix: str,
) -> None:
    result = resolve_okf_path(value, "notes/current.md", files, version)
    assert (result.status, result.path, result.suffix) == (status, path, suffix)


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=25))
def test_encoded_name_resolves_case_sensitively(name: str) -> None:
    value = "".join(f"%{ord(char):02X}" for char in name) + ".md"
    result = resolve_okf_path(value, "nested/current.md", {f"nested/{name}.md"}, "0.3")
    assert result.status == "resolved"
    assert result.path == f"nested/{name}.md"


def test_body_link_does_not_use_legacy_fallback() -> None:
    result = resolve_okf_path(
        "target.md?x#y", "notes/current.md", {"target.md"}, "0.2", path_field=False
    )
    assert result.status == "unresolved"
    assert result.suffix == "?x#y"


def test_invalid_source_is_rejected() -> None:
    for source in ("", "/note.md", "a/../note.md", "./note.md", "a//note.md"):
        with pytest.raises(ValueError):
            resolve_okf_path("note.md", source, set(), "0.3")


def test_parent_path_within_bundle_resolves() -> None:
    result = resolve_okf_path("../target.md", "notes/current.md", {"target.md"}, "0.3")
    assert result.path == "target.md"
    assert result.status == "resolved"


def test_fallback_cannot_escape_root() -> None:
    result = resolve_okf_path("../missing.md", "notes/current.md", set(), "0.2")
    assert result.status == "unresolved"


def test_invalid_utf8_never_resolves_replacement_character() -> None:
    result = resolve_okf_path(
        "bad%FF.md?view#part", "notes/current.md", {"notes/bad�.md", "bad�.md"}, "0.2"
    )
    assert result.status == "unresolved"
    assert result.path is None
    assert result.suffix == "?view#part"
