"""File discovery, frontmatter parsing, and chunking for markdown-vault-mcp."""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import frontmatter
import yaml

from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.types import Chunk, LinkInfo, ParsedNote, SkippedFile
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS, iter_markdown_files
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
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
    single token longer than ``max_chunk_chars``, which is unsplittable; and,
    when ``chunk_overlap_words`` is greater than zero, an overlapped fragment
    may exceed either budget by up to ``chunk_overlap_words`` words).

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
        chunk_overlap_words: int = 0,
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
            chunk_overlap_words: Words of overlap prepended to each budget-split
                fragment from the previous fragment of the same section. ``0``
                disables overlap. Never crosses a heading boundary.
        """
        self.short_doc_lines = short_doc_lines
        self.max_chunk_words = max_chunk_words
        self.max_chunk_chars = max_chunk_chars
        self.chunk_overlap_words = chunk_overlap_words

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
        heading at all. See the class docstring for the two exceptions to
        this invariant, including the ``chunk_overlap_words`` case.
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
        fragment fits within the configured word **and** char budgets, then
        applies ``chunk_overlap_words`` overlap via :meth:`_apply_overlap`,
        which may push a returned fragment over either budget by up to that
        many words.

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
            One or more chunks. Each fits the word and char budgets from the
            bin-packing phase; after ``chunk_overlap_words`` overlap is applied
            a fragment may exceed either budget by up to that many words.
            Returns ``[chunk]`` unchanged when the content is whitespace-only
            (no paragraphs to split on).
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
        return self._apply_overlap(out)

    def _apply_overlap(self, fragments: list[Chunk]) -> list[Chunk]:
        """Prepend each fragment's trailing words to the next (same section).

        Applied only to the budget-split fragments of one leaf section, so
        overlap never crosses a heading or heading-refined boundary. Words come
        from the original previous fragment (no accumulation); start_line is
        preserved (overlap is duplicated retrieval context, not new source).
        """
        n = self.chunk_overlap_words
        if n <= 0 or len(fragments) < 2:
            return fragments
        out: list[Chunk] = [fragments[0]]
        for i in range(1, len(fragments)):
            overlap = " ".join(fragments[i - 1].content.split()[-n:])
            cur = fragments[i]
            content = f"{overlap}\n\n{cur.content}" if overlap else cur.content
            out.append(
                Chunk(
                    heading=cur.heading,
                    heading_level=cur.heading_level,
                    content=content,
                    start_line=cur.start_line,
                )
            )
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


# ATX heading detection, matching ``HeadingChunker._split_at_levels``: an
# ``#``-run of 1-6 followed by whitespace and text, tested against the
# right-stripped line.  Deliberately *not* code-fence aware, so a section's
# span here matches the boundaries the chunker used when indexing.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def normalize_heading(heading: str) -> str:
    """Collapse internal whitespace runs to single spaces and strip.

    Rendered TOCs and markdown editors normalise whitespace inconsistently
    (single vs double space after a numbered prefix, tabs vs spaces, trailing
    whitespace), so an LLM caller rarely reproduces the on-disk byte sequence.
    Normalising both sides before comparison closes that gap.

    Args:
        heading: Heading string to normalise.

    Returns:
        The heading with whitespace runs collapsed to single spaces, stripped.
    """
    return " ".join(heading.split())


def _scan_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return ``(line_index, level, heading_text)`` for each ATX heading.

    Heading detection mirrors :meth:`HeadingChunker._split_at_levels` so the
    section spans derived here line up with how the document was chunked.

    Args:
        lines: Document body lines (``splitlines(keepends=True)``).

    Returns:
        One tuple per heading, in document order.
    """
    out: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line.rstrip())
        if m:
            out.append((idx, len(m.group(1)), m.group(2).strip()))
    return out


def list_section_headings(text: str) -> list[str]:
    """Return the document's ATX heading texts in document order.

    Frontmatter is stripped before scanning. Used to build "did you mean"
    suggestions when a section lookup misses.

    Args:
        text: Raw markdown file text (frontmatter included).

    Returns:
        Heading strings as written (whitespace not normalised), in order.
    """
    body = frontmatter.loads(text).content
    return [h for _, _, h in _scan_headings(body.splitlines(keepends=True))]


