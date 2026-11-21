"""Decide whether a write is a trivial edit that keeps ``generated.at`` (#2418).

OKF 0.3 §5.2: "``generated.at``: The time of the last meaningful change to
the content. A trivial edit (format, whitespace, link repair) does not
change ``generated.at``." The spec gives examples, not a test, so the
server counts an edit as trivial only when a reader provably cannot see it
(account holder, 21 November 2026):

- the frontmatter is equal apart from ``generated`` and ``verified``, and
- the body parses to the same CommonMark document: equal markdown-it token
  streams, compared on what renders (type, tag, attributes, content, info
  string, children) and not on source positions or markup characters.

Everything else is meaningful. The error runs one way on purpose: calling a
real change trivial would keep a review older than the change counting
toward the trust tier, the outcome §5.3 exists to prevent, while calling a
trivial change meaningful only lowers the tier. Link repair by the server
(a rename's rewrites in other notes) never reaches the stamp at all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml
from markdown_it import MarkdownIt

from markdown_vault_mcp.scanner import note_body, parse_frontmatter

if TYPE_CHECKING:
    from markdown_it.token import Token

logger = logging.getLogger(__name__)

#: Keys the server owns on a stamped write; an edit to them is not content.
_SERVER_KEYS = frozenset({"generated", "verified"})

#: CommonMark with GFM tables, the body dialect OKF 0.3 §4.2 names.
_PARSER = MarkdownIt("commonmark").enable("table")


def _fingerprint(tokens: list[Token]) -> tuple[Any, ...]:
    """What a token stream renders, without positions or markup characters.

    An ``inline`` token's ``content`` is its raw source (``*a*`` and ``_a_``
    differ there); its children carry what renders, so a token with
    children is compared by them alone.
    """
    return tuple(
        (
            token.type,
            token.tag,
            tuple(sorted((str(k), str(v)) for k, v in (token.attrs or {}).items())),
            None if token.children else token.content,
            token.info,
            _fingerprint(token.children or []),
        )
        for token in tokens
    )


def _content_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    """The frontmatter a reader sees as content: all but the server's keys."""
    return {k: v for k, v in metadata.items() if k not in _SERVER_KEYS}


def is_trivial_edit(stored: str, written: str) -> bool:
    """Whether *written* changes nothing a reader of *stored* can see.

    Args:
        stored: The note's text on disk before the write.
        written: The text the write would save.

    Returns:
        ``True`` only when the frontmatter is equal apart from ``generated``
        and ``verified`` and the bodies parse to the same token stream;
        ``False`` otherwise, including when either text cannot be parsed.
    """
    try:
        before = parse_frontmatter(stored)
        after = parse_frontmatter(written)
    except (yaml.YAMLError, ValueError):
        return False
    if _content_meta(dict(before.metadata)) != _content_meta(dict(after.metadata)):
        return False
    old_body = note_body(stored)
    new_body = note_body(written)
    if old_body == new_body:
        return True
    trivial = _fingerprint(_PARSER.parse(old_body)) == _fingerprint(
        _PARSER.parse(new_body)
    )
    if trivial:
        logger.debug("okf_trivial_edit_detected chars_before=%d", len(old_body))
    return trivial
