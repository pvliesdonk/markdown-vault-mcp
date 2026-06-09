"""File discovery, frontmatter parsing, and chunking for markdown-vault-mcp."""

from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import frontmatter
import yaml

from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.types import Chunk, LinkInfo, ParsedNote
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# Threshold below which a document is not split (single chunk).
_SHORT_DOC_LINES = 30


@runtime_checkable
class ChunkStrategy(Protocol):
    """Protocol for document chunking strategies."""

    def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
        """Chunk the markdown body into sections.

        Args:
            content: Markdown body after frontmatter has been stripped.
            _metadata: Parsed frontmatter dict (for context, not modification).
                Underscore prefix marks the parameter as unused-by-default in
                implementations; callers pass it positionally.

        Returns:
            List of Chunk objects.
        """
        ...


class WholeDocumentChunker:
    """Returns the entire document as a single chunk.

    Suitable for short documents or when per-section search is not needed.
    """

    def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
        """Return the document as one chunk.

        Args:
            content: Markdown body after frontmatter has been stripped.
            _metadata: Parsed frontmatter dict (unused by this strategy).

        Returns:
            A list containing exactly one Chunk covering the full document.
        """
        return [
            Chunk(
                heading=None,
                heading_level=0,
                content=content,
                start_line=0,
            )
        ]