def extract_section(text: str, heading: str) -> str | None:
    """Return the full body of the first section matching *heading*.

    The span runs from just after the matched heading line to the line before
    the next heading at the same or higher level (or end of document). The
    heading line itself is excluded, matching the chunker's section-content
    convention. Matching collapses internal whitespace and is case-sensitive.
    Frontmatter is stripped before scanning.

    Unlike a per-chunk lookup, this reconstructs the whole section regardless of
    how the chunker fragmented it (an over-budget body split on paragraphs, or a
    parent split at its sub-headings), so the complete section is returned (#741).

    Args:
        text: Raw markdown file text (frontmatter included).
        heading: Heading to match (internal whitespace collapsed).

    Returns:
        The section's body text, or ``None`` when no heading matches.
    """
    norm_query = normalize_heading(heading)
    if not norm_query:
        return None
    body = frontmatter.loads(text).content
    lines = body.splitlines(keepends=True)
    headings = _scan_headings(lines)
    for i, (idx, level, htext) in enumerate(headings):
        if normalize_heading(htext) != norm_query:
            continue
        end = len(lines)
        for nxt_idx, nxt_level, _ in headings[i + 1 :]:
            if nxt_level <= level:
                end = nxt_idx
                break
        return "".join(lines[idx + 1 : end])
    return None


def _frontmatter_title(metadata: dict[str, Any], key: str) -> str | None:
    """Return the stripped string value of *key*, or ``None`` when unusable."""
    value = metadata.get(key)
    if isinstance(value, str):
        title = value.strip()
        if title:
            return title
    return None


