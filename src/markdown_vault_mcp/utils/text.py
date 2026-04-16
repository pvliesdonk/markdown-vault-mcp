"""Text normalization and fuzzy-matching utilities for edit operations.

These functions support the edit workflow by normalizing text for comparison
while preserving exact original positions for replacement.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

# Direct single-character substitutions applied during normalization.
# Used by build_position_map to avoid calling normalize_text() per
# character, which would (a) be O(n²) and (b) incorrectly strip a lone
# space/tab as "trailing whitespace" of a one-char string.
CHAR_SUBS: dict[str, str] = {
    "\u2013": "-",  # en-dash
    "\u2014": "-",  # em-dash
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
}


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy edit matching.

    Applied to both old_text and file content for comparison only — the
    actual file replacement uses original bytes.

    Steps:
        1. Unicode NFC normalization.
        2. En-dash / em-dash → hyphen.
        3. Smart quotes → straight quotes.
        4. Collapse whitespace runs within lines (not across newlines).
        5. Strip trailing whitespace per line.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in lines]
    return "\n".join(lines)


def build_position_map(original: str, normalized: str) -> list[int]:
    """Map each normalized character index to its original character index.

    Walks both strings in parallel, advancing the original pointer past
    characters that were removed or merged by normalization.

    Args:
        original: The original (un-normalized) text.
        normalized: The result of ``normalize_text(original)``.

    Returns:
        A list of *len(normalized) + 1* entries where ``pos_map[i]`` is the
        index in *original* corresponding to ``normalized[i]``, and the final
        sentinel ``pos_map[len(normalized)]`` equals ``len(original)``.  The
        sentinel lets callers compute the original end-position of a match
        as ``pos_map[norm_end]`` without special-casing the last character.
    """
    pos_map: list[int] = []
    orig_idx = 0
    norm_idx = 0
    orig_len = len(original)
    norm_len = len(normalized)

    while norm_idx < norm_len:
        if orig_idx >= orig_len:
            # Safety: normalized should never be longer.
            break

        norm_char = normalized[norm_idx]
        orig_char = original[orig_idx]

        # Newlines anchor both streams.
        if norm_char == "\n" and orig_char == "\n":
            pos_map.append(orig_idx)
            orig_idx += 1
            norm_idx += 1
            continue

        # Trailing whitespace was stripped: skip original trailing ws
        # before a newline or end-of-string.
        if norm_char == "\n" or (norm_idx == norm_len - 1 and norm_char != "\n"):
            if norm_char != "\n":
                # last char of normalized, not a newline — emit it first
                pos_map.append(orig_idx)
                orig_idx += 1
                norm_idx += 1
            # skip trailing whitespace in original before newline/end
            while orig_idx < orig_len and original[orig_idx] in " \t":
                orig_idx += 1
            continue

        # Whitespace collapse: normalized has single space, original has one or
        # more spaces/tabs. Checked before the direct-match step so that runs
        # of whitespace are always consumed in full (a single space would pass
        # the direct-match test below, leaving trailing spaces unadvanced).
        if norm_char == " " and orig_char in " \t":
            pos_map.append(orig_idx)
            orig_idx += 1
            # skip remaining whitespace in original
            while orig_idx < orig_len and original[orig_idx] in " \t":
                orig_idx += 1
            norm_idx += 1
            continue

        # Direct character match (possibly after NFC + char substitution).
        # Using normalize_text(orig_char) would be O(n²) and would also
        # incorrectly strip a lone space as "trailing whitespace", so we
        # apply NFC and CHAR_SUBS directly instead.
        nfc_char = unicodedata.normalize("NFC", orig_char)
        sub_char = CHAR_SUBS.get(nfc_char, nfc_char)
        if sub_char == norm_char:
            pos_map.append(orig_idx)
            orig_idx += 1
            norm_idx += 1
            continue

        # Unicode NFC: original may have multiple chars for one normalized.
        # Try expanding original chars until they normalize to norm_char.
        consumed = 1
        while orig_idx + consumed <= orig_len:
            chunk = original[orig_idx : orig_idx + consumed]
            if unicodedata.normalize("NFC", chunk) == norm_char:
                pos_map.append(orig_idx)
                orig_idx += consumed
                norm_idx += 1
                break
            consumed += 1
        else:
            # Fallback: advance both by one.
            pos_map.append(orig_idx)
            orig_idx += 1
            norm_idx += 1

    # Sentinel: pos_map[norm_len] = orig_len so callers can compute
    # orig_end = pos_map[norm_end] for any norm_end including norm_len.
    pos_map.append(orig_len)
    return pos_map


def find_closest_match(old_text: str, file_content: str) -> dict[str, Any]:
    """Find the closest fuzzy match for diagnostic error reporting.

    Compares the first line of *old_text* against every line in the file
    using ``difflib.SequenceMatcher``.  If a match with ratio >= 0.6 is
    found, returns diagnostic info about the first character divergence.

    Args:
        old_text: The text the caller tried to match.
        file_content: The full file content.

    Returns:
        A dict with ``closest_match_line``, ``first_diff_char``,
        ``expected_snippet``, and ``found_snippet``; or an empty dict
        if no match with ratio >= 0.6 is found.
    """
    first_line = old_text.split("\n", 1)[0]
    file_lines = file_content.split("\n")
    best_ratio = 0.0
    best_line_num = 0
    best_line_text = ""

    for i, line in enumerate(file_lines, 1):
        ratio = SequenceMatcher(None, first_line, line).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_line_num = i
            best_line_text = line

    if best_ratio < 0.6:
        return {}

    # Find first character difference.
    diff_pos = 0
    min_len = min(len(first_line), len(best_line_text))
    while diff_pos < min_len and first_line[diff_pos] == best_line_text[diff_pos]:
        diff_pos += 1

    ctx = 30
    return {
        "closest_match_line": best_line_num,
        "first_diff_char": diff_pos,
        "expected_snippet": first_line[max(0, diff_pos - ctx) : diff_pos + ctx],
        "found_snippet": best_line_text[max(0, diff_pos - ctx) : diff_pos + ctx],
    }
