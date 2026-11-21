"""YAML 1.2 core-schema loading, the frontmatter dialect OKF 0.3 requires.

OKF 0.3 §4: "The frontmatter MUST parse as a YAML 1.2 mapping with the core
schema." PyYAML — and therefore ``python-frontmatter``, whose ``YAMLHandler``
calls ``yaml.load(..., Loader=SafeLoader)`` — resolves plain scalars by the
YAML 1.1 rules, so ``yes``/``on`` become booleans, ``012`` becomes octal 10,
``1:30`` becomes sexagesimal 90, ``1_000`` becomes 1000, and an unquoted
leap-second timestamp makes the whole block fail to load (#2415). The YAML
1.2 core schema defines none of those: only ``true``/``false`` (in three
capitalisations) are booleans, integers are decimal, ``0o``-octal or
``0x``-hex, floats are the dotted/exponent forms plus ``.inf``/``.nan``, and
everything else — ``yes``, ``1:30``, ``1_000``, dates and date-times — is a
string.

This module subclasses ``yaml.SafeLoader`` with the core schema's implicit
resolvers (the regexps of YAML 1.2.2 §10.3.2) and matching constructors, so
``parse_frontmatter`` resolves exactly as the spec requires while keeping
PyYAML's parser, merge-key and explicit-tag (``!!str``, ``!!timestamp``, ...)
support. SafeLoader's own resolver table is untouched: the subclass starts
from an empty table and registers only the core-schema tags.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

__all__ = ["Yaml12CoreLoader", "load_core_schema"]

# The YAML 1.2.2 core-schema resolution regexps (spec §10.3.2, Example 10.9).
# Unlike PyYAML's YAML 1.1 patterns they carry no underscores, no `yes`/`on`,
# no leading-zero octal, no sexagesimal and no timestamp.
_NULL_RE = re.compile(r"^(?:~|null|Null|NULL|)$")
_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT_RE = re.compile(r"^(?:[-+]?(?:0x[0-9a-fA-F]+|0o[0-7]+|[0-9]+))$")
_FLOAT_RE = re.compile(
    r"^(?:"
    r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)"
    r"|\.(?:nan|NaN|NAN)"
    r")$"
)
# The `<<` merge key is a yaml.org convention, not a YAML 1.1 type, so keeping
# it does not depart from the core schema's scalar resolution; dropping it
# would silently change every vault that already uses merge keys.
_MERGE_RE = re.compile(r"^<<$")


class Yaml12CoreLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that resolves plain scalars by the YAML 1.2 core schema.

    Same parser, same safety, different tag resolution: PyYAML's SafeLoader
    is a YAML 1.1 resolver, this one is the YAML 1.2 core schema OKF 0.3 §4
    mandates for frontmatter (#2415).
    """


# Start from an empty implicit-resolver table. ``add_implicit_resolver`` only
# copies the inherited table when the subclass does not define its own, so
# assigning here first keeps SafeLoader's YAML 1.1 tags (bool `yes`/`on`, octal
# `012`, sexagesimal `1:30`, timestamp, ...) out of this loader entirely.
Yaml12CoreLoader.yaml_implicit_resolvers = {}

Yaml12CoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null", _NULL_RE, ["~", "n", "N", ""]
)
Yaml12CoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", _BOOL_RE, ["t", "T", "f", "F"]
)
Yaml12CoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", _INT_RE, list("-+0123456789")
)
Yaml12CoreLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float", _FLOAT_RE, list("-+.0123456789")
)
Yaml12CoreLoader.add_implicit_resolver("tag:yaml.org,2002:merge", _MERGE_RE, ["<"])


def _construct_bool(loader: Yaml12CoreLoader, node: yaml.ScalarNode) -> bool:
    """Core-schema boolean: only the ``true``/``false`` spellings are bool."""
    value = loader.construct_scalar(node)
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    # An explicit `!!bool` on a non-core spelling defers to SafeConstructor's
    # table, so tagged documents keep PyYAML's reading of the tag.
    return yaml.constructor.SafeConstructor.construct_yaml_bool(loader, node)


def _construct_int(loader: Yaml12CoreLoader, node: yaml.ScalarNode) -> int:
    """Core-schema integer: decimal, ``0o`` octal or ``0x`` hex — no 1.1 octal."""
    value = loader.construct_scalar(node)
    sign = 1
    if value[0] in "+-":
        if value[0] == "-":
            sign = -1
        value = value[1:]
    if value[:2].lower() == "0x":
        return sign * int(value[2:], 16)
    if value[:2].lower() == "0o":
        return sign * int(value[2:], 8)
    # `012` is twelve in the core schema, not octal ten: plain base 10.
    return sign * int(value, 10)


def _construct_float(loader: Yaml12CoreLoader, node: yaml.ScalarNode) -> float:
    """Core-schema float: dotted/exponent forms plus ``.inf`` and ``.nan``."""
    value = loader.construct_scalar(node)
    sign = 1.0
    if value[0] in "+-":
        if value[0] == "-":
            sign = -1.0
        value = value[1:]
    lowered = value.lower()
    if lowered == ".inf":
        return sign * float("inf")
    if lowered == ".nan":
        return float("nan")
    # No sexagesimal, no underscores: the resolver only tags core-schema forms.
    return sign * float(value)


# add_constructor copies the inherited constructor table the same way, so only
# this subclass sees the core-schema constructors; SafeLoader keeps PyYAML's.
Yaml12CoreLoader.add_constructor("tag:yaml.org,2002:bool", _construct_bool)
Yaml12CoreLoader.add_constructor("tag:yaml.org,2002:int", _construct_int)
Yaml12CoreLoader.add_constructor("tag:yaml.org,2002:float", _construct_float)


def load_core_schema(text: str) -> Any:
    """Parse *text* as YAML under the 1.2 core schema.

    Args:
        text: A YAML document (typically one frontmatter block).

    Returns:
        The constructed document. Plain scalars resolve by the YAML 1.2 core
        schema, so ``yes`` stays ``"yes"``, ``0o17`` is ``15``, ``1:30`` stays
        ``"1:30"`` and a date-time stays a string.

    Raises:
        yaml.YAMLError: *text* is not well-formed YAML.
    """
    return yaml.load(text, Loader=Yaml12CoreLoader)
