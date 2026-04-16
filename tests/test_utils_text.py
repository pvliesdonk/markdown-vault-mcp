"""Tests for markdown_vault_mcp.utils.text."""

from __future__ import annotations

from markdown_vault_mcp.utils.text import (
    CHAR_SUBS,
    build_position_map,
    find_closest_match,
    normalize_text,
)

# ---------------------------------------------------------------------------
# CHAR_SUBS
# ---------------------------------------------------------------------------


class TestCharSubs:
    def test_contains_expected_keys(self) -> None:
        assert "\u2013" in CHAR_SUBS  # en-dash
        assert "\u2014" in CHAR_SUBS  # em-dash
        assert "\u201c" in CHAR_SUBS  # left double quote
        assert "\u201d" in CHAR_SUBS  # right double quote
        assert "\u2018" in CHAR_SUBS  # left single quote
        assert "\u2019" in CHAR_SUBS  # right single quote


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_smart_quotes_replaced(self) -> None:
        assert normalize_text("\u201chello\u201d") == '"hello"'
        assert normalize_text("\u2018hi\u2019") == "'hi'"

    def test_dashes_replaced(self) -> None:
        assert normalize_text("a\u2013b") == "a-b"
        assert normalize_text("a\u2014b") == "a-b"

    def test_whitespace_collapsed(self) -> None:
        assert normalize_text("a   b") == "a b"
        assert normalize_text("a\t\tb") == "a b"
        assert normalize_text("a \t b") == "a b"

    def test_trailing_whitespace_stripped(self) -> None:
        assert normalize_text("hello   ") == "hello"
        assert normalize_text("hello   \nworld  ") == "hello\nworld"

    def test_nfc_normalization(self) -> None:
        # e + combining acute → NFC é
        decomposed = "e\u0301"
        result = normalize_text(decomposed)
        assert result == "\u00e9"

    def test_newlines_preserved(self) -> None:
        assert normalize_text("a\nb\nc") == "a\nb\nc"

    def test_empty_string(self) -> None:
        assert normalize_text("") == ""

    def test_already_normalized(self) -> None:
        text = "hello world"
        assert normalize_text(text) == text


# ---------------------------------------------------------------------------
# build_position_map
# ---------------------------------------------------------------------------


class TestBuildPositionMap:
    def test_identity(self) -> None:
        """When original == normalized, positions are 0..n."""
        text = "hello"
        pm = build_position_map(text, text)
        assert pm == [0, 1, 2, 3, 4, 5]

    def test_sentinel_value(self) -> None:
        """Last entry equals len(original)."""
        original = "abc"
        normalized = normalize_text(original)
        pm = build_position_map(original, normalized)
        assert pm[-1] == len(original)

    def test_whitespace_collapse(self) -> None:
        """Multiple spaces collapse to one; positions map correctly."""
        original = "a   b"
        normalized = normalize_text(original)
        assert normalized == "a b"
        pm = build_position_map(original, normalized)
        # 'a' at 0, ' ' at 1 (first space), 'b' at 4
        assert pm[0] == 0  # 'a'
        assert pm[1] == 1  # space maps to first space
        assert pm[2] == 4  # 'b'
        assert pm[3] == 5  # sentinel

    def test_trailing_whitespace_stripped(self) -> None:
        original = "ab  "
        normalized = normalize_text(original)
        assert normalized == "ab"
        pm = build_position_map(original, normalized)
        assert pm[0] == 0  # 'a'
        assert pm[1] == 1  # 'b'
        assert pm[2] == len(original)  # sentinel

    def test_smart_quote_substitution(self) -> None:
        original = "\u201chello\u201d"
        normalized = normalize_text(original)
        assert normalized == '"hello"'
        pm = build_position_map(original, normalized)
        assert len(pm) == len(normalized) + 1

    def test_multiline(self) -> None:
        original = "a  \nb"
        normalized = normalize_text(original)
        assert normalized == "a\nb"
        pm = build_position_map(original, normalized)
        assert len(pm) == len(normalized) + 1
        assert pm[-1] == len(original)


# ---------------------------------------------------------------------------
# find_closest_match
# ---------------------------------------------------------------------------


class TestFindClosestMatch:
    def test_exact_match(self) -> None:
        result = find_closest_match("hello world", "hello world\ngoodbye")
        assert result["closest_match_line"] == 1

    def test_no_match(self) -> None:
        result = find_closest_match("xxxxxxxxx", "hello\nworld")
        assert result == {}

    def test_close_match(self) -> None:
        result = find_closest_match("hello wrld", "goodbye\nhello world\nfoo")
        assert result["closest_match_line"] == 2
        assert "first_diff_char" in result
        assert "expected_snippet" in result
        assert "found_snippet" in result

    def test_threshold_below_0_6(self) -> None:
        """Completely different strings return empty dict."""
        result = find_closest_match("abcdef", "xyzxyz\n123456")
        assert result == {}

    def test_single_line_file(self) -> None:
        result = find_closest_match("hello", "hello")
        assert result["closest_match_line"] == 1
        assert result["first_diff_char"] == 5  # past end

    def test_diff_position_reported(self) -> None:
        result = find_closest_match("abcXef", "abcdef")
        assert result["first_diff_char"] == 3
