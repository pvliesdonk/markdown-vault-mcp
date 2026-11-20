"""Pure resolution of OKF URI references against supplied bundle paths."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import unquote


@dataclass(frozen=True)
class OkfPathResult:
    """A resolution outcome, including the unmodified query and fragment."""

    status: Literal["resolved", "root_fallback", "unresolved", "external"]
    path: str | None = None
    suffix: str = ""


def _within_root(base: tuple[str, ...], path: str) -> str | None:
    parts = list(base)
    for part in path.split("/"):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def resolve_okf_path(
    value: str,
    source: str,
    available: Collection[str],
    version: str | None,
    *,
    path_field: bool = True,
) -> OkfPathResult:
    """Resolve an OKF reference using bundle-relative POSIX file identities.

    Args:
        value: URI reference as written in the source file.
        source: Bundle-relative path of the file containing the reference.
        available: Case-sensitive bundle-relative file paths available for lookup.
        version: Declared OKF version, or None when undeclared.
        path_field: Whether legacy path-field root fallback is allowed.

    Returns:
        The outcome, resolved path when found, and original URI suffix.

    Raises:
        ValueError: If source is not a normalized bundle-relative file path.
    """
    source_parts = source.split("/")
    if not source or any(part in ("", ".", "..") for part in source_parts):
        raise ValueError("source must be a normalized bundle-relative file path")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value) or value.startswith("//"):
        return OkfPathResult("external")
    match = re.search(r"[?#]", value)
    raw_path = value[: match.start()] if match else value
    suffix = value[match.start() :] if match else ""
    decoded = unquote(raw_path)
    rooted = raw_path.startswith("/")
    base = () if rooted else tuple(PurePosixPath(source).parts[:-1])
    candidate = source if not raw_path else _within_root(base, decoded)
    if candidate is None:
        return OkfPathResult("external", suffix=suffix)
    if candidate in available:
        return OkfPathResult("resolved", candidate, suffix)
    if path_field and raw_path and not rooted and version in (None, "0.1", "0.2"):
        fallback = _within_root((), decoded)
        if fallback is not None and fallback in available:
            return OkfPathResult("root_fallback", fallback, suffix)
    return OkfPathResult("unresolved", suffix=suffix)
