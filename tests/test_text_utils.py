"""Unit tests for text normalization and fuzzy matching utilities."""

from __future__ import annotations

from markdown_vault_mcp.utils.text import (
    build_position_map,
    find_closest_match,
    normalize_text,
)


class TestNormalizeText:
    def test_nfc_normalization(self) -> None:
        """NFC normalizes composed vs decomposed Unicode."""
        # e + combining acute accent → é (composed)
        decomposed = "caf\u0065\u0301"
        assert normalize_text(decomposed) == "caf\u00e9"

    def test_dashes_normalized(self) -> None:
        """En-dash and em-dash become hyphens."""
        assert normalize_text("a\u2013b\u2014c") == "a-b-c"

    def test_smart_quotes_normalized(self) -> None:
        """Smart quotes become straight quotes."""
        assert (
            normalize_text("\u201chello\u201d \u2018world\u2019") == "\"hello\" 'world'"
        )

    def test_whitespace_collapsed(self) -> None:
        """Multiple spaces/tabs collapse to single space within lines."""
        assert normalize_text("a   b\tc") == "a b c"

    def test_trailing_whitespace_stripped(self) -> None:
        """Trailing whitespace stripped per line."""
        assert normalize_text("hello   \nworld\t\n") == "hello\nworld\n"

    def test_newlines_preserved(self) -> None:
        """Newlines are not collapsed."""
        assert normalize_text("a\n\nb") == "a\n\nb"

    def test_no_change_passthrough(self) -> None:
        """Clean text passes through unchanged."""
        text = "hello world"
        assert normalize_text(text) == text

    def test_empty_string_passthrough(self) -> None:
        """Empty string remains empty."""
        assert normalize_text("") == ""


class TestBuildPositionMap:
    def test_identity_mapping(self) -> None:
        """When text is already normalized, positions map 1:1 plus sentinel."""
        text = "hello"
        pos_map = build_position_map(text, text)
        # 5 chars + 1 sentinel = 6 entries; sentinel equals len(original)
        assert pos_map == [0, 1, 2, 3, 4, 5]

    def test_dash_mapping(self) -> None:
        """Em-dash (1 char) maps to hyphen (1 char), sentinel = orig_len."""
        original = "a\u2014b"
        normalized = normalize_text(original)
        pos_map = build_position_map(original, normalized)
        # normalized is "a-b", length 3 → 4 entries (3 + sentinel)
        assert len(pos_map) == 4
        assert pos_map[0] == 0  # 'a' -> 'a'
        assert pos_map[1] == 1  # '—' -> '-'
        assert pos_map[2] == 2  # 'b' -> 'b'
        assert pos_map[3] == 3  # sentinel = orig_len

    def test_whitespace_collapse_mapping(self) -> None:
        """Multiple spaces collapse; map points to first original space."""
        original = "a   b"
        normalized = normalize_text(original)
        pos_map = build_position_map(original, normalized)
        # normalized is "a b", length 3 → 4 entries (3 + sentinel)
        assert len(pos_map) == 4
        assert pos_map[0] == 0  # 'a'
        assert pos_map[1] == 1  # first space of '   '
        assert pos_map[2] == 4  # 'b'
        assert pos_map[3] == 5  # sentinel = orig_len

    def test_trailing_ws_mapping(self) -> None:
        """Trailing whitespace stripped; mapping covers remaining chars."""
        original = "ab  "
        normalized = normalize_text(original)
        pos_map = build_position_map(original, normalized)
        # normalized is "ab", length 2 → 3 entries (2 + sentinel)
        assert len(pos_map) == 3
        assert pos_map[0] == 0
        assert pos_map[1] == 1
        assert pos_map[2] == 4  # sentinel = orig_len

    def test_nfc_multichar_mapping(self) -> None:
        """NFC decomposed sequence (2 chars) maps to original start index.

        Sentinel must equal orig_len so orig_end is computed correctly
        when the NFC sequence is the last character.
        """
        # 'e' + combining acute accent (U+0301) → 'é' (U+00E9) after NFC.
        original = "caf\u0065\u0301"  # 5 chars: c a f e ́
        normalized = normalize_text(original)  # "café" — 4 chars
        assert normalized == "caf\u00e9"
        pos_map = build_position_map(original, normalized)
        # 4 normalized chars + 1 sentinel = 5 entries
        assert len(pos_map) == 5
        assert pos_map[0] == 0  # 'c'
        assert pos_map[1] == 1  # 'a'
        assert pos_map[2] == 2  # 'f'
        assert pos_map[3] == 3  # 'e' (start of 2-char decomposed sequence)
        assert pos_map[4] == 5  # sentinel = orig_len (past the combining accent)

    def test_empty_mapping(self) -> None:
        """Empty inputs return just the sentinel [0]."""
        assert build_position_map("", "") == [0]


class TestFindClosestMatch:
    def test_close_match_found(self) -> None:
        """Returns diagnostic info when a close match exists."""
        old_text = "the quick\u2014brown fox"
        file_content = "line one\nthe quick-brown fox\nline three\n"
        diag = find_closest_match(old_text, file_content)
        assert diag["closest_match_line"] == 2
        assert diag["first_diff_char"] is not None
        assert diag["expected_snippet"] is not None
        assert diag["found_snippet"] is not None

    def test_no_close_match(self) -> None:
        """Returns empty dict when nothing is close."""
        old_text = "completely different text xyz123"
        file_content = "line one\nline two\nline three\n"
        diag = find_closest_match(old_text, file_content)
        assert diag == {}

    def test_exact_match_reports_line(self) -> None:
        """Even near-exact matches are reported with correct line number."""
        old_text = "hello world"
        file_content = "first\nhello worlds\nthird\n"
        diag = find_closest_match(old_text, file_content)
        assert diag["closest_match_line"] == 2

    def test_empty_file_no_match(self) -> None:
        """Matching against empty file returns empty dict."""
        assert find_closest_match("some text", "") == {}