class HeadingChunker:
    """Split document on heading boundaries, descending adaptively when chunks
    exceed the configured word and/or character budget.

    Legacy behaviour (both ``max_chunk_words`` and ``max_chunk_chars`` are
    ``None``): split on H1/H2 only — the pre-2026-04 behaviour.  A document
    that contains only H3+ headings and no H1/H2 headings returns a
    **single** ``heading=None`` chunk in legacy mode (same as pre-2026-04).
    With a budget set, after the initial H1/H2 split each chunk that exceeds
    the budget is recursively re-split at the next heading level (H3, then
    H4, …, up to H6) until each chunk fits or no headings of the next level
    exist inside.  In adaptive mode, if H1/H2 yielded nothing the chunker
    descends H3→H6 to find the shallowest heading level present — so a doc
    with only H3 headings is still split at H3 rather than dropped into a
    single chunk.  When heading-based refinement cannot make further
    progress — a leaf section with no deeper headings, a preamble with no
    headings at all, or a short-doc / no-heading document —
    :meth:`_budget_split` falls back to paragraph, line, and word boundaries
    so the invariant ``not _over_budget(chunk.content)`` holds for every
    emitted chunk regardless of source structure (the sole exception being a
    single token longer than ``max_chunk_chars``, which is unsplittable).

    The ``max_chunk_chars`` cap bounds token-dense content — text with a high
    character-per-word ratio that fits ``max_chunk_words`` yet exceeds the
    embedding model's token context; it is derived from the model's context
    length (see :func:`markdown_vault_mcp.config.derive_max_chunk_chars`).

    Short documents (fewer than ``short_doc_lines`` lines) are returned as a
    single chunk when they fit the budget; if they exceed it, the same
    budget fallback applies.

    This is the default chunking strategy.
    """

    def __init__(
        self,
        short_doc_lines: int = _SHORT_DOC_LINES,
        *,
        max_chunk_words: int | None = None,
        max_chunk_chars: int | None = None,
    ) -> None:
        """Initialise the chunker.

        Args:
            short_doc_lines: Line count at or below which the document is
                returned as a single chunk rather than split on headings.
            max_chunk_words: Word-count threshold above which a chunk is
                recursively re-split at the next heading level. ``None``
                preserves today's H1/H2-only behaviour.
            max_chunk_chars: Character-count threshold above which a chunk is
                re-split. Bounds token-dense content (many characters per
                word) that fits the word budget yet exceeds the embedding
                model's context. ``None`` disables the char cap; splitting
                then depends on ``max_chunk_words`` alone.
        """
        self.short_doc_lines = short_doc_lines
        self.max_chunk_words = max_chunk_words
        self.max_chunk_chars = max_chunk_chars

    def _has_budget(self) -> bool:
        """True when either a word budget or a char budget is configured."""
        return self.max_chunk_words is not None or self.max_chunk_chars is not None

    def _over_budget(self, content: str) -> bool:
        """True when content exceeds either the word or the char budget."""
        if (
            self.max_chunk_words is not None
            and len(content.split()) > self.max_chunk_words
        ):
            return True
        return self.max_chunk_chars is not None and len(content) > self.max_chunk_chars

    def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
        """Split content adaptively on heading boundaries.

        Args:
            content: Markdown body after frontmatter has been stripped.
            _metadata: Parsed frontmatter dict (unused).

        Returns:
            List of :class:`~markdown_vault_mcp.types.Chunk` objects.
        """
        lines = content.splitlines(keepends=True)

        if len(lines) <= self.short_doc_lines:
            single = Chunk(heading=None, heading_level=0, content=content, start_line=0)
            if self._over_budget(content):
                return self._budget_split(single)
            return [single]

        # Try the canonical H1+H2 split first.
        chunks = self._split_at_levels(lines, levels=(1, 2), base_line=0)

        # In adaptive mode, if H1/H2 yielded nothing, descend through H3..H6
        # to find any heading-level present in the doc, so the cap/snippet
        # pipeline still sees per-section chunks.  In legacy mode (no budget
        # configured) we preserve the pre-2026-04 H1/H2-only behaviour: a doc
        # with only deep headings falls through to the single-chunk-no-heading
        # return below.
        deepest_split_level = 2
        if not chunks and self._has_budget():
            for level in (3, 4, 5, 6):
                chunks = self._split_at_levels(lines, levels=(level,), base_line=0)
                if chunks:
                    deepest_split_level = level
                    break

        if not chunks:
            # No headings at all → single chunk with no heading.
            single = Chunk(heading=None, heading_level=0, content=content, start_line=0)
            if self._over_budget(content):
                return self._budget_split(single)
            return [single]

        if self._has_budget():
            chunks = self._refine_oversize(chunks, current_level=deepest_split_level)
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_at_levels(
        self,
        lines: list[str],
        *,
        levels: tuple[int, ...],
        base_line: int,
    ) -> list[Chunk]:
        """Split *lines* on any heading whose level is in *levels*.

        ``base_line`` is added to every emitted ``start_line`` so that
        ``start_line`` always refers to the *original* document, not the
        sub-slice passed in during recursion.

        Returns an empty list if the slice contains no matching headings.
        """
        # Walk and record split points (line index in this slice, level, text).
        split_points: list[tuple[int, int, str]] = []
        max_level = max(levels)
        pat = re.compile(rf"^(#{{1,{max_level}}})\s+(.+)$")
        for idx, line in enumerate(lines):
            m = pat.match(line.rstrip())
            if m:
                level = len(m.group(1))
                if level in levels:
                    split_points.append((idx, level, m.group(2).strip()))

        if not split_points:
            return []

        chunks: list[Chunk] = []

        # Preamble: anything before the first split point.
        first_line = split_points[0][0]
        if first_line > 0:
            preamble = "".join(lines[:first_line])
            if preamble.strip():
                chunks.append(
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content=preamble,
                        start_line=base_line,
                    )
                )

        for i, (line_idx, level, heading_text) in enumerate(split_points):
            content_start = line_idx + 1
            content_end = (
                split_points[i + 1][0] if i + 1 < len(split_points) else len(lines)
            )
            section_content = "".join(lines[content_start:content_end])
            if not section_content.strip():
                continue
            chunks.append(
                Chunk(
                    heading=heading_text,
                    heading_level=level,
                    content=section_content,
                    start_line=base_line + line_idx,
                )
            )
        return chunks

    def _refine_oversize(
        self, chunks: list[Chunk], *, current_level: int
    ) -> list[Chunk]:
        """Recursively re-split chunks that exceed the configured budget.

        ``current_level`` is the deepest heading level already used as a
        split point. Refinement attempts ``current_level + 1`` next; if no
        deeper headings exist inside an oversize chunk (or we have already
        reached H6), :meth:`_budget_split` fragments it on paragraph and
        word boundaries so the invariant ``not _over_budget(chunk.content)``
        holds for every emitted chunk — including preamble chunks with no
        heading at all.
        """
        assert self._has_budget()  # guarded by caller
        out: list[Chunk] = []
        for chunk in chunks:
            if not self._over_budget(chunk.content):
                out.append(chunk)
                continue

            if chunk.heading is not None and current_level < 6:
                next_level = current_level + 1
                sub_chunks = self._split_at_levels(
                    chunk.content.splitlines(keepends=True),
                    levels=(next_level,),
                    base_line=chunk.start_line + 1,
                )
                if sub_chunks:
                    # The first sub-chunk may be the preamble of the parent
                    # section (text between the parent's heading and the
                    # first sub-heading).  ``_split_at_levels`` assigns it
                    # ``heading=None``; promote it to inherit the parent's
                    # heading so the inter-heading text stays attributed
                    # to the parent rather than appearing orphaned.
                    if sub_chunks[0].heading is None:
                        sub_chunks[0].heading = chunk.heading
                        sub_chunks[0].heading_level = chunk.heading_level
                    out.extend(
                        self._refine_oversize(sub_chunks, current_level=next_level)
                    )
                    continue

            out.extend(self._budget_split(chunk))
        return out

    def _budget_split(self, chunk: Chunk) -> list[Chunk]:
        """Split *chunk* on paragraph, line, and word boundaries until each
        fragment fits within the configured word **and** char budgets.

        Used as the last-resort splitter when no deeper heading is available
        (e.g. an H6 section longer than the budget, or a preamble with no
        internal structure).  Preserves the parent chunk's ``heading`` and
        ``heading_level`` on every fragment; ``start_line`` is the parent's
        ``start_line`` offset by the paragraph or line offset within the
        parent content.

        Splitting hierarchy (each level used only when the previous level
        cannot keep the budget):
        1. Paragraph boundaries — multiple paragraphs bin-pack into a
           single chunk when their combined word/char counts fit the
           budget, preserving inter-paragraph blank lines.
        2. Line boundaries inside an oversize paragraph — lines bin-pack
           the same way, so tables, lists, code blocks, and other
           line-structured content keep their layout.
        3. Word boundaries inside an oversize single line — last resort
           for a pathological single line longer than the whole budget;
           collapses internal whitespace and loses line structure (but
           the alternative is silent truncation by the embedding model).

        Args:
            chunk: A chunk whose word or char count exceeds the budget.

        Returns:
            One or more chunks, each respecting the budget.  Returns
            ``[chunk]`` unchanged when the content is whitespace-only (no
            paragraphs to split on).
        """
        assert self._has_budget()  # guarded by caller
        word_budget = self.max_chunk_words
        char_budget = self.max_chunk_chars

        def _over(words: int, chars: int) -> bool:
            return (word_budget is not None and words > word_budget) or (
                char_budget is not None and chars > char_budget
            )

        # Group consecutive non-blank lines into paragraphs and record each
        # paragraph's line offset within the chunk so fragments can carry
        # accurate ``start_line`` values.
        raw_lines = chunk.content.splitlines(keepends=True)
        paragraphs: list[tuple[int, list[str]]] = []
        cur_offset: int | None = None
        cur_lines: list[str] = []
        for idx, line in enumerate(raw_lines):
            if line.strip():
                if cur_offset is None:
                    cur_offset = idx
                cur_lines.append(line)
            elif cur_lines:
                assert cur_offset is not None
                paragraphs.append((cur_offset, cur_lines))
                cur_lines = []
                cur_offset = None
        if cur_lines:
            assert cur_offset is not None
            paragraphs.append((cur_offset, cur_lines))

        if not paragraphs:
            return [chunk]

        out: list[Chunk] = []
        pending_offset: int | None = None
        pending_lines: list[str] = []
        pending_words = 0
        pending_chars = 0

        def make_chunk(content: str, offset: int) -> Chunk:
            return Chunk(
                heading=chunk.heading,
                heading_level=chunk.heading_level,
                content=content,
                start_line=chunk.start_line + offset,
            )

        def emit() -> None:
            nonlocal pending_offset, pending_lines, pending_words, pending_chars
            if not pending_lines:
                return
            assert pending_offset is not None
            out.append(make_chunk("".join(pending_lines), pending_offset))
            pending_offset = None
            pending_lines = []
            pending_words = 0
            pending_chars = 0

        for offset, lines in paragraphs:
            words_in_para = sum(len(line.split()) for line in lines)
            chars_in_para = sum(len(line) for line in lines)

            if _over(words_in_para, chars_in_para):
                # Single paragraph exceeds a budget — flush, then bin-pack
                # its lines.  This preserves line-structured content
                # (tables, lists, code blocks) within an oversize paragraph
                # rather than flattening it via word-split.
                emit()
                self._budget_split_lines(
                    lines, offset, word_budget, char_budget, out, make_chunk
                )
                continue

            # +1 char accounts for the blank-line separator restored below
            # when this paragraph joins an existing pending group.
            sep_chars = 1 if pending_offset is not None else 0
            if _over(
                pending_words + words_in_para,
                pending_chars + sep_chars + chars_in_para,
            ):
                emit()
            if pending_offset is None:
                pending_offset = offset
            else:
                # Restore the blank-line separator that paragraph collection
                # stripped, so accumulated paragraphs stay valid markdown.
                pending_lines.append("\n")
                pending_chars += 1
            pending_lines.extend(lines)
            pending_words += words_in_para
            pending_chars += chars_in_para

        emit()
        return out

    def _budget_split_lines(
        self,
        lines: list[str],
        paragraph_offset: int,
        word_budget: int | None,
        char_budget: int | None,
        out: list[Chunk],
        make_chunk: Any,
    ) -> None:
        """Bin-pack the lines of a single oversize paragraph within budget.

        Each line keeps its trailing newline (``splitlines(keepends=True)``
        from the caller) so concatenation preserves the original layout —
        tables stay tabular, lists stay listed, code blocks stay
        line-broken.  Only when an individual line itself exceeds a budget
        do we resort to a word-split on that single line; this final
        fallback collapses internal whitespace but is bounded to
        pathological cases (a single line carrying more words/chars than the
        entire budget).

        Emits chunks via *make_chunk(content, offset)* so the parent
        chunk's ``heading`` / ``heading_level`` / ``start_line`` baseline
        is applied uniformly.

        Args:
            lines: All lines of the oversize paragraph, each with its
                trailing newline preserved.
            paragraph_offset: Line offset of the paragraph within the
                parent chunk's content.
            word_budget: Maximum word count per emitted chunk, or ``None``.
            char_budget: Maximum character count per emitted chunk, or
                ``None``.  At least one budget is non-``None``.
            out: Mutable list to append emitted chunks to.
            make_chunk: Callable ``(content, offset) -> Chunk`` that
                stamps the parent's metadata onto each fragment.
        """

        def _over(words: int, chars: int) -> bool:
            return (word_budget is not None and words > word_budget) or (
                char_budget is not None and chars > char_budget
            )

        line_pending: list[str] = []
        line_pending_words = 0
        line_pending_chars = 0
        line_pending_offset: int | None = None

        def flush_pending() -> None:
            nonlocal line_pending, line_pending_words, line_pending_chars
            nonlocal line_pending_offset
            if not line_pending:
                return
            assert line_pending_offset is not None
            out.append(make_chunk("".join(line_pending), line_pending_offset))
            line_pending = []
            line_pending_words = 0
            line_pending_chars = 0
            line_pending_offset = None

        for li, line in enumerate(lines):
            line_words = len(line.split())
            line_chars = len(line)
            line_offset = paragraph_offset + li

            if _over(line_words, line_chars):
                # Single line over a budget — flush pending, then word-split
                # this one line so each fragment fits BOTH budgets.  Collapses
                # internal whitespace and drops the trailing newline; bounded
                # to pathological cases where one line carries more
                # words/chars than the entire budget would allow.
                flush_pending()
                self._word_split_line(
                    line, line_offset, word_budget, char_budget, out, make_chunk
                )
                continue

            if line_pending and _over(
                line_pending_words + line_words,
                line_pending_chars + line_chars,
            ):
                flush_pending()
            if line_pending_offset is None:
                line_pending_offset = line_offset
            line_pending.append(line)
            line_pending_words += line_words
            line_pending_chars += line_chars

        flush_pending()

    def _word_split_line(
        self,
        line: str,
        line_offset: int,
        word_budget: int | None,
        char_budget: int | None,
        out: list[Chunk],
        make_chunk: Any,
    ) -> None:
        """Word-split a single oversize *line* into budget-fitting fragments.

        Greedily packs whitespace-delimited tokens into space-joined
        fragments, flushing before a token would push the fragment over the
        word or char budget.  A single token longer than the char budget is
        emitted alone (it is unsplittable without breaking the token), so the
        invariant may be violated only by such a pathological token.

        Args:
            line: The single oversize line (its trailing newline is dropped).
            line_offset: ``start_line`` offset for every emitted fragment.
            word_budget: Maximum word count per fragment, or ``None``.
            char_budget: Maximum character count per fragment, or ``None``.
            out: Mutable list to append emitted fragments to.
            make_chunk: Callable ``(content, offset) -> Chunk`` stamping
                parent metadata onto each fragment.
        """

        def _over(words: int, chars: int) -> bool:
            return (word_budget is not None and words > word_budget) or (
                char_budget is not None and chars > char_budget
            )

        def _emit(frag_tokens: list[str]) -> None:
            content = " ".join(frag_tokens)
            if char_budget is not None and len(content) > char_budget:
                # An unsplittable single token over the char budget (the one
                # invariant exception). Diagnostic only — the conservative cap
                # means it may still embed; a true overflow surfaces at embed
                # time as build_embeddings_skip_batch.
                logger.debug(
                    "chunker_unsplittable_token chars=%d budget=%d line_offset=%d",
                    len(content),
                    char_budget,
                    line_offset,
                )
            out.append(make_chunk(content, line_offset))

        tokens = line.split()
        cur: list[str] = []
        cur_words = 0
        cur_chars = 0
        for tok in tokens:
            # Char cost of adding this token: the token plus a joining space
            # when the fragment is non-empty.
            add_chars = len(tok) + (1 if cur else 0)
            if cur and _over(cur_words + 1, cur_chars + add_chars):
                _emit(cur)
                cur = []
                cur_words = 0
                cur_chars = 0
                add_chars = len(tok)
            cur.append(tok)
            cur_words += 1
            cur_chars += add_chars
        if cur:
            _emit(cur)


