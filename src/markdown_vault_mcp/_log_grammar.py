"""The log-call grammar: ``event_name key=value`` templates.

Ported from ``fastmcp_pvl_core._log_grammar`` 10.2.0 (#2340). The renderer,
``_log_render``, reads it too (#2342).

The logging standard every first-party module follows (the
``logging-standard`` skill) writes
``logger.info("event_name key=%s", value)``. Standard library logging keeps
the two halves of that call apart on the record — ``record.msg`` is the
template the developer wrote, ``record.args`` holds the values — so a
renderer can recover typed fields by parsing the *template* and pairing each
placeholder with its argument. This module owns that parse.

It deliberately never looks at a rendered message. A value is whatever the
caller passed; it is never re-read out of formatted text, so a value
containing a space, an ``=`` or a quote cannot confuse the result.

Non-conforming templates are not an error here. ``parse_log_template``
returns ``None`` and callers fall back to the formatted message — a log call
must never raise because of how its message was phrased.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# ``event`` and field names are snake_case; JSON keys come straight from the
# latter, so no dots or dashes that an aggregator would read as structure.
_EVENT = r"[a-z][a-z0-9_]*"
_NAME = r"[a-z][a-z0-9_]*"
# One %-conversion, e.g. ``%s`` ``%d`` ``%.1f`` ``%r``. ``%%`` is excluded by
# construction: its second character is not a type character. The type
# character set is deliberately narrow — it is the subset of %-conversions
# the logging standard uses, so an exotic one such as ``%a`` or ``%c`` is
# treated as non-conforming rather than silently accepted.
_CONVERSION = r"%[-#0+]*\d*(?:\.\d+)?[sdifeEgGxXor]"
# A fixed value, e.g. ``status=configured``. No space (it would start a new
# field), no ``%`` (it would be a malformed placeholder), no ``=``.
_LITERAL = r"[^\s%=]+"

_VALUE = rf"(?:{_CONVERSION}|{_LITERAL})"
_TEMPLATE_RE = re.compile(rf"\A{_EVENT}(?: {_NAME}={_VALUE})*\Z")
_FIELD_RE = re.compile(
    rf"\A(?P<name>{_NAME})=(?:(?P<conv>{_CONVERSION})|(?P<lit>{_LITERAL}))\Z"
)


@dataclass(frozen=True)
class LogField:
    """One ``name=value`` field of a conforming template.

    Exactly one of *conversion* and *literal* is set: a placeholder field
    consumes the next positional argument, a literal field carries its own
    value and consumes none.
    """

    name: str
    conversion: str | None
    literal: str | None


@dataclass(frozen=True)
class LogTemplate:
    """A parsed conforming template: its event name and its fields, in order."""

    event: str
    fields: tuple[LogField, ...]

    @property
    def placeholder_count(self) -> int:
        """How many positional arguments this template consumes."""
        return sum(1 for field in self.fields if field.conversion is not None)


@lru_cache(maxsize=1024)
def parse_log_template(template: str) -> LogTemplate | None:
    """Parse *template* into its event and fields, or ``None`` if it does not conform.

    Conforming means: a snake_case event name, then zero or more
    space-separated ``name=value`` fields, where a value is either a single
    %-conversion or a fixed token containing no space, ``%`` or ``=``.

    Cached, because the same template string is re-parsed on every record
    that logging call emits; the cache is keyed on the template, so the
    per-record cost after the first is a dictionary lookup.
    """
    if not _TEMPLATE_RE.match(template):
        return None

    event, *raw_fields = template.split(" ")
    fields: list[LogField] = []
    for raw in raw_fields:
        match = _FIELD_RE.match(raw)
        if match is None:  # pragma: no cover - _TEMPLATE_RE already rejected it
            return None
        fields.append(
            LogField(
                name=match.group("name"),
                conversion=match.group("conv"),
                literal=match.group("lit"),
            )
        )
    return LogTemplate(event=event, fields=tuple(fields))
