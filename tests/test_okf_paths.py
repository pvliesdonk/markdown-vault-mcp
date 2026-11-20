"""Tests for the pure OKF URI-reference resolver."""

import pytest

from markdown_vault_mcp._okf_paths import resolve_okf_path


@pytest.mark.parametrize(
    ("value", "version", "files", "expected"),
    [
        (
            "next.md#part",
            "0.3",
            {"notes/next.md"},
            ("resolved", "notes/next.md", "#part"),
        ),
        (
            "/next%20one.md?q=1#part",
            "0.3",
            {"next one.md"},
            ("resolved", "next one.md", "?q=1#part"),
        ),
        (
            "#part",
            "0.3",
            {"notes/current.md"},
            ("resolved", "notes/current.md", "#part"),
        ),
        ("custom:opaque", "0.3", set(), ("external", None, "")),
        ("../../secret", "0.2", {"secret"}, ("external", None, "")),
        ("/%2e%2e/secret", None, {"secret"}, ("external", None, "")),
        ("next.md", "0.2", {"next.md"}, ("root_fallback", "next.md", "")),
        ("next.md", "0.1", {"next.md"}, ("root_fallback", "next.md", "")),
        ("next.md", None, {"next.md"}, ("root_fallback", "next.md", "")),
        ("next.md", "0.3", {"next.md"}, ("unresolved", None, "")),
        ("next.md", "0.4", {"next.md"}, ("unresolved", None, "")),
        (
            "next.md",
            "0.2",
            {"next.md", "notes/next.md"},
            ("resolved", "notes/next.md", ""),
        ),
        ("Next.md", "0.3", {"notes/next.md"}, ("unresolved", None, "")),
        ("/next.md", "0.2", {"notes/next.md"}, ("unresolved", None, "")),
        ("a%23b.md", "0.3", {"notes/a#b.md"}, ("resolved", "notes/a#b.md", "")),
        ("../target.md", "0.3", {"target.md"}, ("resolved", "target.md", "")),
        ("folder", "0.3", {"notes/folder/"}, ("resolved", "notes/folder/", "")),
        (
            "folder",
            "0.3",
            {"notes/folder", "notes/folder/"},
            ("resolved", "notes/folder", ""),
        ),
        ("folder/", "0.3", set(), ("unresolved", None, "")),
        (
            "target.md/",
            "0.3",
            {"notes/target.md/"},
            ("resolved", "notes/target.md/", ""),
        ),
        ("/folder/", "0.3", {"folder/"}, ("resolved", "folder/", "")),
        (".", "0.3", {"notes", "notes/"}, ("resolved", "notes/", "")),
        ("..", "0.3", {"/"}, ("resolved", "/", "")),
        ("/", "0.3", {"/"}, ("resolved", "/", "")),
        (
            "folder/?q#f",
            "0.3",
            {"notes/folder/"},
            ("resolved", "notes/folder/", "?q#f"),
        ),
        ("folder/", "0.2", {"folder/"}, ("root_fallback", "folder/", "")),
        ("folder", "0.2", {"folder/"}, ("root_fallback", "folder/", "")),
        ("folder/", "0.3", {"folder/"}, ("unresolved", None, "")),
        ("folder", "0.3.1", {"folder/"}, ("unresolved", None, "")),
        ("folder", "v0.2", {"folder/"}, ("unresolved", None, "")),
        ("a%00b.md", "0.3", {"notes/a%00b.md"}, ("unresolved", None, "")),
        ("bad%FF.md", "0.3", {"notes/bad%FF.md"}, ("unresolved", None, "")),
    ],
)
def test_resolution(
    value: str,
    version: str | None,
    files: set[str],
    expected: tuple[str, str | None, str],
) -> None:
    result = resolve_okf_path(value, "notes/current.md", files, version)
    assert (result.status, result.path, result.suffix) == expected


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


def test_fallback_cannot_escape_root() -> None:
    result = resolve_okf_path("../y.md", "a/b/current.md", {"y.md"}, "0.2")
    assert result.status == "unresolved"


def test_refused_escapes_never_match() -> None:
    result = resolve_okf_path(
        "a%2Fb.md", "notes/current.md", {"notes/a%2Fb.md", "notes/a/b.md"}, "0.2"
    )
    assert result.status == "unresolved"


@pytest.mark.parametrize(
    "value", ["target.md/", "/notes/target.md/", "target.md/.", "target.md/child/.."]
)
def test_directory_reference_does_not_select_file(value: str) -> None:
    assert (
        resolve_okf_path(value, "notes/current.md", {"notes/target.md"}, "0.2").status
        == "unresolved"
    )


@pytest.mark.parametrize("value", ["https://example.org/a?q#f", "//example.org/a?q#f"])
def test_external_reference_retains_suffix(value: str) -> None:
    result = resolve_okf_path(value, "notes/current.md", set(), "0.3")
    assert (result.status, result.suffix) == ("external", "?q#f")


def test_empty_path_field_is_absent() -> None:
    assert (
        resolve_okf_path("", "notes/current.md", {"notes/current.md"}, None).status
        == "absent"
    )


def test_encoded_percent_can_name_literal_escape() -> None:
    result = resolve_okf_path(
        "a%252Fb.md", "notes/current.md", {"notes/a%2Fb.md"}, "0.3"
    )
    assert (result.status, result.path) == ("resolved", "notes/a%2Fb.md")


def test_empty_body_reference_targets_source() -> None:
    result = resolve_okf_path(
        "", "notes/current.md", {"notes/current.md"}, None, path_field=False
    )
    assert (result.status, result.path, result.suffix) == (
        "resolved",
        "notes/current.md",
        "",
    )


def test_directory_target_resolves() -> None:
    result = resolve_okf_path("folder/", "notes/current.md", {"notes/folder/"}, "0.3")
    assert (result.status, result.path) == ("resolved", "notes/folder/")


def test_body_link_does_not_fallback_to_directory() -> None:
    result = resolve_okf_path(
        "folder/", "notes/current.md", {"folder/"}, "0.2", path_field=False
    )
    assert result.status == "unresolved"


@pytest.mark.parametrize("path_field", [True, False])
def test_single_letter_scheme_is_external(path_field: bool) -> None:
    result = resolve_okf_path(
        "C:/x.md", "notes/current.md", {"C:/x.md"}, "0.3", path_field=path_field
    )
    assert result.status == "external"