def _resolve_title(metadata: dict[str, Any], content: str, path: Path) -> str:
    """Resolve the document title using the priority order from the design spec.

    Priority: frontmatter ``title`` field → first H1 heading → filename
    without extension.

    Args:
        metadata: Parsed frontmatter dict.
        content: Markdown body (frontmatter stripped).
        path: Absolute path to the file (used for filename fallback).

    Returns:
        Resolved title string.
    """
    # 1. Frontmatter title field.
    if "title" in metadata and isinstance(metadata["title"], str):
        title = metadata["title"].strip()
        if title:
            return title

    # 2. First H1 heading in content.
    for line in content.splitlines():
        m = re.match(r"^#\s+(.+)$", line.rstrip())
        if m:
            return m.group(1).strip()

    # 3. Filename without extension.
    return path.stem


_EXTERNAL_URL_PREFIXES = ("http://", "https://", "mailto:", "//")

# Fenced code block: matches ``` or ~~~ delimiters (with optional language tag).
_RE_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
# Inline code: matches single backtick spans (non-greedy, no newlines inside).
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
# Inline markdown link: [text](target)
_RE_INLINE_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
# Reference-style link usage: [text][ref] or [text][]
_RE_REF_USAGE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
# Reference definition: [ref]: target  (at start of line, optional leading whitespace)
_RE_REF_DEF = re.compile(r"^\s*\[([^\]]+)\]:\s*(.+)$", re.MULTILINE)
# Wikilink: [[path]] or [[path|alias]]
_RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _strip_code_spans(content: str) -> str:
    """Remove fenced and inline code spans from markdown content.

    This prevents links inside code examples from being extracted.

    Args:
        content: Raw markdown body text.

    Returns:
        Content with fenced and inline code regions replaced by empty strings.
    """
    content = _RE_FENCED_CODE.sub("", content)
    content = _RE_INLINE_CODE.sub("", content)
    return content


