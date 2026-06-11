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
    def test_exact_match_returns_empty(self) -> None:
        """A fully-matching old_text has no divergence to report."""
        result = find_closest_match("hello world", "hello world\ngoodbye")
        assert result == {}

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

    def test_single_line_file_full_match_returns_empty(self) -> None:
        result = find_closest_match("hello", "hello")
        assert result == {}

    def test_diff_position_reported(self) -> None:
        result = find_closest_match("abcXef", "abcdef")
        assert result["first_diff_char"] == 3

    def test_multiline_reports_divergent_line(self) -> None:
        """First line matches exactly; a later line is the real divergence."""
        old = "## Heading\nfirst line\nsecond lXne"
        file_content = "intro\n## Heading\nfirst line\nsecond line\ntail"
        result = find_closest_match(old, file_content)
        assert result["closest_match_line"] == 4
        assert result["first_diff_char"] == 8
        assert result["expected_snippet"] != result["found_snippet"]

    def test_multiline_first_line_match_not_reported_as_diff(self) -> None:
        """A perfectly-matching first line must not be reported as the diff."""
        # old_text differs from the file only by a zero-width space (U+200B)
        # on the third line; the first line matches the file byte-for-byte.
        old = "### The six links\n\nThe chain has​ six links"
        file_content = "### The six links\n\nThe chain has six links\n"
        result = find_closest_match(old, file_content)
        assert result["closest_match_line"] == 3
        assert result["first_diff_char"] == 13
        assert result["expected_snippet"] != result["found_snippet"]

    def test_old_text_extends_past_file(self) -> None:
        """old_text has more lines than the matched file region."""
        old = "alpha\nbeta\ngamma"
        file_content = "alpha\nbeta"
        result = find_closest_match(old, file_content)
        assert result["closest_match_line"] == 3
        assert result["found_snippet"] == ""
        assert result["expected_snippet"] == "gamma"

    def test_multiline_all_lines_match_returns_empty(self) -> None:
        """Every old_text line matches the file region — no diagnostic."""
        old = "alpha\nbeta\ngamma"
        file_content = "intro\nalpha\nbeta\ngamma\ntail"
        result = find_closest_match(old, file_content)
        assert result == {}


# ---------------------------------------------------------------------------
# BOM-stripping helpers
# ---------------------------------------------------------------------------


class TestUtf8BomHelpers:
    def test_read_text_utf8_strips_leading_bom(self, tmp_path) -> None:
        from markdown_vault_mcp.utils.text import read_text_utf8

        p = tmp_path / "bom.md"
        p.write_bytes(b"\xef\xbb\xbf# Title\n\nbody\n")  # UTF-8 BOM + content
        assert read_text_utf8(p) == "# Title\n\nbody\n"

    def test_read_text_utf8_passes_through_without_bom(self, tmp_path) -> None:
        from markdown_vault_mcp.utils.text import read_text_utf8

        p = tmp_path / "plain.md"
        p.write_text("# Title\n\nbody\n", encoding="utf-8")
        assert read_text_utf8(p) == "# Title\n\nbody\n"

    def test_read_text_utf8_raises_on_non_utf8(self, tmp_path) -> None:
        import pytest

        from markdown_vault_mcp.utils.text import read_text_utf8

        p = tmp_path / "bad.bin"
        p.write_bytes(b"\xff\xfe not valid utf-8 \x80\n")
        with pytest.raises(UnicodeDecodeError):
            read_text_utf8(p)

    def test_decode_utf8_strips_leading_bom(self) -> None:
        from markdown_vault_mcp.utils.text import decode_utf8

        assert (
            decode_utf8(b"\xef\xbb\xbf---\ntitle: x\n---\n") == "---\ntitle: x\n---\n"
        )

    def test_decode_utf8_passes_through_without_bom(self) -> None:
        from markdown_vault_mcp.utils.text import decode_utf8

        assert decode_utf8(b"plain text\n") == "plain text\n"

    def test_decode_utf8_raises_on_non_utf8(self) -> None:
        import pytest

        from markdown_vault_mcp.utils.text import decode_utf8

        with pytest.raises(UnicodeDecodeError):
            decode_utf8(b"\xff\xfe\x80")
