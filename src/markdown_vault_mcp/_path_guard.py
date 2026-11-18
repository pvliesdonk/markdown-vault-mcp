"""Operator policy for ordinary mutations of vault paths."""

from collections.abc import Callable, Iterator, Sequence
from fnmatch import fnmatchcase
from itertools import chain
from pathlib import Path, PurePosixPath
from unicodedata import normalize

from markdown_vault_mcp.utils import reject_nul


class PathGuard:
    """Match configured protected globs against paths and their ancestors."""

    def __init__(
        self,
        source_dir: Path,
        patterns: Sequence[str] | None,
        designated: Callable[[str], bool] | None = None,
    ) -> None:
        self._designated = designated
        self._source_dir = source_dir
        self._patterns = tuple(normalize("NFC", p).casefold() for p in patterns or ())

    def reason(self, path: str, *, allow_designated: bool = False) -> str | None:
        """Return the protection reason for a path, or ``None`` if unprotected.

        Args:
            path: Vault-relative path to check.
            allow_designated: Permit a generated index, retaining operator policy.

        Returns:
            The reason a mutation is refused, if one applies.
        """
        if not self._patterns and (allow_designated or not self._designated):
            return None

        def resolved_candidate() -> Iterator[str]:
            reject_nul(path)
            root = self._source_dir.resolve()
            resolved = (root / path).resolve()
            if resolved.is_relative_to(root):
                yield resolved.relative_to(root).as_posix()

        candidates = tuple(dict.fromkeys(chain([path], resolved_candidate())))
        for candidate in candidates:
            parts = PurePosixPath(normalize("NFC", candidate).casefold()).parts
            for end in range(1, len(parts) + 1):
                prefix = "/".join(parts[:end])
                if any(fnmatchcase(prefix, pattern) for pattern in self._patterns):
                    return "protected by MARKDOWN_VAULT_MCP_PROTECTED_PATHS"
        if (
            not allow_designated
            and self._designated
            and any(map(self._designated, candidates))
        ):
            return "a designated generated index; use okf_generate_index"
        return None