def _resolve_link_path(target: str, source_rel: str) -> tuple[str, str | None]:
    """Resolve a raw link target against the source document's directory.

    Splits off any fragment identifier (``#heading``), resolves the path
    relative to the source document using POSIX semantics, and clamps any
    traversal above the vault root to the root.

    Args:
        target: Raw target string from the link (may include ``#fragment``).
        source_rel: Relative POSIX path of the source document
            (e.g. ``"Journal/2024/today.md"``).

    Returns:
        A ``(resolved_path, fragment)`` tuple where ``resolved_path`` is the
        vault-relative POSIX path with forward slashes and ``fragment`` is the
        heading identifier or ``None``.
    """
    # Split fragment.
    fragment: str | None = None
    if "#" in target:
        idx = target.index("#")
        fragment = target[idx + 1 :] or None
        target = target[:idx]

    if not target:
        # Link with only a fragment — points to the source document itself.
        return source_rel, fragment

    # Resolve relative to the source document's directory.
    source_dir_parts = PurePosixPath(source_rel).parent
    resolved = source_dir_parts / target

    # Normalise (collapse /../ and /./) without going above root.
    parts = resolved.parts
    normalised: list[str] = []
    for part in parts:
        if part == "..":
            if normalised:
                normalised.pop()
            # else: clamp — traversal above root is dropped silently
        elif part != ".":
            normalised.append(part)

    resolved_str = "/".join(normalised)

    return resolved_str, fragment


