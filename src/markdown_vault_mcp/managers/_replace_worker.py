"""Find the spans one text replacement changes, within fixed bounds (#2031).

:class:`~markdown_vault_mcp.managers.text_replace.TextReplacer` matches a
literal pattern in-process with :func:`literal_spans`. A regular expression
can backtrack catastrophically and :mod:`re` has no timeout, so it runs this
file as a script in a separate ``python -I`` process that the caller kills
after a deadline. The design comes from #2187.

The module imports only the standard library: the isolated interpreter puts
neither the package nor the script's own directory on ``sys.path``.

Protocol: one JSON object on stdin, ``{"pattern", "replacement", "bodies",
"max_replacements", "max_output_chars"}``; one JSON object on stdout, either
``{"spans": [[[start, end, text], ...], ...]}`` with one list per body,
``{"limit": message}`` when a bound is exceeded, or ``{"error": message}``
when the pattern or replacement is invalid.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

#: One change: start and end offsets in the body, and the text replacing them.
Span = tuple[int, int, str]


class ReplacementLimitError(ValueError):
    """The replacement exceeded one of the request's fixed bounds."""


class _Budget:
    """Count replacements and replacement characters against the bounds."""

    def __init__(self, max_replacements: int, max_output_chars: int) -> None:
        self._max_replacements = max_replacements
        self._max_output_chars = max_output_chars
        self._replacements = 0
        self._chars = 0

    def spend(self, text: str) -> None:
        """Charge one replacement producing *text*, raising past a bound."""
        self._replacements += 1
        self._chars += len(text)
        if self._replacements > self._max_replacements:
            raise ReplacementLimitError(
                f"more than {self._max_replacements} replacements"
            )
        if self._chars > self._max_output_chars:
            raise ReplacementLimitError(
                f"more than {self._max_output_chars} characters of replacement text"
            )


def literal_spans(
    pattern: str,
    replacement: str,
    bodies: list[str],
    *,
    max_replacements: int,
    max_output_chars: int,
) -> list[list[Span]]:
    """Return every non-overlapping exact occurrence of *pattern* per body.

    Args:
        pattern: The exact text to find; must be non-empty.
        replacement: The text each occurrence becomes, used as given.
        bodies: The texts to search.
        max_replacements: Most occurrences allowed across all bodies.
        max_output_chars: Most replacement characters allowed across all bodies.

    Returns:
        One list of ``(start, end, replacement)`` spans per body, in order.

    Raises:
        ReplacementLimitError: A bound was exceeded.
    """
    budget = _Budget(max_replacements, max_output_chars)
    result: list[list[Span]] = []
    for body in bodies:
        spans: list[Span] = []
        start = body.find(pattern)
        while start != -1:
            budget.spend(replacement)
            end = start + len(pattern)
            spans.append((start, end, replacement))
            start = body.find(pattern, end)
        result.append(spans)
    return result


def regex_spans(
    pattern: str,
    replacement: str,
    bodies: list[str],
    *,
    max_replacements: int,
    max_output_chars: int,
) -> list[list[Span]]:
    """Return every match of the regular expression *pattern* per body.

    *replacement* is a :meth:`re.Match.expand` template, so ``\\1`` and
    ``\\g<name>`` insert capture groups.

    Args:
        pattern: A Python regular expression, inline flags allowed.
        replacement: The expansion template for each match.
        bodies: The texts to search.
        max_replacements: Most matches allowed across all bodies.
        max_output_chars: Most replacement characters allowed across all bodies.

    Returns:
        One list of ``(start, end, expanded)`` spans per body, in order.

    Raises:
        re.error: *pattern* does not compile.
        IndexError: *replacement* names a group *pattern* lacks.
        ReplacementLimitError: A bound was exceeded.
    """
    # Sonar S2631 (ReDoS) is accepted here: running the caller's pattern is the
    # feature. This function runs only in the `python -I` child that
    # text_replace._regex_spans starts with subprocess.run(timeout=...), which
    # kills and reaps it at the deadline, so a catastrophic pattern costs one
    # bounded process and a refusal, never the server (#2031). Nothing in the
    # server process calls it.
    compiled = re.compile(pattern)
    # An invalid template fails here even when no body matches.
    compiled.sub(replacement, "")
    budget = _Budget(max_replacements, max_output_chars)
    result: list[list[Span]] = []
    for body in bodies:
        spans: list[Span] = []
        # Still inside the child: the deadline above bounds every match here.
        for match in compiled.finditer(body):
            text = match.expand(replacement)
            budget.spend(text)
            spans.append((match.start(), match.end(), text))
        result.append(spans)
    return result


def run(data: dict[str, Any]) -> dict[str, Any]:
    """Answer one protocol request, as the script does on stdin and stdout.

    Args:
        data: The decoded request object.

    Returns:
        The response object: ``spans``, ``limit`` or ``error``.
    """
    try:
        spans = regex_spans(
            data["pattern"],
            data["replacement"],
            data["bodies"],
            max_replacements=data["max_replacements"],
            max_output_chars=data["max_output_chars"],
        )
    except ReplacementLimitError as exc:
        return {"limit": str(exc)}
    except (re.error, IndexError, ValueError, OverflowError, RecursionError) as exc:
        return {"error": str(exc)}
    return {"spans": spans}


if __name__ == "__main__":
    json.dump(run(json.load(sys.stdin)), sys.stdout, ensure_ascii=True)
