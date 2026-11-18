"""Bounded property checks for selective authoring."""

import frontmatter
from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._okf_author import author_text


@settings(max_examples=60, deadline=None, database=None)
@given(
    title=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz ABC012", min_size=1, max_size=30
    ).filter(str.strip),
    body=st.text(alphabet="abcdefghijklmnopqrstuvwxyz 012", max_size=80),
)
def test_author_preserves_unselected_evidence_and_body(title: str, body: str) -> None:
    original = (
        "---\ntype: Guide\nverified: [{by: human:alice}] # evidence\nx-extra: retained\n---\n"
        + body
        + "\n"
    )
    result = author_text(original, {"title": title}, None)
    metadata = frontmatter.loads(result).metadata
    assert metadata["title"] == title
    assert metadata["verified"] == [{"by": "human:alice"}]
    assert "verified: [{by: human:alice}] # evidence\nx-extra: retained\n" in result
    assert result.endswith("---\n" + body + "\n")
    assert "generated" not in metadata
