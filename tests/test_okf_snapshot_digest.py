"""Snapshot identity for full OKF reads."""

from pathlib import Path

import pytest

from markdown_vault_mcp._okf_review import concept_snapshot


def test_snapshot_tracks_content_but_not_review_metadata() -> None:
    base = "---\ntype: concept\nsources: [a]\n---\nA [[link]]\n"
    digest = concept_snapshot(base)
    assert digest == {
        "algo": "mvmcp-concept-v1",
        "digest": "990a4fc08d76a9066ddbfe43436080a8e8caca131617ba89e7ab5db68705abb5",
    }
    assert digest == concept_snapshot(
        base.replace(
            "sources: [a]",
            "sources: [a]\nverified: [{by: human:x, at: 2026-01-01}]\nx-mvmcp: {reviews: []}",
        )
    )
    assert digest == concept_snapshot(base.replace("\n", "\r\n"))
    for changed in (
        base.replace("concept", "other"),
        base.replace("[a]", "[b]"),
        base.replace("[[link]]", "[[other]]"),
    ):
        assert concept_snapshot(changed) != digest


def test_yaml_values_are_deterministic_and_typed() -> None:
    date = "---\nvalue: 2026-01-01\n---\ntext\n"
    assert concept_snapshot(date) == concept_snapshot(date)
    assert concept_snapshot(date) != concept_snapshot(
        date.replace("2026-01-01", '"2026-01-01"')
    )


def test_leading_body_blank_line_changes_snapshot() -> None:
    note = "---\ntype: concept\n---\nBody\n"
    assert concept_snapshot(note) != concept_snapshot(
        note.replace("---\nBody", "---\n\nBody")
    )


def test_yaml_scalar_and_container_values_are_stable() -> None:
    note = """---
null_value: null
bool_value: true
integer: 12
float: 1.5
datetime: 2026-01-01T12:00:00Z
binary: !!binary aGVsbG8=
set: !!set {a: null, b: null}
list: [one, two]
---
Body
"""
    assert concept_snapshot(note) == concept_snapshot(note)
    assert concept_snapshot(note) != concept_snapshot(
        note.replace("integer: 12", 'integer: "12"')
    )


def test_ordered_yaml_pairs_and_mapping_order() -> None:
    note = "---\na: !!omap [{x: 1}, {y: 2}]\nb: !!pairs [{x: 1}]\n---\nBody\n"
    reordered = "---\nb: !!pairs [{x: 1}]\na: !!omap [{x: 1}, {y: 2}]\n---\nBody\n"
    assert concept_snapshot(note) == concept_snapshot(reordered)


def test_whitespace_before_frontmatter_changes_snapshot() -> None:
    note = "---\ntype: concept\n---\nBody\n"
    assert concept_snapshot(note) != concept_snapshot("\n" + note)


def test_concurrent_malformed_content_reports_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.exceptions import DocumentUnreadableError
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (tmp_path / "note.md").write_text("---\ntype: concept\n---\nBody\n")
    vault = Vault(source_dir=tmp_path)
    monkeypatch.setattr(
        "markdown_vault_mcp.managers.document._read_text_utf8",
        lambda _path: "---\ntype: [\n---\nBody\n",
    )
    try:
        with pytest.raises(DocumentUnreadableError, match=r"note\.md"):
            read_payload(vault, "note.md")
    finally:
        vault.close()


def test_full_read_handles_yaml_types(tmp_path: Path) -> None:
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    text = "---\na: !!omap [{x: 1}]\nb: !!pairs [{y: 2}]\nc: !!set {a: null}\nd: !!binary aGk=\ne: 2026-01-01T12:00:00Z\n---\nBody\n"
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path)
    try:
        snapshot = read_payload(vault, "note.md")["okf"]["snapshot"]
        assert snapshot == {
            "algo": "mvmcp-concept-v1",
            "digest": "c4ed5594b2839fefaff1608872890f151df18db4456d58878dfd7c12eb72240d",
        }
        (tmp_path / "note.md").write_text(text.replace("---\nBody", "---\n\nBody"))
        assert read_payload(vault, "note.md")["okf"]["snapshot"] != snapshot
    finally:
        vault.close()


def test_body_without_complete_frontmatter_is_hashed_verbatim() -> None:
    for text in ("Plain body\n", "---\ntype: incomplete\n"):
        assert concept_snapshot(text) != concept_snapshot(text + "extra\n")
