"""Find logging calls that do not follow the log-call grammar.

Ported from ``fastmcp_pvl_core._log_conformance`` 10.2.0 (#2340);
``tests/test_logging_standard.py`` runs it over ``src/``.

The grammar in :mod:`markdown_vault_mcp._log_grammar` is what turns a log
record into fields rather than a sentence. A call that does not follow it
still logs, but arrives at an aggregator as an opaque ``message``. This module
finds those calls so the build fails on them instead of production surfacing
them.

It reads source with :mod:`ast` and never imports the tree it scans, so it is
safe to point at a package whose dependencies are not installed.

Kept separate from the grammar for separation of concerns: the
grammar is the contract a template either follows or does not, while this
module is a tool built on that contract — a project's build gate, not
something a running server depends on.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_vault_mcp._log_grammar import parse_log_template
from markdown_vault_mcp.utils.fs import is_directory

if TYPE_CHECKING:
    from pathlib import Path

_LEVEL_METHODS = frozenset(
    {"debug", "info", "warning", "error", "exception", "critical"}
)
"""The level methods the standard uses. ``log`` is excluded: its first
argument is the level, not the message, so it has a different shape."""

REASON_F_STRING = "f-string"
REASON_NON_LITERAL = "non-literal-message"
REASON_NO_ARGUMENTS = "no-arguments"
REASON_STARRED = "starred-arguments"
REASON_NON_CONFORMING = "non-conforming-template"
REASON_ARG_COUNT = "argument-count-mismatch"


@dataclass(frozen=True)
class LogCallViolation:
    """One logging call that does not follow the grammar.

    Attributes:
        path: File the call is in.
        line: 1-based line of the call.
        reason: Why it does not conform. One of the six literal strings
            ``"f-string"``, ``"non-literal-message"``, ``"no-arguments"``,
            ``"starred-arguments"``, ``"non-conforming-template"`` and
            ``"argument-count-mismatch"`` — the module-level ``REASON_*``
            constants are these same values but are private, so filter on
            the literal string rather than importing a constant.
        template: The literal template, when the call had one; ``None`` when
            the message was an f-string or another non-literal expression.
    """

    path: Path
    line: int
    reason: str
    template: str | None


def _is_get_logger_call(call: ast.Call) -> bool:
    """Whether *call* invokes ``getLogger``, qualified or bare.

    Covers both ``logging.getLogger(...)`` (an ``ast.Attribute`` whose
    ``attr`` is ``"getLogger"``) and a bare ``getLogger(...)`` reached via
    ``from logging import getLogger`` (an ``ast.Name``).
    """
    func = call.func
    return (isinstance(func, ast.Attribute) and func.attr == "getLogger") or (
        isinstance(func, ast.Name) and func.id == "getLogger"
    )


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Bare names *node* binds; anything else (tuple, attribute, ...) is skipped.

    ``ast.Assign`` and ``ast.AnnAssign`` are distinct node types with a
    differently shaped target: the former's ``targets`` is a list (it
    supports chained assignment, ``a = b = ...``), the latter's ``target`` is
    a single node.
    """
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _logger_names(tree: ast.Module) -> set[str]:
    """Names bound to a ``logging.getLogger(...)`` call anywhere in *tree*.

    Handles both plain assignment (``logger = logging.getLogger(__name__)``)
    and the annotated form the spec mandates (``logger: logging.Logger =
    logging.getLogger(__name__)``).
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and _is_get_logger_call(value)):
            continue
        names.update(_assigned_names(node))
    return names


def _violation(path: Path, call: ast.Call) -> LogCallViolation | None:  # noqa: PLR0911, RUF100 -- one return per violation reason
    """Judge one logging call, or return ``None`` when it conforms."""
    if not call.args:
        return LogCallViolation(path, call.lineno, REASON_NO_ARGUMENTS, None)

    message, *values = call.args
    if isinstance(message, ast.JoinedStr):
        return LogCallViolation(path, call.lineno, REASON_F_STRING, None)
    if not (isinstance(message, ast.Constant) and isinstance(message.value, str)):
        return LogCallViolation(path, call.lineno, REASON_NON_LITERAL, None)

    template = message.value
    parsed = parse_log_template(template)
    if parsed is None:
        return LogCallViolation(path, call.lineno, REASON_NON_CONFORMING, template)

    # ``*args`` makes the count unknowable from source; report rather than
    # guess, so a reader is never told a call was checked when it was not.
    if any(isinstance(value, ast.Starred) for value in values):
        return LogCallViolation(path, call.lineno, REASON_STARRED, template)
    if len(values) != parsed.placeholder_count:
        return LogCallViolation(path, call.lineno, REASON_ARG_COUNT, template)
    return None


def _is_level_call(call: ast.Call, receivers: set[str]) -> bool:
    """Whether *call* invokes a level method on one of *receivers*.

    A qualifying call is an attribute call (``receiver.method(...)``) whose
    method is one of :data:`_LEVEL_METHODS` and whose receiver is a bare name
    bound in *receivers* — the set :func:`_logger_names` already collected for
    the file *call* was found in.
    """
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _LEVEL_METHODS
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in receivers
    )


def _scan_file(path: Path) -> list[LogCallViolation]:
    """Violations in the single file at *path*; empty when it has none.

    Also empty, without walking the tree, when the file binds no
    ``logging.getLogger(...)`` receiver at all — there is nothing a call in
    it could conform or fail to conform to.
    """
    # Bytes, not str: ``ast.parse`` decodes per PEP 263 (an encoding cookie
    # or a BOM) the same way the interpreter does, independent of the
    # locale. ``Path.read_text()`` would decode with the locale encoding
    # instead, so a non-ASCII source file would raise ``UnicodeDecodeError``
    # under e.g. ``LC_ALL=C``.
    tree = ast.parse(path.read_bytes(), filename=str(path))
    receivers = _logger_names(tree)
    if not receivers:
        return []
    violations: list[LogCallViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_level_call(node, receivers):
            found = _violation(path, node)
            if found is not None:
                violations.append(found)
    return violations


def find_nonconforming_log_calls(root: Path) -> list[LogCallViolation]:
    """Report every logging call under *root* that breaks the grammar.

    Walks ``*.py`` beneath *root*, finds calls to a level method on a name
    bound to ``logging.getLogger(...)`` in the same file, and judges each one
    against :func:`markdown_vault_mcp._log_grammar.parse_log_template`. Results are sorted by
    path, then line, so output is stable across runs and platforms.

    Reads source only — nothing under *root* is imported or executed.

    Args:
        root: Directory to scan, typically a project's ``src/``.

    Returns:
        Violations, sorted; empty when everything conforms.

    Raises:
        NotADirectoryError: If *root* does not exist or is not a directory.
        OSError: If *root* cannot be stat-ed (the project's existence rule,
            #1625: a refused stat is not read as "not there").
            A build gate that calls this expecting ``== []`` must not have a
            wrong or stale path silently pass having scanned nothing.
        SyntaxError: If a file under *root* cannot be parsed. The scanned
            tree is the caller's own source, so unparseable input is a
            defect in it rather than something to report as a violation.
    """
    if not is_directory(root):
        raise NotADirectoryError(f"not a directory: {root}")
    violations: list[LogCallViolation] = []
    for path in sorted(root.rglob("*.py")):
        violations.extend(_scan_file(path))
    return sorted(violations, key=lambda v: (str(v.path), v.line))
