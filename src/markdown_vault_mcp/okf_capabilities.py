"""Resolve OKF capabilities and filesystem-aware index designation."""

import os
import re
import sys
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache, partial
from pathlib import Path
from unicodedata import combining, normalize


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


@lru_cache(maxsize=1)
def _longest_decomposition() -> int:
    return max(map(len, _witnesses(True)), default=1)


def _ordered_tokens(pattern: str, unicode: bool) -> list[str]:
    tokens = re.findall(r"\[!?\]?[^\]]*\]|.", pattern, re.S)
    if unicode:
        start = 0
        while start < len(tokens):
            end = start
            while (
                end < len(tokens) and len(tokens[end]) == 1 and combining(tokens[end])
            ):
                end += 1
            if end > start:
                tokens[start:end] = sorted(tokens[start:end], key=combining)
            start = max(start + 1, end)
    return tokens


def _equivalent(name: str, pattern: str, case: bool, unicode: bool) -> bool:
    """Match an original glob against measured equivalent segment spellings."""
    tokens = _ordered_tokens(pattern, unicode)
    base = normalize("NFD", name) if unicode else name
    decompositions = _witnesses(True) if unicode else {}
    cases = _witnesses(False) if case else {}
    longest = _longest_decomposition() if unicode else 1
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
            for original in originals:
                folded = original.casefold()
                variants = (original, *cases.get(folded, ())) if case else (original,)
                if case and len(folded) == 1:
                    variants += (folded,)
                for pos in positions:
                    if pos < len(tokens) and any(
                        fnmatchcase(v, tokens[pos]) for v in variants
                    ):
                        states[offset + length].add(
                            pos if tokens[pos] == "*" else pos + 1
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
