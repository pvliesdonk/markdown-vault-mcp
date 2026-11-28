"""A path outside the bundle carries no bundle metadata on read (#2451).

OKF 0.3 §3.2 rule 4: "Files and directories whose names start with `.` are
not part of the bundle." Such files stay vault files that search finds, but
the bundle the server presents does not hold them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._cli_read import read_payload
from markdown_vault_mcp._read_payloads import annotate_hits
from markdown_vault_mcp._tools._common import attach_okf
from markdown_vault_mcp.okf import derive_annotation, matches_okf_filters
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOTE = "---\ntype: Note\nstatus: stable\n---\n# Note\n\nshared words\n"


@pytest.fixture
def bundle(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.3"\n---\n# B\n')
    for rel in ("member.md", ".drafts/hidden.md", "notes/.wip.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(_NOTE)
    col = Vault(source_dir=root, settings=VaultSettings())
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def test_a_non_member_matches_no_okf_filter() -> None:
    meta = {"type": "Note"}
    assert matches_okf_filters(meta, {"status": "stable"}, path="a.md")
    assert not matches_okf_filters(meta, {"status": "stable"}, path=".d/a.md")
    assert matches_okf_filters(meta, {"status": "stable"})


def test_hits_outside_the_bundle_get_no_annotation() -> None:
    hits = [
        {"path": "a.md", "frontmatter": {"type": "Note"}},
        {"path": ".d/a.md", "frontmatter": {"type": "Note"}},
    ]
    annotate_hits(hits, derive_annotation)
    assert "okf" in hits[0]
    assert "okf" not in hits[1]


def test_read_annotates_members_only(bundle: Vault) -> None:
    assert "okf" in read_payload(bundle, "member.md")
    assert "okf" not in read_payload(bundle, ".drafts/hidden.md")
    assert "okf" not in read_payload(bundle, "notes/.wip.md")


async def test_attach_okf_reads_the_payload_path(bundle: Vault) -> None:
    member = await attach_okf(bundle, {"path": "member.md"}, {"type": "Note"})
    hidden = await attach_okf(bundle, {"path": ".drafts/x.md"}, {"type": "Note"})
    assert "okf" in member
    assert "okf" not in hidden


def test_search_still_finds_non_members(bundle: Vault) -> None:
    # Index admission is retrieval policy (north star, "Local exclusion is
    # not OKF"); only the bundle metadata is withheld.
    paths = {hit.path for hit in bundle.reader.search("shared words")}
    assert {"member.md", ".drafts/hidden.md", "notes/.wip.md"} <= paths


def test_okf_filters_and_stats_count_members_only(bundle: Vault) -> None:
    listed = {
        note.path for note in bundle.reader.list_documents(filters={"status": "stable"})
    }
    assert "member.md" in listed
    assert not any(p.startswith(".") or "/." in p for p in listed)
    stats = bundle.reader.okf_stats()
    assert stats is not None
    assert stats["types"] == {"Note": 1}