def extract_links(content: str, source_path: str) -> list[LinkInfo]:
    """Extract all links from a markdown document body.

    Handles three link formats:

    * **Inline markdown**: ``[text](path.md)``
    * **Reference-style**: ``[text][ref]`` with ``[ref]: path.md``
    * **Wikilinks**: ``[[path]]`` or ``[[path|alias]]``

    Links inside fenced code blocks and inline code spans are ignored.
    External URLs (``http://``, ``https://``, ``mailto:``) are skipped.

    Args:
        content: Markdown body text (frontmatter already stripped).
        source_path: Relative POSIX path of the source document, used for
            resolving relative link targets (e.g. ``"Journal/2024/today.md"``).

    Returns:
        List of :class:`~markdown_vault_mcp.types.LinkInfo` objects.
    """
    clean = _strip_code_spans(content)
    links: list[LinkInfo] = []

    # --- Inline markdown links ---
    for m in _RE_INLINE_LINK.finditer(clean):
        # Skip image links: ![alt](src) shares the same bracket syntax.
        if m.start() > 0 and clean[m.start() - 1] == "!":
            continue
        text = m.group(1)
        raw_target = m.group(2).strip()
        if any(raw_target.startswith(p) for p in _EXTERNAL_URL_PREFIXES):
            continue
        if raw_target.startswith("#"):
            # Pure anchor link — skip (points to a section in the same doc).
            continue
        resolved, fragment = _resolve_link_path(raw_target, source_path)
        links.append(
            LinkInfo(
                target_path=resolved,
                link_text=text,
                link_type="markdown",
                fragment=fragment,
                raw_target=raw_target,
            )
        )

    # --- Reference-style links ---
    # Collect reference definitions first.
    ref_defs: dict[str, str] = {}
    for m in _RE_REF_DEF.finditer(clean):
        ref_key = m.group(1).strip().lower()
        ref_target = m.group(2).strip()
        # Strip optional CommonMark title: "...", '...', or (...)
        ref_target = re.sub(r'\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\))\s*$', "", ref_target)
        ref_defs[ref_key] = ref_target

    for m in _RE_REF_USAGE.finditer(clean):
        text = m.group(1)
        ref = m.group(2).strip() or text  # empty [ref] falls back to link text
        ref_key = ref.lower()
        raw_target = ref_defs.get(ref_key)
        if raw_target is None:
            continue
        if any(raw_target.startswith(p) for p in _EXTERNAL_URL_PREFIXES):
            continue
        if raw_target.startswith("#"):
            continue
        resolved, fragment = _resolve_link_path(raw_target, source_path)
        links.append(
            LinkInfo(
                target_path=resolved,
                link_text=text,
                link_type="reference",
                fragment=fragment,
                raw_target=raw_target,
            )
        )

    # --- Wikilinks ---
    for m in _RE_WIKILINK.finditer(clean):
        raw_path = m.group(1).strip()
        alias = m.group(2)
        link_text = alias.strip() if alias else raw_path

        # Split fragment BEFORE appending .md so [[note#heading]] works.
        fragment = None
        if "#" in raw_path:
            idx = raw_path.index("#")
            fragment = raw_path[idx + 1 :] or None
            raw_path = raw_path[:idx]

        # raw_target for wikilinks: path portion before .md is appended,
        # with fragment re-attached so the original [[Note#section]] is preserved.
        wikilink_raw_target = raw_path + ("#" + fragment if fragment else "")

        # Wikilinks without .md extension: append it.
        if not raw_path.lower().endswith(".md"):
            raw_path = raw_path + ".md"

        # Obsidian vault-wide resolution semantics:
        # Only explicit relative prefixes (./  ../) use source-relative path
        # resolution.  All other wikilinks — bare [[Note]] and path-qualified
        # [[folder/Note]] — are stored as-is so that
        # FTSIndex.resolve_vault_wikilinks() can resolve them vault-wide
        # against the full indexed document set.
        if raw_path.startswith("./") or raw_path.startswith("../"):
            resolved, path_fragment = _resolve_link_path(raw_path, source_path)
            fragment = fragment or path_fragment
        else:
            resolved = raw_path
        links.append(
            LinkInfo(
                target_path=resolved,
                link_text=link_text,
                link_type="wikilink",
                fragment=fragment,
                raw_target=wikilink_raw_target,
            )
        )

    return links


