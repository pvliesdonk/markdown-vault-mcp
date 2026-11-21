"""Change single top-level frontmatter keys without re-serializing the rest (#2419).

OKF 0.3 §4: "A producer that modifies an existing file SHOULD NOT
re-serialize values that it did not change." The server's own writes change
at most ``generated`` and ``verified``, so :func:`splice_keys` rewrites only
those keys' lines and leaves every other byte of the file as written,
comments, quoting and the body included.

A splice is kept only when the result re-reads to exactly the frontmatter
the caller intended and the same body; otherwise :func:`splice_keys`
returns ``None`` and the caller re-serializes the whole block as before. A
block the line scan cannot read with certainty (CRLF line endings, a key
written twice, a flow-style top-level mapping) therefore always falls back.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from markdown_vault_mcp._yaml12 import InteropDumper

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Marker for :func:`splice_keys`: delete this key from the block.
REMOVE: Any = object()

_DELIMITER = "---"


def _key_line(key: str) -> re.Pattern[str]:
    """A column-0 line that starts *key*, plain or quoted, as a mapping key."""
    name = re.escape(key)
    return re.compile(rf"^(?:{name}|\"{name}\"|'{name}')[ \t]*:(?:[ \t]|$)")


def _continues(line: str) -> bool:
    """Whether *line* belongs to the key above it rather than starting another.

    Indented lines and blank lines continue a value; so does a column-0
    ``- `` item, which YAML allows for a block sequence under a mapping key.
    A column-0 comment starts the next key's region.
    """
    return line.strip() == "" or line[0] in " \t" or line.startswith(("- ", "-\n"))


class _Ambiguous(Exception):
    """The line scan cannot place a key with certainty."""


def _span(lines: list[str], key: str) -> tuple[int, int] | None:
    """The ``[start, end)`` line range of *key*'s entry in *lines*.

    Returns ``None`` when the key is absent. Trailing blank lines stay
    outside the range.

    Raises:
        _Ambiguous: If the key starts more than one line.
    """
    pattern = _key_line(key)
    starts = [i for i, line in enumerate(lines) if pattern.match(line)]
    if not starts:
        return None
    if len(starts) > 1:
        raise _Ambiguous(key)
    start = starts[0]
    end = start + 1
    while end < len(lines) and _continues(lines[end]):
        end += 1
    while end > start + 1 and lines[end - 1].strip() == "":
        end -= 1
    return start, end


def _dump_key(key: str, value: Any) -> list[str]:
    """One key's entry, dumped as the interop dumper writes it."""
    text = yaml.dump(
        {key: value},
        Dumper=InteropDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return text.splitlines(keepends=True)


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Line indexes of the opening and closing ``---`` of a leading block."""
    if not lines or lines[0].rstrip("\n") != _DELIMITER:
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\n") == _DELIMITER:
            return 0, index
    return None


def _apply(block: list[str], updates: Mapping[str, Any]) -> list[str]:
    """Rewrite *block* (the lines between the delimiters) for *updates*.

    Raises:
        _Ambiguous: If a key to change occurs more than once.
    """
    for key, value in updates.items():
        span = _span(block, key)
        if span is None:
            if value is not REMOVE:
                block = [*block, *_dump_key(key, value)]
            continue
        start, end = span
        replacement = [] if value is REMOVE else _dump_key(key, value)
        block = [*block[:start], *replacement, *block[end:]]
    return block


def splice_keys(
    text: str, updates: Mapping[str, Any], *, expected: Mapping[str, Any]
) -> str | None:
    """Set or remove top-level frontmatter keys, keeping every other byte.

    Args:
        text: The full note text, frontmatter block first.
        updates: Key to new value, or :data:`REMOVE` to delete the key. A
            new key is appended at the end of the block.
        expected: The whole frontmatter mapping the result must read back
            as; the splice is discarded when it does not.

    Returns:
        The spliced text, or ``None`` when *text* has no leading block, the
        block is outside what the line scan handles, or the result does not
        read back as *expected* with the same body.
    """
    from markdown_vault_mcp.scanner import parse_frontmatter

    if "\r" in text:
        return None
    lines = text.splitlines(keepends=True)
    bounds = _block_bounds(lines)
    if bounds is None:
        return None
    opening, closing = bounds
    try:
        block = _apply(lines[opening + 1 : closing], updates)
    except _Ambiguous:
        logger.debug("frontmatter_splice_ambiguous keys=%s", ",".join(updates))
        return None
    spliced = "".join([lines[opening], *block, *lines[closing:]])
    try:
        before = parse_frontmatter(text)
        after = parse_frontmatter(spliced)
    except (yaml.YAMLError, ValueError):
        logger.debug("frontmatter_splice_unreadable keys=%s", ",".join(updates))
        return None
    if dict(after.metadata) != dict(expected) or after.content != before.content:
        logger.debug("frontmatter_splice_mismatch keys=%s", ",".join(updates))
        return None
    return spliced
