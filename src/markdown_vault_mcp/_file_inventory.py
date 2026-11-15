"""Bounded, metadata-only current-file inventory for the local CLI (#2121)."""

from __future__ import annotations

import fnmatch
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from markdown_vault_mcp.query_types import _instant
from markdown_vault_mcp.utils.content_kind import (
    effective_attachment_extensions,
    has_md_suffix,
    is_allowed_artifact,
    is_note,
)
from markdown_vault_mcp.utils.fs import _dir_prune_rules, _should_prune_dir

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

_BILLION = 1_000_000_000
_FD_WALK_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.scandir in os.supports_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)
_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
)


@contextmanager
def _directory_descriptor(base: int, parts: tuple[str, ...]) -> Iterator[int]:
    """Open every component relative to its held parent, never following links."""
    descriptor = os.dup(base)
    try:
        for part in parts:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _bound(value: str) -> tuple[Fraction, str]:
    # Keep the existing validation, but retain fractions exactly: floats can
    # round a file across a half-open boundary.
    _instant(value)
    fraction = re.search(r"\.\d+", value)
    whole = (
        value
        if fraction is None
        else value[: fraction.start()] + value[fraction.end() :]
    )
    seconds = cast("float", _instant(whole))
    suffix = fraction.group().rstrip("0").rstrip(".") if fraction else ""
    try:
        utc = datetime.fromtimestamp(int(seconds), UTC).isoformat(timespec="seconds")[
            :-6
        ]
    except (OverflowError, OSError, ValueError):
        raise ValueError(
            "Bounds must be representable as UTC RFC3339 instants."
        ) from None
    nanos = Fraction(int(seconds)) * _BILLION
    if fraction:
        nanos += Fraction("0" + fraction.group()) * _BILLION
    return nanos, utc + suffix + "Z"


def _utc(nanos: int) -> str:
    seconds, remainder = divmod(nanos, _BILLION)
    suffix = f".{remainder:09d}".rstrip("0") if remainder else ""
    return (
        datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")[:-6]
        + suffix
        + "Z"
    )


@dataclass(frozen=True)
class Window:
    """Validated half-open interval, preserving sub-nanosecond boundaries."""

    start: str
    end: str
    lower: Fraction = field(init=False)
    upper: Fraction = field(init=False)

    def __post_init__(self) -> None:
        lower, start = _bound(self.start)
        upper, end = _bound(self.end)
        if lower >= upper:
            raise ValueError("--start must precede --end.")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class ScanLimits:
    """Work limits, not a claim that a filesystem call can be interrupted."""

    entries: int = 100_000
    seconds: float = 10.0


@dataclass
class _Scan:
    root: Path
    window: Window
    settings: VaultSettings
    limits: ScanLimits
    items: list[dict[str, Any]] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)
    visited: int = 0
    deadline: float = field(init=False)
    excludes: tuple[str, ...] = field(init=False)
    prune: tuple[list[str], list[str]] = field(init=False)
    extensions: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.limits.seconds
        self.excludes = tuple(self.settings.effective_exclude_patterns() or ())
        self.prune = _dir_prune_rules(self.excludes)
        self.extensions = effective_attachment_extensions(
            self.settings.attachment_extensions
        )

    def stopped(self) -> bool:
        if self.visited >= self.limits.entries:
            self.reasons.add("scan_limit")
            return True
        if time.monotonic() >= self.deadline:
            self.reasons.add("deadline")
            return True
        return False

    def visit(self, entry: os.DirEntry[str], prefix: Path) -> Path | None:
        path = prefix / entry.name
        relative = path.as_posix()
        # Dot paths never need metadata or diagnostics. Directory exclusion
        # uses only subtree rules: a pattern matching the directory's own
        # name need not match any of its files under fnmatch semantics.
        if entry.name.startswith("."):
            return None
        if entry.is_dir(follow_symlinks=False):
            return None if _should_prune_dir(relative, *self.prune) else path
        if any(fnmatch.fnmatch(relative, p) for p in self.excludes):
            return None
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            self.reasons.add("unsupported_symlink")
        if not stat.S_ISREG(info.st_mode):
            return None
        kind = "markdown" if is_note(relative) else "attachment"
        if kind == "attachment" and (
            has_md_suffix(path) or not is_allowed_artifact(path, self.extensions)
        ):
            return None
        if self.window.lower <= info.st_mtime_ns < self.window.upper:
            self.items.append(
                {"path": relative, "kind": kind, "modified_at": _utc(info.st_mtime_ns)}
            )
        return None

    def directory(self, directory: Path, root_descriptor: int) -> list[Path]:
        children: list[Path] = []
        try:
            with (
                _directory_descriptor(root_descriptor, directory.parts) as descriptor,
                os.scandir(descriptor) as entries,
            ):
                while not self.stopped():
                    entry = next(entries, None)
                    if entry is None:
                        break
                    self.visited += 1
                    try:
                        child = self.visit(entry, directory)
                    except (OSError, ValueError, OverflowError):
                        self.reasons.add("entry_unavailable")
                        continue
                    if child is not None:
                        children.append(child)
        except (OSError, RuntimeError):
            self.reasons.add("directory_unavailable")
        return children


def _scan_tree(scan: _Scan) -> None:
    # Holding each parent while opening the next component avoids the
    # resolve-then-scandir race. No pathname-based fallback is safe here.
    try:
        anchor = os.open(scan.root.anchor, _DIRECTORY_FLAGS)
        try:
            with _directory_descriptor(anchor, scan.root.parts[1:]) as root_descriptor:
                pending = [Path()]
                while pending and not scan.stopped():
                    pending.extend(scan.directory(pending.pop(), root_descriptor))
        finally:
            os.close(anchor)
    except OSError:
        scan.reasons.add("directory_unavailable")


def scan_current_files(
    root: Path, window: Window, settings: VaultSettings, limits: ScanLimits
) -> dict[str, Any]:
    """Observe eligible current files without opening their contents or an index.

    Args:
        root: Selected local vault root, resolved before scanning.
        window: Inclusive start and exclusive end.
        settings: Existing exclusion and attachment configuration.
        limits: Maximum entries visited and cooperative wall-clock deadline.

    Returns:
        A JSON-compatible current-file view, with generic incompleteness reasons.
        A complete scan is still a live observation, not an atomic snapshot.
    """
    scan = _Scan(root, window, settings, limits)
    if _FD_WALK_SUPPORTED:
        _scan_tree(scan)
    else:
        scan.reasons.add("unsupported_platform")
    return {
        "source_dir": str(root),
        "start": window.start,
        "end": window.end,
        "interval": "[start,end)",
        "basis": "current_file_mtime",
        "complete": not scan.reasons,
        "incomplete_reasons": sorted(scan.reasons),
        "items": sorted(scan.items, key=lambda item: item["path"]),
    }