def parse_note(
    path: Path,
    source_dir: Path,
    chunk_strategy: ChunkStrategy | None = None,
) -> ParsedNote:
    """Parse a single markdown file into a ParsedNote.

    Reads raw bytes for hash computation, decodes as UTF-8, parses frontmatter
    with ``python-frontmatter``, resolves title, and chunks content.

    Args:
        path: Absolute path to the markdown file.
        source_dir: Root directory of the vault; used to derive the
            document's relative identity path.
        chunk_strategy: Chunking strategy to apply. Defaults to
            :class:`HeadingChunker`.

    Returns:
        A :class:`~markdown_vault_mcp.types.ParsedNote` instance.

    Raises:
        UnicodeDecodeError: If the file cannot be decoded as UTF-8. Callers
            such as :func:`scan_directory` catch this and skip the file.
    """
    if chunk_strategy is None:
        chunk_strategy = HeadingChunker()

    raw_bytes = path.read_bytes()
    content_hash = compute_etag(raw_bytes)
    modified_at = path.stat().st_mtime

    # May raise UnicodeDecodeError — propagated to caller.
    text = raw_bytes.decode("utf-8")

    # python-frontmatter strips the YAML block and returns the body separately.
    post = frontmatter.loads(text)
    metadata: dict[str, Any] = dict(post.metadata)
    body: str = post.content

    title = _resolve_title(metadata, body, path)

    # Relative path from source_dir, always using forward slashes.
    rel_path = path.relative_to(source_dir)
    rel_str = rel_path.as_posix()

    chunks = chunk_strategy.chunk(body, metadata)
    links = extract_links(body, rel_str)

    note = ParsedNote(
        path=rel_str,
        frontmatter=metadata,
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
        links=links,
    )
    logger.debug(
        "parse_note: %s — title=%r chunks=%d links=%d",
        rel_str,
        title,
        len(chunks),
        len(links),
    )
    return note


