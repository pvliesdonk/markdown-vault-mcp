"""Written link targets are RFC 3986 URI references (OKF 0.3 §6, #2417).

OKF 0.3 §6: "Link targets and path field values are URI references (RFC
3986). A value that contains a space or another reserved character MUST be
percent-encoded". RFC 3986 §3.3 admits in a path segment only ``pchar`` —
``unreserved / pct-encoded / sub-delims / ":" / "@"`` — and §4.2 bars a
``:`` from the first segment of a relative-path reference. Every producer
that writes a destination (the index builder, the wikilink converter and so
the bundle export, and the rename rewriter) must emit that grammar, and the
scanner must read each one back onto the note it names.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.okf import build_index_markdown, convert_wikilinks_to_markdown
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import OutlinkInfo
from markdown_vault_mcp.utils.links import (
    build_plain_destination,
    compute_new_raw_target,
    encode_uri_path,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

#: RFC 3986 §3.3 ``pchar`` and §3.5 ``fragment``, ``pct-encoded`` included.
_PCHAR = r"(?:[A-Za-z0-9\-._~!$&'()*+,;=:@]|%[0-9A-Fa-f]{2})"
_PCHAR_NC = r"(?:[A-Za-z0-9\-._~!$&'()*+,;=@]|%[0-9A-Fa-f]{2})"
_FRAGMENT = rf"(?:#(?:{_PCHAR}|[/?])*)?"
#: ``path-absolute`` or ``path-noscheme`` (§4.2), then an optional fragment.
_RE_URI_REFERENCE = re.compile(
    rf"(?:/(?:{_PCHAR}+(?:/{_PCHAR}*)*)?|{_PCHAR_NC}+(?:/{_PCHAR}*)*){_FRAGMENT}"
)

#: Names that exercise every class RFC 3986 and CommonMark treat apart.
NAMES = {
    "percent": "50% done.md",
    "hash": "a#b.md",
    "leading-hash": "#top.md",
    "query": "what?.md",
    "brackets": "b[1].md",
    "balanced-parens": "a (c).md",
    "open-paren": "a(b.md",
    "close-paren": "a)b.md",
    "space": "my note.md",
    "accents": "café/día.md",
    "emoji": "emoji-🙂.md",
    "literal-escape-2f": "dir%2Fnote.md",
    "literal-escape-00": "nul%00.md",
    "literal-escape-41": "x%41.md",
    "entity": "a&amp;b.md",
    "ampersand": "Q&A.md",
    "backslash": "back\\slash.md",
    "first-segment-colon": "draft:v2.md",
    "later-colon": "sub/draft:v2.md",
    "angle": "a<b>.md",
    "backtick": "co`de.md",
    "issue-example": "50% done/a#b (c).md",
}


def _wl(raw_target: str, target_path: str, fragment: str | None = None) -> OutlinkInfo:
    return OutlinkInfo(
        target_path=target_path,
        link_text=raw_target,
        link_type="wikilink",
        raw_target=raw_target,
        fragment=fragment,
        exists=True,
    )


def _only_link(content: str, source: str = "src.md") -> tuple[str, str, str | None]:
    links = extract_links(content, source)
    assert len(links) == 1, links
    return links[0].raw_target, links[0].target_path, links[0].fragment


def _written_by_every_producer(name: str) -> list[tuple[str, str]]:
    """``(content, source)`` for each producer, linking to *name*."""
    index = build_index_markdown("I", [("T", f"/{name}", None)]).splitlines()[-1]
    converted, _, _ = convert_wikilinks_to_markdown("[[T]]", [_wl("T", name)])
    rooted = compute_new_raw_target(
        "markdown", "/old.md", None, name, source_path="src.md", old_path="old.md"
    )
    relative = compute_new_raw_target(
        "markdown", "old.md", None, name, source_path="src.md", old_path="old.md"
    )
    return [
        (index, "index.md"),
        (converted, "src.md"),
        (f"[t]({rooted})", "src.md"),
        (f"[t]({relative})", "src.md"),
    ]


class TestTheIssueExample:
    def test_the_reported_name_is_written_as_rfc_3986_requires(self) -> None:
        assert (
            build_plain_destination("50% done/a#b (c).md")
            == "50%25%20done/a%23b%20(c).md"
        )


class TestEveryProducerWritesAUriReference:
    @pytest.mark.parametrize("name", NAMES.values(), ids=NAMES.keys())
    def test_the_destination_matches_the_rfc_3986_grammar(self, name: str) -> None:
        for content, source in _written_by_every_producer(name):
            raw, _target, _fragment = _only_link(content, source)
            assert _RE_URI_REFERENCE.fullmatch(raw), raw
            assert "\\" not in raw, raw

    @pytest.mark.parametrize("name", NAMES.values(), ids=NAMES.keys())
    def test_the_scanner_reads_it_back_onto_the_same_name(self, name: str) -> None:
        for content, source in _written_by_every_producer(name):
            _raw, target, fragment = _only_link(content, source)
            assert (target, fragment) == (name, None), content

    def test_a_generated_fragment_is_encoded_as_section_3_5_allows(self) -> None:
        written = build_plain_destination("/n.md", "Café [1] (x?/y")
        assert written == "/n.md#Caf%C3%A9%20%5B1%5D%20%28x?/y"
        assert _RE_URI_REFERENCE.fullmatch(written)
        _raw, target, _fragment = _only_link(f"[t]({written})")
        assert target == "n.md"


class TestWhatAPathSegmentKeepsLiteral:
    @pytest.mark.parametrize(
        "path",
        ["guides/note.md", "a(b).md", "x!$'*+,;=@.md", "sub/a:b.md", "/a:b.md"],
        ids=["plain", "parens", "sub-delims", "later-colon", "rooted-colon"],
    )
    def test_pchar_is_left_alone(self, path: str) -> None:
        # §3.3 admits sub-delims, ":" and "@" in a segment (``&`` aside, which
        # a markdown reader decodes as an entity first).
        assert encode_uri_path(path) == path

    def test_a_relative_first_segment_colon_gets_a_dot_segment(self) -> None:
        # §4.2: it would be mistaken for a scheme name, and "must be
        # preceded by a dot-segment"; the scanner agrees it is external.
        assert encode_uri_path("draft:v2.md") == "./draft:v2.md"

    def test_an_encoded_original_gets_the_dot_segment_too(self) -> None:
        raw = compute_new_raw_target(
            "markdown", "old%20x.md", None, "draft:v2.md", "src.md", "old x.md"
        )
        assert raw == "./draft%3Av2.md"
        assert _only_link(f"[t]({raw})")[1] == "draft:v2.md"

    def test_non_ascii_is_encoded_as_utf8_octets(self) -> None:
        assert encode_uri_path("café.md") == "caf%C3%A9.md"


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    col = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


class TestEndToEnd:
    @pytest.mark.parametrize(
        "name",
        ["50% done/a#b (c).md", "what?/b[1].md", "café #1.md", "draft:v2.md"],
        ids=["issue", "query-brackets", "non-ascii-hash", "colon"],
    )
    def test_a_rename_rewrites_to_a_link_that_resolves(
        self, tmp_path: Path, vault: Vault, name: str
    ) -> None:
        (tmp_path / "target.md").write_text("# Target\n", encoding="utf-8")
        (tmp_path / "src.md").write_text("See [it](target.md).\n", encoding="utf-8")
        vault.index.build_index()

        result = vault.writer.rename("target.md", name, update_links=True)

        assert result.updated_links == 1
        content = (tmp_path / "src.md").read_text(encoding="utf-8")
        raw, target, _fragment = _only_link(content)
        assert _RE_URI_REFERENCE.fullmatch(raw), raw
        assert target == name
        wait_for_writer_drain(vault)
        assert [b.source_path for b in vault.graph.get_backlinks(name)] == ["src.md"]

    def test_a_generated_index_links_resolve(
        self, tmp_path: Path, vault: Vault
    ) -> None:
        folder = tmp_path / "50% done"
        folder.mkdir()
        (folder / "a#b (c).md").write_text("# T\n", encoding="utf-8")
        vault.index.build_index()

        vault.writer.okf_generate_index(folder="50% done")

        body = (folder / "index.md").read_text(encoding="utf-8")
        assert "(/50%25%20done/a%23b%20(c).md)" in body
        targets = {
            link.target_path for link in extract_links(body, "50% done/index.md")
        }
        assert targets == {"50% done/a#b (c).md"}
