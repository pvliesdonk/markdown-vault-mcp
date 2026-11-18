"""Resolve OKF capabilities and filesystem-aware index designation."""

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
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
    legacy_stamping: bool


def _fold(value: str, case: bool, unicode: bool) -> str:
    """Apply only the name transformations the filesystem aliases."""
    if unicode:
        value = normalize("NFC", value)
    return value.casefold() if case else value


def _unicode_variant(value: str) -> str:
    """Switch a spelling between composed and decomposed Unicode."""
    return normalize("NFD" if value == normalize("NFC", value) else "NFC", value)


def _folds(anchor: Path, variant: Callable[[str], str]) -> bool | None:
    """Observe at most 64 entries for an alias, or return None without evidence."""
    try:
        with os.scandir(anchor) as entries:
            for item in islice(entries, 64):
                entry = anchor / item.name
                alias = anchor / variant(item.name)
                if alias.name == item.name:
                    continue
                try:
                    if entry.is_symlink() or alias.is_symlink():
                        continue
                    if entry.is_file() and entry.stat().st_nlink > 1:
                        continue
                    if not os.path.lexists(alias):
                        return False
                    return alias.samefile(entry)
                except OSError:
                    continue
    except OSError:
        pass
    return None


def _probe(anchor: Path) -> tuple[bool, bool]:
    """Measure both aliases in an immediately removed same-filesystem directory."""
    try:
        with tempfile.TemporaryDirectory(prefix=".mvmcp-probe-", dir=anchor) as scratch:
            source = Path(scratch) / "Aé"
            source.touch()
            case = Path(scratch) / source.name.swapcase()
            unicode = Path(scratch) / _unicode_variant(source.name)
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
    folder = "" if note.parent == Path() else note.parent.as_posix()

    def hit(case: bool, unicode: bool) -> bool:
        return _fold(note.name, case, unicode) == "index.md" and folder_is_designated(
            _fold(folder, case, unicode),
            tuple(_fold(p, case, unicode) for p in folders),
        )

    if not hit(True, True) or (root / note).is_dir():
        return False
    resolved_root = root.resolve()
    parent = (root / note.parent).resolve()
    if not parent.is_relative_to(resolved_root):
        return False
    folder = parent.relative_to(resolved_root).as_posix()
    if folder == ".":
        folder = ""
    if not hit(True, True):
        return False
    if hit(False, False):
        return True
    anchor = root / note.parent
    while not anchor.is_dir() and anchor != root:
        anchor = anchor.parent
    case = _folds(anchor, str.swapcase)
    unicode = _folds(anchor, _unicode_variant)
    if hit(case or False, unicode or False):
        return True
    if not hit(
        case if case is not None else True, unicode if unicode is not None else True
    ):
        return False
    if case is None or unicode is None:
        probed_case, probed_unicode = _probe(anchor)
        case = probed_case if case is None else case
        unicode = probed_unicode if unicode is None else unicode
    return hit(case, unicode)