def _resolve_title(
    metadata: dict[str, Any],
    content: str,
    path: Path,
    title_field: str = "title",
) -> str:
    """Resolve the document title using the priority order from the design spec.

    Priority: frontmatter *title_field* → frontmatter ``title`` (only when
    *title_field* is not ``"title"``) → first H1 heading → filename without
    extension. Non-string or empty frontmatter values are skipped.

    Args:
        metadata: Parsed frontmatter dict.
        content: Markdown body (frontmatter stripped).
        path: Absolute path to the file (used for filename fallback).
        title_field: Frontmatter key consulted first for the title.

    Returns:
        Resolved title string.
    """
    # 1. Configured frontmatter title field.
    title = _frontmatter_title(metadata, title_field)
    if title is not None:
        return title

    # 2. Standard ``title`` field, when a custom field was configured.
    if title_field != "title":
        title = _frontmatter_title(metadata, "title")
        if title is not None:
            return title

    # 3. First H1 heading in content.
    for line in content.splitlines():
        m = re.match(r"^#\s+(.+)$", line.rstrip())
        if m:
            return m.group(1).strip()

    # 4. Filename without extension.
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
# Markdown footnotes ([^label] / [^label]: body) differ from reference-style
# links by exactly this character, and both reference regexes match them.
_FOOTNOTE_LABEL_PREFIX = "^"
# Wikilink: [[path]], [[path|alias]], or [[path\|alias]] (Obsidian table-cell escape)
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

    if target.startswith("/"):
        # Vault-root-absolute link (`/guides/note.md` — OKF's recommended
        # style, #969): resolve against the vault root. Joining an absolute
        # path onto a base with pathlib would *replace* the base and keep
        # the leading slash, serializing as an unresolvable `//...` target.
        # removeprefix (not lstrip): exactly one slash marks root-relative;
        # `//`-prefixed targets never reach here (external-URL filter), and
        # this keeps that invariant from silently mattering.
        resolved = PurePosixPath(target.removeprefix("/"))
    else:
        # Resolve relative to the source document's directory.
        resolved = PurePosixPath(source_rel).parent / target

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
    External URLs (``http://``, ``https://``, ``mailto:``) are skipped, as
    are same-document anchors in every spelling — ``[text](#heading)``,
    ``[text][ref]`` with a ``#`` target, and ``[[#heading]]``.

    Args:
        content: Markdown body text (frontmatter already stripped).
        source_path: Relative POSIX path of the source document, used for
            resolving relative link targets (e.g. ``"Journal/2024/today.md"``).

    Returns:
        List of :class:`~markdown_vault_mcp.types.LinkInfo` objects.
    """
    clean = _strip_code_spans(content)
    return [
        *_extract_inline_links(clean, source_path),
        *_extract_reference_links(clean, source_path),
        *_extract_wikilinks(clean, source_path),
    ]


def _extract_inline_links(clean: str, source_path: str) -> list[LinkInfo]:
    """Extract inline ``[text](path.md)`` links from code-stripped content.

    Skips image links (``![alt](src)``), external URLs, and pure anchor
    links (``#section`` within the same document).
    """
    links: list[LinkInfo] = []
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

    return links


def _extract_reference_links(clean: str, source_path: str) -> list[LinkInfo]:
    """Extract reference-style ``[text][ref]`` links from code-stripped content.

    Collects the ``[ref]: path.md`` definitions first (optional CommonMark
    titles stripped), then resolves each usage; an empty ``[ref]`` falls
    back to the link text per CommonMark shortcut semantics. External URLs
    and pure anchors are skipped.

    Markdown footnotes are skipped on both halves: a footnote definition
    (``[^label]: body``) is prose, not a link target, and two adjacent
    footnote references (``[^a][^b]``) are not one reference-style link
    (#1104).
    """
    links: list[LinkInfo] = []
    # Collect reference definitions first.
    ref_defs: dict[str, str] = {}
    for m in _RE_REF_DEF.finditer(clean):
        ref_key = m.group(1).strip().lower()
        if ref_key.startswith(_FOOTNOTE_LABEL_PREFIX):
            # Footnote definition: the body is prose, so storing it as a
            # target resolved whatever the footnote said as a vault path.
            continue
        ref_target = m.group(2).strip()
        # Strip optional CommonMark title: "...", '...', or (...)
        ref_target = re.sub(r'\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\))\s*$', "", ref_target)
        ref_defs[ref_key] = ref_target

    for m in _RE_REF_USAGE.finditer(clean):
        text = m.group(1)
        ref = m.group(2).strip() or text  # empty [ref] falls back to link text
        if text.startswith(_FOOTNOTE_LABEL_PREFIX) or ref.startswith(
            _FOOTNOTE_LABEL_PREFIX
        ):
            # Consecutive footnote references read as one [text][ref] pair,
            # taking the first label as link text and the second as the
            # reference — and finditer, being non-overlapping, then swallows
            # the second label so a third reference goes unseen.
            continue
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

    return links


def _extract_wikilinks(clean: str, source_path: str) -> list[LinkInfo]:
    """Extract ``[[path]]`` / ``[[path|alias]]`` wikilinks from content.

    Handles the Obsidian table-cell pipe escape (#731), fragment splitting
    before the ``.md`` append, and Obsidian vault-wide resolution
    semantics: only explicit relative prefixes (``./`` / ``../``) resolve
    source-relative; bare and path-qualified wikilinks are stored as-is
    for ``FTSIndex.resolve_vault_wikilinks()`` to resolve vault-wide.

    Fragment-only wikilinks (``[[#Heading]]``) are skipped, matching how
    :func:`_extract_inline_links` treats ``[text](#heading)`` (#1107).
    """
    links: list[LinkInfo] = []
    for m in _RE_WIKILINK.finditer(clean):
        raw_path = m.group(1).strip()
        # Obsidian escapes the alias pipe as `\|` in table cells, so the target
        # keeps a trailing `\` (#731). Strip it BEFORE wikilink_raw_target is
        # built — raw_target is the resolve_vault_wikilinks() anchor and must be
        # the clean stem, else the link never resolves.
        if raw_path.endswith("\\"):
            raw_path = raw_path[:-1]
        alias = m.group(2)
        link_text = alias.strip() if alias else raw_path

        # Split fragment BEFORE appending .md so [[note#heading]] works.
        fragment = None
        if "#" in raw_path:
            idx = raw_path.index("#")
            fragment = raw_path[idx + 1 :] or None
            raw_path = raw_path[:idx]

        if not raw_path:
            # Fragment-only wikilink ([[#Heading]]) — Obsidian's same-note
            # heading reference, not a vault link. Skipped for the same
            # reason _extract_inline_links skips [text](#heading): it is
            # intra-document navigation, and recording it would add a
            # self-edge that suppresses orphan detection and inflates
            # backlink counts. Appending ".md" to the empty path portion is
            # what used to store the literal target ".md", which cannot
            # exist and so was always reported broken (#1107).
            continue

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
    *,
    title_field: str = "title",
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
        title_field: Frontmatter key consulted first when resolving the
            document title (see :func:`_resolve_title`).

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

    # Decode for frontmatter parsing, stripping a leading BOM (#673). The hash
    # above is over the raw on-disk bytes (BOM included). May raise
    # UnicodeDecodeError — propagated to caller.
    text = decode_utf8(raw_bytes)

    # python-frontmatter strips the YAML block and returns the body separately.
    post = frontmatter.loads(text)
    metadata: dict[str, Any] = dict(post.metadata)
    body: str = post.content

    title = _resolve_title(metadata, body, path, title_field)

    # Relative path from source_dir, always using forward slashes.
    rel_path = path.relative_to(source_dir)
    rel_str = rel_path.as_posix()

    chunks = chunk_strategy.chunk(body, metadata)
    links = extract_links(body, rel_str)
    # Measured here rather than summed from `chunks`: overlap duplicates text
    # across chunk boundaries, so a sum would over-count multi-chunk notes.
    content_chars = len(body)

    note = ParsedNote(
        path=rel_str,
        frontmatter=metadata,
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
        links=links,
        content_chars=content_chars,
    )
    logger.debug(
        "parse_note: %s — title=%r chunks=%d links=%d",
        rel_str,
        title,
        len(chunks),
        len(links),
    )
    return note


@dataclass(frozen=True)
class CategorizedSkip:
    """A deterministic skip from :func:`parse_note_categorized`.

    Wraps the surfaced :class:`~markdown_vault_mcp.types.SkippedFile` with
    the parsed note's ``content_hash`` when parsing itself succeeded (the
    ``missing_frontmatter`` category), so callers can record the hash of
    the exact bytes that were evaluated instead of re-hashing from disk —
    a second read could capture newer, now-valid content and stick the
    file in the skip state (#888 review). ``content_hash`` is ``None``
    for the exception categories, where no parsed note exists.

    Kept scanner-internal (not in ``types.py``): ``SkippedFile`` itself is
    serialized into the ``get_index_status`` tool output, and this wrapper
    must not widen that schema.
    """

    skip: SkippedFile
    content_hash: str | None = None


def parse_note_categorized(
    abs_path: Path,
    source_dir: Path,
    chunk_strategy: ChunkStrategy | None,
    *,
    rel_path: str,
    title_field: str = "title",
    required_frontmatter: list[str] | None = None,
    log_context: str = "scan",
) -> ParsedNote | CategorizedSkip | None:
    """Parse one markdown file, classifying failures into the skip taxonomy.

    The single owner of the deterministic-skip policy shared by
    :func:`scan_directory` and ``IndexManager.reindex`` (#762): decode,
    YAML, and unexpected parse failures surface as categorized
    :class:`CategorizedSkip` values, while a possibly-transient
    ``OSError`` returns ``None`` — callers must record nothing for it, so
    the file retries on the next scan (#775). A ``required_frontmatter``
    miss returns a ``missing_frontmatter`` skip carrying the parsed
    note's ``content_hash``, so callers can record the hash of the exact
    bytes that were evaluated without a second (raceable) disk read.

    Exception arms are logged here, prefixed with *log_context* (WARNING
    for decode/I/O/YAML; ERROR with traceback for unexpected failures).
    The missing-frontmatter case is **not** logged here — each caller
    reports it at its own level (scan: DEBUG per file plus an aggregate
    INFO; reindex: INFO per file).

    Args:
        abs_path: Absolute path of the file to parse.
        source_dir: Vault root, forwarded to :func:`parse_note`.
        chunk_strategy: Chunking strategy, forwarded to :func:`parse_note`.
        rel_path: Vault-relative POSIX path, used for the ``SkippedFile``
            and log messages.
        title_field: Frontmatter key consulted first for the title.
        required_frontmatter: Frontmatter keys that must be present.
        log_context: Prefix identifying the calling pipeline in logs.

    Returns:
        The parsed note on success, a :class:`CategorizedSkip` for a
        deterministic skip, or ``None`` for a transient I/O failure.
    """
    try:
        note = parse_note(abs_path, source_dir, chunk_strategy, title_field=title_field)
    except UnicodeDecodeError as exc:
        logger.warning(
            "%s: skipping %s — cannot decode as UTF-8 (%s)",
            log_context,
            rel_path,
            exc,
        )
        return CategorizedSkip(
            SkippedFile(path=rel_path, category="encoding_error", detail=str(exc))
        )
    except OSError as exc:
        # Possibly transient (I/O error) — do not surface; retry next scan.
        logger.warning("%s: skipping %s — I/O error (%s)", log_context, rel_path, exc)
        return None
    except yaml.YAMLError as exc:
        logger.warning("%s: skipping %s — parse error (%s)", log_context, rel_path, exc)
        return CategorizedSkip(
            SkippedFile(path=rel_path, category="parse_error", detail=str(exc))
        )
    except Exception as exc:
        logger.error(
            "%s: skipping %s — unexpected error (%s)",
            log_context,
            rel_path,
            exc,
            exc_info=True,
        )
        return CategorizedSkip(
            SkippedFile(path=rel_path, category="internal_error", detail=str(exc))
        )

    if required_frontmatter:
        missing = [f for f in required_frontmatter if f not in note.frontmatter]
        if missing:
            return CategorizedSkip(
                SkippedFile(
                    path=rel_path,
                    category="missing_frontmatter",
                    detail=f"missing: {missing}",
                ),
                content_hash=note.content_hash,
            )
    return note


def scan_directory(
    source_dir: Path,
    *,
    glob_pattern: str = "**/*.md",
    exclude_patterns: list[str] | None = None,
    required_frontmatter: list[str] | None = None,
    chunk_strategy: ChunkStrategy | None = None,
    on_skip: Callable[[SkippedFile], None] | None = None,
    title_field: str = "title",
) -> Iterator[ParsedNote]:
    """Discover and parse all markdown files under ``source_dir``.

    Yields :class:`~markdown_vault_mcp.types.ParsedNote` objects. Fault-tolerant:
    a single bad file (UTF-8 decode error, I/O error, YAML parse error) is
    skipped and the scan continues, logged at ``WARNING`` — except a genuinely
    unexpected parse failure (surfaced as ``internal_error``), which is logged
    at ``ERROR`` with a traceback.

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
        on_skip: Optional callback invoked once per *surfaced* deterministic
            skip with a :class:`~markdown_vault_mcp.types.SkippedFile`
            (categories ``"encoding_error"``, ``"parse_error"``,
            ``"missing_frontmatter"``, or ``"internal_error"``). It is
            **not** called for exclude-pattern matches (intentional) or
            transient ``OSError`` skips (self-healing). ``None`` (default)
            preserves the historical behaviour of silently skipping such
            files (#775).
        title_field: Frontmatter key consulted first when resolving each
            document's title; passed through to :func:`parse_note`.

    Yields:
        Parsed notes in filesystem traversal order.
    """
    exclude_patterns = exclude_patterns or []
    skipped_required: int = 0

    # For the default pattern, walk with directory pruning so excluded subtrees
    # are never descended; a non-default glob_pattern keeps the raw glob so its
    # semantics are unchanged. The per-file exclude filter below stays the
    # correctness layer either way.
    if glob_pattern == "**/*.md":
        discovered: Iterable[Path] = iter_markdown_files(source_dir, exclude_patterns)
    else:
        discovered = source_dir.glob(glob_pattern, **GLOB_SYMLINK_KWARGS)

    for abs_path in sorted(discovered):
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

        # Parse and categorize; skip on decode / I/O / YAML / frontmatter.
        outcome = parse_note_categorized(
            abs_path,
            source_dir,
            chunk_strategy,
            rel_path=rel_posix,
            title_field=title_field,
            required_frontmatter=required_frontmatter,
            log_context="scan",
        )
        if outcome is None:
            # Transient I/O failure (already logged) — retry next scan.
            continue
        if isinstance(outcome, CategorizedSkip):
            if outcome.skip.category == "missing_frontmatter":
                logger.debug(
                    "Skipping %s: missing required frontmatter fields (%s)",
                    rel,
                    outcome.skip.detail,
                )
                skipped_required += 1
            if on_skip is not None:
                on_skip(outcome.skip)
            continue

        yield outcome

    if skipped_required:
        logger.info(
            "%d document(s) skipped due to missing required frontmatter fields.",
            skipped_required,
        )
