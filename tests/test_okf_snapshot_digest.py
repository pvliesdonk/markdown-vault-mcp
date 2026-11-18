"""Snapshot identity for full OKF reads."""

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
