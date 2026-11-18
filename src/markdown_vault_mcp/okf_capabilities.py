"""Resolve OKF capabilities and filesystem-aware index designation."""

import os
import tempfile
from dataclasses import dataclass
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
    return hit(*_probe(anchor))
