"""Resolve OKF capabilities and filesystem-aware index designation."""

from dataclasses import dataclass
from pathlib import Path


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


def _actual_spelling(current: Path, part: str) -> str:
    """Find the spelling of an existing entry by identity."""
    from unicodedata import normalize

    requested = current / part
    if not requested.exists():
        return part
    for child in current.iterdir():
        if normalize("NFC", child.name).casefold() == normalize(
            "NFC", part
        ).casefold() and child.samefile(requested):
            composed = normalize("NFC", child.name)
            if (current / composed).exists() and (current / composed).samefile(
                requested
            ):
                return composed
            return child.name
    return part


def _ancestor_case_alias(root: Path) -> bool:
    """Use a same-device ancestor with a cased name to test filesystem identity."""
    device = root.stat().st_dev
    current = root
    while current.parent != current and current.parent.stat().st_dev == device:
        alternate = current.name.swapcase()
        if alternate != current.name:
            alias = current.with_name(alternate)
            return alias.exists() and alias.samefile(current)
        current = current.parent
    return False


def _actual_folder(root: Path, folder: Path) -> tuple[str, bool, bool]:
    """Find each existing folder's disk spelling without conflating distinct files."""
    from unicodedata import normalize

    current = root
    parts: list[str] = []
    case_aliases = True
    case_proven = False
    unicode_aliases = True
    for part in folder.relative_to(root).parts:
        requested = current / part
        actual = _actual_spelling(current, part)
        alternate = actual.swapcase()
        if alternate != actual:
            case_proven = True
            if not (
                (current / alternate).exists()
                and (current / alternate).samefile(requested)
            ):
                case_aliases = False
        alternate_unicode = normalize(
            "NFD" if actual == normalize("NFC", actual) else "NFC", actual
        )
        if alternate_unicode != actual and not (
            (current / alternate_unicode).exists()
            and (current / alternate_unicode).samefile(requested)
        ):
            unicode_aliases = False
        parts.append(actual)
        current = current / actual
    if not case_proven:
        case_aliases = _ancestor_case_alias(root)
        case_proven = True
    return "/".join(parts), case_aliases and case_proven, unicode_aliases


def _index_name_matches(target: Path, parent: Path, case_aliases: bool) -> bool:
    """Test the file identity or the destination spelling on this filesystem."""
    if target.name == "index.md":
        return True
    canonical = parent / "index.md"
    if target.exists():
        return canonical.exists() and target.samefile(canonical)
    return case_aliases


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
    target = root / note
    if note.name.casefold() != "index.md":
        return False
    if target.is_dir():
        return False
    resolved_root = root.resolve()
    parent = target.parent.resolve()
    if not parent.is_relative_to(resolved_root):
        return False
    from unicodedata import normalize

    folder, case_aliases, unicode_aliases = _actual_folder(resolved_root, parent)
    if not _index_name_matches(target, parent, case_aliases):
        return False
    if folder_is_designated(folder, folders):
        return True
    if unicode_aliases:
        folder = normalize("NFC", folder)
        folders = tuple(normalize("NFC", pattern) for pattern in folders)
    if case_aliases:
        folder = folder.casefold()
        folders = tuple(pattern.casefold() for pattern in folders)
    return folder_is_designated(folder, folders)
