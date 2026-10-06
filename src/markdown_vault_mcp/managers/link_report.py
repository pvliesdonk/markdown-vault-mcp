"""Report the links a write introduced that do not resolve (#1725).

A writer that guesses a link target wrong learns about it today only when
someone runs ``get_broken_links``, long after it lost the context to fix the
link.  :class:`UnresolvedLinkReporter` answers the question at write time,
for the ``write`` / ``edit`` / ``append`` tools when
``MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS`` is on.

"Unresolved" is not decided here.  The reporter waits for the index to take
in the write, then reads the note's outlinks back from it, so a reported link
is one :meth:`~markdown_vault_mcp.fts_index.FTSIndex.get_broken_links` lists
for the same note, under the same resolution (path stem, then ``aliases``).
The note's own text only decides which of those links are *new*: a link the
note already held before the write is never reported again.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

from markdown_vault_mcp.scanner import extract_links, parse_frontmatter
from markdown_vault_mcp.utils import validate_path
from markdown_vault_mcp.utils.content_kind import effective_attachment_extensions
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from markdown_vault_mcp.interfaces import KeywordGraphIndex

logger = logging.getLogger(__name__)

#: A link's identity within one note: its kind and its target as written.
LinkKey = tuple[str, str]


class UnresolvedLinkReporter:
    """Find the unresolved links one write added to a note.

    Args:
        fts: The index whose link rows ``get_broken_links`` reads.
        source_dir: Absolute path to the vault root.
        attachment_extensions: The configured attachment allowlist, so the
            note is parsed with the same link rules the indexer applies
            (``None`` = the default set).
        is_queryable: Whether the index has finished its initial build.
        refresh_index: Blocks until queued writes are in the index; raises
            when the refresh fails or times out.
    """

    def __init__(
        self,
        *,
        fts: KeywordGraphIndex,
        source_dir: Path,
        attachment_extensions: Sequence[str] | None,
        is_queryable: Callable[[], bool],
        refresh_index: Callable[[], None],
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._extensions = effective_attachment_extensions(attachment_extensions)
        self._is_queryable = is_queryable
        self._refresh_index = refresh_index

    def _index_path(self, path: str) -> str:
        """Return the spelling of *path* the indexer keys the note's rows by."""
        return (self._source_dir / path).relative_to(self._source_dir).as_posix()

    def note_links(self, path: str) -> list[LinkKey]:
        """Return the links the note at *path* holds on disk, grouped by kind.

        Args:
            path: Vault-relative note path.

        Returns:
            One key per link: inline links, then reference links, then
            wikilinks, each kind in document order (the order
            ``extract_links`` returns).  ``[]`` when no note is at *path*, or
            when it cannot be read, decoded or its frontmatter parsed — the
            indexer skips such a note, so it holds no links
            ``get_broken_links`` sees.
        """
        abs_path = validate_path(path, self._source_dir)
        try:
            body = parse_frontmatter(decode_utf8(abs_path.read_bytes())).content
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return []
        links = extract_links(
            body, self._index_path(path), attachment_extensions=self._extensions
        )
        return [(link.link_type, link.raw_target) for link in links]

    def introduced(
        self,
        path: str,
        before: list[LinkKey],
        after: list[LinkKey],
    ) -> list[str] | None:
        """Return the targets of links the write added that do not resolve.

        Call after the write, outside the vault's write lock: the index
        refresh waits for the index writer.

        Args:
            path: Vault-relative path of the note just written.
            before: :meth:`note_links` taken before the write.  A note that
                could not be read then counts as holding no links, so its
                unresolved links are reported rather than hidden.
            after: :meth:`note_links` taken right after the write, still under
                the write lock, so a later writer's links are never reported
                as this write's.

        Returns:
            Each unresolved target as written, once, in :meth:`note_links`
            order (grouped by link kind); ``[]`` when every new link
            resolves.  ``None`` when the index could not be brought up to
            date, so the check did not run.
        """
        if not self._is_queryable():
            logger.warning(
                "unresolved_link_check_skipped path=%s reason=index_not_built", path
            )
            return None
        try:
            self._refresh_index()
        except Exception:
            # The write has already landed; failing the call now would make
            # the caller retry a write that succeeded.
            logger.warning(
                "unresolved_link_check_skipped path=%s reason=index_refresh_failed",
                path,
                exc_info=True,
            )
            return None
        broken = {
            (row["link_type"], row["raw_target"])
            for row in self._fts.get_outlinks(self._index_path(path))
            if not row["target_exists"]
        }
        existing = set(before)
        return list(
            dict.fromkeys(
                key[1] for key in after if key in broken and key not in existing
            )
        )
