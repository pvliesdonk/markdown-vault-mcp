"""Resolve OKF capabilities and filesystem-aware index designation."""

import os
import re
import sys
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache, partial
from pathlib import Path
from unicodedata import normalize


class DefaultVerify(str):
    """The legacy public default, distinguished from an explicit selection."""


DEFAULT_VERIFY = DefaultVerify("elicit")


@dataclass(frozen=True)
class OkfCapabilities:
    """Configured OKF capabilities before vault and read-only restrictions."""

    author: bool
    verify: str
    index: bool
    log: str
    folders: tuple[str, ...]


@lru_cache(maxsize=2)
def _witnesses(unicode: bool) -> dict[str, tuple[str, ...]]:
    """Index inverse single-character transformations for one measured axis."""
    result: dict[str, list[str]] = {}
    for cp in range(sys.maxunicode + 1):
        char = chr(cp)
        key = normalize("NFD", char) if unicode else char.casefold()
        if key != char:
            result.setdefault(key, []).append(char)
    return {key: tuple(value) for key, value in result.items()}


def _advance(
    tokens: list[str], positions: set[int], text: str, cases: dict[str, tuple[str, ...]]
) -> set[int]:
    """Advance the existing glob automaton through one spelling witness."""
    current = positions.copy()
    for char in text:
        for pos in range(len(tokens)):
            if pos in current and tokens[pos] == "*":
                current.add(pos + 1)
        folded = char.casefold()
        variants = (char, *cases.get(folded, ()))
        if cases and len(folded) == 1:
            variants += (folded,)
        current = {
            pos if tokens[pos] == "*" else pos + 1
            for pos in current
            if pos < len(tokens) and any(fnmatchcase(v, tokens[pos]) for v in variants)
        }
    return current


def _equivalent(name: str, pattern: str, case: bool, unicode: bool) -> bool:
    """Match an original glob against measured equivalent segment spellings."""
    tokens = re.findall(r"\[!?\]?[^\]]*\]|.", pattern, re.S)
    base = normalize("NFD", name) if unicode else name
    decompositions = _witnesses(True) if unicode else {}
    cases = _witnesses(False) if case else {}
    longest = max(
        max(map(len, decompositions), default=1) if unicode else 1,
        max(map(len, cases), default=1),
    )
    states: list[set[int]] = [set() for _ in range(len(base) + 1)]
    states[0].add(0)
    for offset, positions in enumerate(states):
        for pos in range(len(tokens)):
            if pos in positions and tokens[pos] == "*":
                positions.add(pos + 1)
        for length in range(1, min(longest, len(base) - offset) + 1):
            unit = base[offset : offset + length]
            originals = (
                *((unit,) if length == 1 else ()),
                *decompositions.get(unit, ()),
            )
            variants = set(cases.get(unit.casefold(), ())) if case else set()
            for original in originals:
                variants.add(original)
                if case:
                    folded = original.casefold()
                    variants.update(cases.get(folded, ()))
                    variants.add(folded)
            for variant in variants:
                states[offset + length].update(
                    _advance(tokens, positions, variant, cases)
                )
    return len(tokens) in states[-1]


def _probe(anchor: Path) -> tuple[bool, bool]:
    """Measure both aliases in an immediately removed same-filesystem directory."""
    try:
        with tempfile.TemporaryDirectory(prefix=".mvmcp-probe-", dir=anchor) as scratch:
            source = Path(scratch) / "Aé"
            source.touch()
            case = Path(scratch) / source.name.swapcase()
            unicode = Path(scratch) / "Ae\u0301"
            return (
                os.path.lexists(case) and case.samefile(source),
                os.path.lexists(unicode) and unicode.samefile(source),
            )
    except OSError:
        return True, True


def designated_index_path(root: Path, path: str, folders: tuple[str, ...]) -> bool:
    """Return whether a path names a generated index in a designated folder.

    Args:
        root: Vault root directory.
        path: Requested vault-relative path.
        folders: Operator's folder designation globs.

    Returns:
        Whether this path identifies a designated index file or destination.
    """
    from markdown_vault_mcp._okf_convention import folder_is_designated

    note = Path(path)
    if not path or note.is_absolute() or ".." in note.parts:
        return False
    if normalize("NFC", note.name).casefold() != "index.md" or (root / note).is_dir():
        return False
    resolved_root = root.resolve()
    parent = (root / note.parent).resolve()
    if not parent.is_relative_to(resolved_root):
        return False
    resolved = parent.relative_to(resolved_root).as_posix()
    if resolved == ".":
        resolved = ""
    if note.name == "index.md" and folder_is_designated(resolved, folders):
        return True
    anchor = root / note.parent
    while not anchor.is_dir() and anchor != root:
        anchor = anchor.parent
    case, unicode = _probe(anchor)
    return _equivalent(note.name, "index.md", case, unicode) and folder_is_designated(
        resolved,
        folders,
        segment_match=partial(_equivalent, case=case, unicode=unicode),
    )
