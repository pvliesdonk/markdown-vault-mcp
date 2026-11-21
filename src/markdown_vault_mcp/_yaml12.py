"""YAML 1.2 core-schema reading and writing for note frontmatter (#2415).

OKF 0.3 §4: "The frontmatter MUST parse as a YAML 1.2 mapping with the core
schema." PyYAML resolves plain scalars by YAML 1.1 (``yes`` is ``True``,
``1:30`` is 90, a date is a ``date``), so this module swaps in the YAML 1.2.2
§10.3.2 core-schema resolution on PyYAML's C loader and dumper. What the
spec says, and how PyYAML differs, is recorded in
``docs/design/reference/yaml-1-2-core-schema.md``.

Reading resolves exactly the core schema: null, bool, int (base 10, ``0o``
octal, ``0x`` hex) and float; every other plain scalar is a string, dates
and timestamps included. Explicit tags (``!!timestamp``) keep working.

Writing quotes a string whenever a YAML 1.1 *or* a YAML 1.2 reader would
take its plain spelling for something other than a string. A 1.1-only
dumper writes ``0o17`` and ``1e3`` unquoted, which a 1.2 reader then loads
as the numbers 15 and 1000.0; a 1.2-only dumper would write ``yes``
unquoted, which every 1.1 reader (PyYAML included) loads as ``True``.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

import yaml
from frontmatter.default_handlers import YAMLHandler

_NULL = "tag:yaml.org,2002:null"
_BOOL = "tag:yaml.org,2002:bool"
_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"

#: YAML 1.2.2 §10.3.2, the core schema's regular expressions, in table order.
#: Each entry is (tag, pattern, first characters PyYAML indexes it under).
_CORE_SCHEMA: tuple[tuple[str, str, str], ...] = (
    (_NULL, r"(?:null|Null|NULL|~|)", "nN~"),
    (_BOOL, r"(?:true|True|TRUE|false|False|FALSE)", "tTfF"),
    (_INT, r"[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+", "-+0123456789"),
    (
        _FLOAT,
        r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
        r"|[-+]?(?:\.inf|\.Inf|\.INF)|\.nan|\.NaN|\.NAN",
        "-+.0123456789",
    ),
)


def _install_core_schema(cls: type[Any]) -> None:
    """Give *cls* the core schema's implicit resolvers, and only those."""
    cls.yaml_implicit_resolvers = {}
    for tag, pattern, first in _CORE_SCHEMA:
        cls.add_implicit_resolver(tag, re.compile(f"^(?:{pattern})$"), list(first))
    # The empty plain scalar is null too; PyYAML indexes it under ``""``.
    cls.add_implicit_resolver(_NULL, re.compile("^$"), [""])


_INT_RE = re.compile(f"^(?:{_CORE_SCHEMA[2][1]})$")
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})


def _not_core(node: yaml.ScalarNode, tag: str) -> yaml.constructor.ConstructorError:
    """The error for an explicit tag on a spelling the core schema rejects."""
    return yaml.constructor.ConstructorError(
        None,
        None,
        f"{node.value!r} is not a YAML 1.2 core-schema {tag}",
        node.start_mark,
    )


def _construct_int(loader: yaml.BaseLoader, node: yaml.ScalarNode) -> int:
    """Build a core-schema int: base 10, ``0o`` octal or ``0x`` hex.

    An explicit ``!!int`` on any other spelling (``1_000``, ``0b11``) is a
    YAML error, not a Python ``ValueError``.
    """
    text = str(loader.construct_scalar(node))
    if not _INT_RE.match(text):
        raise _not_core(node, "int")
    if text.startswith("0o"):
        return int(text[2:], 8)
    if text.startswith("0x"):
        return int(text[2:], 16)
    return int(text, 10)


def _construct_bool(loader: yaml.BaseLoader, node: yaml.ScalarNode) -> bool:
    """Build a core-schema bool from ``true``/``false`` in its listed cases.

    An explicit ``!!bool`` on any other spelling (``yes``, ``on``) is a YAML
    error, not ``False``.
    """
    text = str(loader.construct_scalar(node))
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise _not_core(node, "bool")


class Core12Loader(yaml.CSafeLoader):
    """PyYAML's C safe loader with YAML 1.2.2 core-schema resolution."""


_install_core_schema(Core12Loader)
Core12Loader.add_constructor(_INT, _construct_int)
Core12Loader.add_constructor(_BOOL, _construct_bool)


class InteropDumper(yaml.CSafeDumper):
    """PyYAML's C safe dumper quoting for YAML 1.1 and 1.2 readers alike.

    It keeps the 1.1 resolvers PyYAML ships and adds the 1.2 core schema's,
    so a string is written plain only when both versions read it as one.
    """


for _tag, _pattern, _first in _CORE_SCHEMA:
    InteropDumper.add_implicit_resolver(
        _tag, re.compile(f"^(?:{_pattern})$"), list(_first)
    )


def _represent_date(dumper: InteropDumper, value: _dt.date) -> yaml.Node:
    """Write a date or datetime as its ISO text, quoted like any string.

    A core-schema reader reads untagged dates as strings, so a native value
    (from an explicit ``!!timestamp``) is written as the string it will be
    read back as; the resolvers above quote it for YAML 1.1 readers.
    """
    node: yaml.Node = dumper.represent_str(value.isoformat())
    return node


InteropDumper.add_representer(_dt.date, _represent_date)
InteropDumper.add_representer(_dt.datetime, _represent_date)


def load_yaml(text: str) -> Any:
    """Load one YAML document with core-schema resolution.

    Args:
        text: The YAML text.

    Returns:
        The loaded value.

    Raises:
        yaml.YAMLError: If the text is not valid YAML.
    """
    return yaml.load(text, Loader=Core12Loader)  # noqa: S506, RUF100 - a CSafeLoader subclass; S506 is gate-only.


class Yaml12Handler(YAMLHandler):
    """The python-frontmatter YAML handler, read and written as above."""

    def load(self, fm: str, **kwargs: object) -> Any:
        """Parse a frontmatter block with core-schema resolution.

        Args:
            fm: The block's YAML text.
            **kwargs: Passed to ``yaml.load``.

        Returns:
            The loaded value.
        """
        kwargs.setdefault("Loader", Core12Loader)
        return super().load(fm, **kwargs)

    def export(self, metadata: dict[str, object], **kwargs: object) -> str:
        """Serialize frontmatter, quoting for YAML 1.1 and 1.2 readers.

        Args:
            metadata: The frontmatter mapping.
            **kwargs: Passed to ``yaml.dump``.

        Returns:
            The YAML text, without delimiters.
        """
        kwargs.setdefault("Dumper", InteropDumper)
        return super().export(metadata, **kwargs)


#: The one handler instance every frontmatter read and write uses.
YAML12_HANDLER = Yaml12Handler()
