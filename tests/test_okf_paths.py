"""Tests for the pure OKF URI-reference resolver."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
        ("https://example.org/a", "0.3", set(), ("external", None, "")),
        ("custom:opaque", "0.3", set(), ("external", None, "")),
        ("//example.org/a", "0.3", set(), ("external", None, "")),
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


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=25))
def test_percent_encoded_name_resolves(name: str) -> None:
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


@pytest.mark.parametrize("escape", ["%2F", "%2f", "%00", "%FF"])
@pytest.mark.parametrize("path_field", [True, False])
def test_refused_escapes_never_match(escape: str, path_field: bool) -> None:
    value = f"a{escape}b.md"
    files = {f"notes/{value}", value, "notes/a/b.md", "notes/a\x00b.md"}
    result = resolve_okf_path(
        value, "notes/current.md", files, "0.2", path_field=path_field
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


@pytest.mark.parametrize("version", [None, "0.3"])
def test_empty_path_field_is_absent(version: str | None) -> None:
    assert (
        resolve_okf_path("", "notes/current.md", {"notes/current.md"}, version).status
        == "absent"
    )


def test_encoded_percent_can_name_literal_escape() -> None:
    result = resolve_okf_path(
        "a%252Fb.md", "notes/current.md", {"notes/a%2Fb.md"}, "0.3"
    )
    assert (result.status, result.path) == ("resolved", "notes/a%2Fb.md")


@pytest.mark.parametrize("value", ["", "?view", "#part"])
def test_empty_body_reference_targets_source(value: str) -> None:
    result = resolve_okf_path(
        value, "notes/current.md", {"notes/current.md"}, None, path_field=False
    )
    assert (result.status, result.path, result.suffix) == (
        "resolved",
        "notes/current.md",
        value,
    )


def test_query_only_path_field_targets_source() -> None:
    result = resolve_okf_path("?view", "notes/current.md", {"notes/current.md"}, None)
    assert (result.status, result.path, result.suffix) == (
        "resolved",
        "notes/current.md",
        "?view",
    )
