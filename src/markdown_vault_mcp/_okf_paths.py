"""Pure resolution of OKF URI references against supplied bundle paths."""

import re
from collections.abc import Set
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from markdown_vault_mcp.utils.links import decode_link_target


@dataclass(frozen=True)
class OkfPathResult:
    """A resolution outcome, including the unmodified query and fragment."""

    status: Literal["resolved", "root_fallback", "unresolved", "external", "absent"]
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
    available: Set[str],
    version: str | None,
    *,
    path_field: bool = True,
) -> OkfPathResult:
    """Resolve an OKF reference using bundle-relative POSIX file identities.

    Args:
        value: URI reference with Markdown syntax removed and percent escapes
            intact. Encode literal ? and #; decoding occurs once.
        source: Bundle-relative path of the file containing the reference.
        available: Set of case-sensitive bundle-relative file paths for lookup.
        version: Normalized declared OKF version, or None when undeclared.
        path_field: Apply path-field empty-value and legacy fallback rules.

    Returns:
        The outcome, resolved path when found, and original URI suffix. An
        empty path field is absent; an empty body reference names its source.

    Raises:
        ValueError: If source is not a normalized bundle-relative file path.
    """
    source_parts = source.split("/")
    if not source or any(part in ("", ".", "..") for part in source_parts):
        raise ValueError("source must be a normalized bundle-relative file path")
    match = re.search(r"[?#]", value)
    raw_path = value[: match.start()] if match else value
    suffix = value[match.start() :] if match else ""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_path) or raw_path.startswith("//"):
        return OkfPathResult("external", suffix=suffix)
    if path_field and not value:
        return OkfPathResult("absent")
    decoded = decode_link_target(raw_path)
    # The shared decoder leaves the entire input unchanged on a refused escape.
    if decoded == raw_path and re.search(r"%[0-9A-Fa-f]{2}", raw_path):
        return OkfPathResult("unresolved", suffix=suffix)
    rooted = raw_path.startswith("/")
    base = () if rooted else tuple(PurePosixPath(source).parts[:-1])
    candidate = source if not raw_path else _within_root(base, decoded)
    if candidate is None:
        return OkfPathResult("external", suffix=suffix)
    if raw_path and decoded.split("/")[-1] in ("", ".", ".."):
        return OkfPathResult("unresolved", suffix=suffix)
    if candidate in available:
        result = OkfPathResult("resolved", candidate, suffix)
    elif path_field and raw_path and not rooted and version in (None, "0.1", "0.2"):
        fallback = _within_root((), decoded)
        if fallback is not None and fallback in available:
            result = OkfPathResult("root_fallback", fallback, suffix)
        else:
            result = OkfPathResult("unresolved", suffix=suffix)
    else:
        result = OkfPathResult("unresolved", suffix=suffix)
    return result