def scan_directory(
    source_dir: Path,
    *,
    glob_pattern: str = "**/*.md",
    exclude_patterns: list[str] | None = None,
    required_frontmatter: list[str] | None = None,
    chunk_strategy: ChunkStrategy | None = None,
) -> Iterator[ParsedNote]:
    """Discover and parse all markdown files under ``source_dir``.

    Yields :class:`~markdown_vault_mcp.types.ParsedNote` objects. Fault-tolerant: a
    single bad file (UTF-8 decode error, I/O error) is skipped with a
    ``WARNING`` log entry; the scan continues.

    Args:
        source_dir: Root directory to scan.
        glob_pattern: Glob pattern relative to ``source_dir`` that selects
            files to scan. Defaults to ``"**/*.md"``.
        exclude_patterns: List of glob patterns matched against each file's
            relative POSIX path using :func:`fnmatch.fnmatch`. Files whose
            path matches any pattern are excluded. Supports ``**`` on all
            Python versions (unlike :meth:`pathlib.Path.match` in < 3.12).
            Example: ``[".obsidian/**", "_templates/**"]``.
        required_frontmatter: If provided, documents missing any of the listed
            frontmatter fields are excluded from the results. The number of
            skipped documents is logged at ``INFO`` level after the scan.
        chunk_strategy: Chunking strategy to pass to :func:`parse_note`.
            Defaults to :class:`HeadingChunker`.

    Yields:
        Parsed notes in filesystem traversal order.
    """
    exclude_patterns = exclude_patterns or []
    skipped_required: int = 0

    for abs_path in sorted(source_dir.glob(glob_pattern, **GLOB_SYMLINK_KWARGS)):
        if not abs_path.is_file():
            continue

        # Compute relative path for exclude matching.
        try:
            rel = abs_path.relative_to(source_dir)
        except ValueError:
            # Shouldn't happen, but be safe.
            logger.warning("File outside source_dir, skipping: %s", abs_path)
            continue

        # Check exclude patterns against the relative POSIX path string.
        # fnmatch is used instead of Path.match() because Path.match() does
        # not support ** patterns in Python < 3.12.
        rel_posix = rel.as_posix()
        if any(fnmatch.fnmatch(rel_posix, pat) for pat in exclude_patterns):
            logger.debug("Excluding %s (matched exclude pattern)", rel)
            continue

        # Parse the file; skip on decode / I/O / YAML errors.
        try:
            note = parse_note(abs_path, source_dir, chunk_strategy)
        except UnicodeDecodeError:
            logger.warning(
                "Skipping %s: cannot decode as UTF-8", abs_path, exc_info=False
            )
            continue
        except OSError as exc:
            logger.warning("Skipping %s: I/O error (%s)", abs_path, exc)
            continue
        except yaml.YAMLError as exc:
            logger.warning("Skipping %s: parse error (%s)", abs_path, exc)
            continue
        except Exception as exc:
            logger.warning(
                "Skipping %s: unexpected error (%s)", abs_path, exc, exc_info=True
            )
            continue

        # Apply required_frontmatter filter.
        if required_frontmatter:
            missing = [
                field for field in required_frontmatter if field not in note.frontmatter
            ]
            if missing:
                logger.debug(
                    "Skipping %s: missing required frontmatter fields: %s",
                    rel,
                    missing,
                )
                skipped_required += 1
                continue

        yield note

    if skipped_required:
        logger.info(
            "%d document(s) skipped due to missing required frontmatter fields.",
            skipped_required,
        )
