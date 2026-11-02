"""Independent edit-distance oracle for eligible ASCII strings."""

from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._path_search import _within_one


def distance(left: str, right: str) -> int:
    """Compute full Levenshtein distance using a reference dynamic program."""
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        previous, row = row, [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
    return row[-1]


@given(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        min_size=3,
        max_size=64,
    ),
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        min_size=3,
        max_size=64,
    ),
)
def test_edit_decision_agrees_with_reference(left: str, right: str) -> None:
    """Threshold decisions agree with independently computed distance."""
    assert _within_one(left.lower(), right.lower()) == (
        distance(left.lower(), right.lower()) <= 1
    )


@given(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=3, max_size=64),
    st.integers(min_value=0),
    st.sampled_from(["insert", "delete", "replace"]),
)
def test_one_edit_is_accepted(value: str, position: int, operation: str) -> None:
    """Near neighbors exercise the accepting direction across token lengths."""
    pos = position % len(value)
    changed = {
        "insert": value[:pos] + "x" + value[pos:],
        "delete": value[:pos] + value[pos + 1 :],
        "replace": value[:pos] + "x" + value[pos + 1 :],
    }[operation]
    assert _within_one(value, changed)


@given(st.text(max_size=300))
def test_request_validation_matches_codepoint_contract(query: str) -> None:
    """Both accept and reject directions cover unrestricted Unicode input."""
    import pytest

    from markdown_vault_mcp.query_types import PathQuery

    valid = bool(query.strip()) and len(query) <= 256 and "\x00" not in query
    if valid:
        assert PathQuery(query=query).query == query
    else:
        with pytest.raises(ValueError):
            PathQuery(query=query)


# Index construction is subject to host scheduling; product time uses an injected clock.
@settings(deadline=None)
@given(
    st.lists(
        st.sampled_from(
            [
                "abc",
                "abd",
                "acb",
                "STRASSE",
                "Straße",
                "東京",
                "A_b",
                "a*b",
                "other",
                "zeta",
            ]
        ),
        unique=True,
        max_size=10,
    ),
    st.sampled_from(
        [
            "abc",
            "abx",
            "STRASSE",
            "a*b",
            "a_b",
            "東京",
            "abc abd",
            "notes/abc.md",
            "notes/ab",
            "notes/STRASSE.md",
        ]
    ),
    st.integers(min_value=1, max_value=1000),
    st.booleans(),
)
def test_public_results_equal_independent_ranked_union(
    names: list[str], query: str, limit: int, fuzzy: bool
) -> None:
    """Public discovery returns the exact ordered union, not just valid-looking hits."""
    import re

    from markdown_vault_mcp._path_search import search_paths
    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.query_types import PathQuery
    from tests.fixtures.structured_queries import add_note

    def tokens(value: str) -> list[str]:
        # An independent extraction formulation: delimiters separate whole pieces.
        pieces = re.sub(r"[/\s_.-]", " ", value).split()
        return [
            p.lower()
            for p in pieces
            if p.isascii() and p.isalnum() and 3 <= len(p) <= 64
        ]

    paths = [f"notes/{name}.md" for name in names]
    query_tokens = tokens(query)
    tiers: dict[str, int] = {}
    for path in paths:
        if path == query:
            tiers[path] = 0
        elif path.casefold().startswith(query.casefold()):
            tiers[path] = 1
        elif query.casefold() in path.casefold():
            tiers[path] = 2
        elif (
            fuzzy
            and query.isascii()
            and query_tokens
            and all(
                min((distance(t, c) for c in tokens(path)), default=100) <= 1
                for t in query_tokens
            )
        ):
            tiers[path] = 3
    expected = sorted(tiers, key=lambda p: (tiers[p], p.casefold(), p))
    index = FTSIndex(":memory:")
    try:
        for path in reversed(paths):
            add_note(index, path)
        result = search_paths(
            index, PathQuery(query=query, fuzzy=fuzzy, limit=limit), clock=lambda: 0
        )
        assert [item["path"] for item in result.items] == expected[:limit]
        assert result.truncated == (len(expected) > limit)
    finally:
        index.close()
