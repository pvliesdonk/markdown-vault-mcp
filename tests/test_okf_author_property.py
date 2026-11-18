"""Bounded property checks for selective authoring."""

import frontmatter
from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._okf_author import author_text


@settings(max_examples=60, deadline=None, database=None)
@given(
    title=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz ABC012,}:[]#", min_size=1, max_size=30
    ).filter(str.strip),
    body=st.text(alphabet="abcdefghijklmnopqrstuvwxyz 012", max_size=80),
    flow=st.booleans(),
)
def test_author_preserves_unselected_evidence_and_body(
    title: str, body: str, flow: bool
) -> None:
    evidence = (
        "{type: Guide, verified: [{by: human:alice}], x-extra: retained} # evidence\n"
        if flow
        else "type: Guide\nverified: [{by: human:alice}] # evidence\nx-extra: retained\n"
    )
    original = "---\n" + evidence + "---\n" + body + "\n"
    result = author_text(original, {"title": title}, None)
    metadata = frontmatter.loads(result).metadata
    assert metadata["title"] == title
    assert metadata["verified"] == [{"by": "human:alice"}]
    preserved = (
        "verified: [{by: human:alice}], x-extra: retained"
        if flow
        else "verified: [{by: human:alice}] # evidence\nx-extra: retained\n"
    )
    assert preserved in result
    assert "# evidence" in result
    assert result.endswith("---\n" + body + "\n")
    assert "generated" not in metadata
