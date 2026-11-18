"""Shared builder for context-enriched embedding text.

One :class:`EmbedTextBuilder` instance is constructed by
:class:`~markdown_vault_mcp.vault.Vault` and shared by every site that
produces embedding input text (hot reindex, cold build, boot convergence,
deferred flush). Using a single builder everywhere is load-bearing: if any
site embedded plain chunk content while another embedded enriched text, the
boot convergence pass would "heal" the enriched vectors back to plain.

The builder has these formats:

* **v1** — the default: :meth:`EmbedTextBuilder.build` returns the chunk
  content byte-for-byte unchanged, so default configurations are an exact
  behavioural no-op when listed-concept enrichment is disabled.
* **v1;listed** — chunk inputs stay byte-identical to v1. A designated
  folder's listed concept gains separate inputs for its title and
  description (:meth:`EmbedTextBuilder.listing_pieces`), each within the chunk
  character cap, because the generated listing that carried them has no
  vector.
* **v2** — active when ``embed_context`` is enabled or any
  ``searchable_fields`` are configured: the chunk content is prefixed with
  the document title, the chunk heading (when present), and — on the first
  chunk only — the newline-joined scalar frontmatter values of the
  configured fields.

:meth:`EmbedTextBuilder.format_token` canonicalises the active format into
a string persisted in the vector sidecar's ``index_metadata`` so a format
flip is detected at load time and routes to a full re-embed.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.utils import normalize_folder

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)


def is_embeddable(text: str) -> bool:
    """Return whether *text* is safe to submit to an embedding provider.

    A note with no body — zero-byte, frontmatter-only, or whitespace-only —
    chunks to a single chunk whose content is ``""``, and under the default
    v1 format :meth:`EmbedTextBuilder.build` returns that content verbatim.
    Every OpenAI-compatible provider rejects an empty input string with a
    hard HTTP 400 (Voyage: ``Input cannot contain empty strings or empty
    lists``), and the rejection fails the whole *batch*, not just the
    offending element (#1087).

    Whitespace-only text is treated the same way: it carries no signal, and
    providers differ on whether they accept it.

    Args:
        text: A candidate embedding input, as returned by
            :meth:`EmbedTextBuilder.build`.

    Returns:
        ``True`` when *text* has non-whitespace content.
    """
    return bool(text.strip())


def fields_text(
    frontmatter_or_json: Mapping[str, Any] | str | None,
    fields: Sequence[str],
) -> str:
    """Return the newline-joined scalar values of *fields* from frontmatter.

    Shared by the FTS ``summary`` column population and the embedding
    preamble so both are always computed identically.

    Args:
        frontmatter_or_json: A parsed frontmatter mapping, a
            ``frontmatter_json`` string (as stored in the ``documents``
            table), or ``None``.
        fields: Frontmatter keys to extract, in order.

    Returns:
        The values of the listed fields — scalars only (``str``, ``int``,
        ``float``, ``bool``, and ``date``/``time``/``datetime``, the last
        three coerced to ISO strings); lists/dicts/``None`` are skipped —
        joined with newlines. ``""`` when no fields are configured, the
        frontmatter is empty, or the JSON does not parse to an object.
    """
    if not fields or not frontmatter_or_json:
        return ""
    frontmatter: Mapping[str, Any]
    if isinstance(frontmatter_or_json, str):
        try:
            parsed = json.loads(frontmatter_or_json)
        except json.JSONDecodeError:
            logger.warning("fields_text_invalid_json")
            return ""
        if not isinstance(parsed, dict):
            return ""
        frontmatter = parsed
    else:
        frontmatter = frontmatter_or_json
    parts: list[str] = []
    for key in fields:
        value = frontmatter.get(key)
        # YAML parses date-like scalars to datetime types; coerce them to ISO
        # strings (as fts_index._json_default does for the documents table) so
        # the value stays searchable AND the live-dict build path matches the
        # frontmatter_json convergence path — otherwise every date-bearing note
        # would re-embed on the first boot.
        if isinstance(value, (datetime.date, datetime.time)):
            value = value.isoformat()
        if isinstance(value, (str, int, float, bool)):
            text = str(value).strip()
            if text:
                parts.append(text)
        elif value is not None:
            # A configured searchable field that holds a list/dict contributes
            # nothing (e.g. a YAML `tags:` list). Log so the operator can tell
            # "unsupported type" apart from "field not present".
            logger.debug(
                "fields_text_non_scalar_field_skipped key=%s type=%s",
                key,
                type(value).__name__,
            )
    return "\n".join(parts)


@dataclass(frozen=True)
class EmbedTextBuilder:
    """Builds the text submitted to the embedding provider for one chunk.

    Args:
        embed_context: When ``True``, forces format v2 (document title and
            chunk heading enrichment) even when ``searchable_fields`` is
            empty. Any non-empty ``searchable_fields`` also activates v2, so
            this flag is only load-bearing on its own.
        searchable_fields: Frontmatter keys whose scalar values form the
            first-chunk preamble (also switches to format v2 when
            non-empty).
        listed: Embed a listed concept's title and description as separate inputs.
        listed_scope: Folder patterns that select listed concepts.
        listed_limit: The chunk character cap each listing input is
            bounded by; ``None`` leaves it unbounded. It is not part
            of the format token, like the chunker cap it comes from.
    """

    embed_context: bool = False
    searchable_fields: tuple[str, ...] = field(default=())
    listed: bool = False
    listed_scope: tuple[str, ...] = ()
    listed_limit: int | None = None

    @property
    def is_v2(self) -> bool:
        """``True`` when the v2 context or field format is active."""
        return self.embed_context or bool(self.searchable_fields)

    def format_token(self) -> str:
        """Canonical format token persisted in the vector sidecar.

        Returns:
            ``"v1"`` for the default no-op format, else
            ``"v2;fields=<comma-joined field names>"``. The listed policy
            appends ``";listed;scope="`` and its folder patterns.
        """
        token = "v2;fields=" + ",".join(self.searchable_fields) if self.is_v2 else "v1"
        if self.listed:
            scope = sorted(
                {
                    "/".join(
                        part
                        for part in (normalize_folder(pattern) or "").split("/")
                        if part and part != "."
                    )
                    for pattern in self.listed_scope
                }
            )
            token += ";listed;scope=" + json.dumps(scope, separators=(",", ":"))
        return token

    def fields_text(self, frontmatter_or_json: Mapping[str, Any] | str | None) -> str:
        """Return the first-chunk preamble for this builder's fields.

        See the module-level :func:`fields_text` for the exact contract.
        """
        return fields_text(frontmatter_or_json, self.searchable_fields)

    def is_listed(self, path: str) -> bool:
        """Return whether *path* is a concept that a designated folder lists."""
        from markdown_vault_mcp._okf_convention import folder_is_designated
        from markdown_vault_mcp.okf import is_okf_reserved_file

        return (
            self.listed
            and bool(path)
            and not is_okf_reserved_file(path)
            and folder_is_designated(path.rpartition("/")[0], self.listed_scope)
        )

    def listing_pieces(
        self, title: str, frontmatter_or_json: Mapping[str, Any] | str | None
    ) -> list[str]:
        """Split a listed concept's title and description into inputs within the cap.

        The existing chunker splits the text, so no piece loses words and every
        built input stays within the cap unless the v2 head alone takes half of
        it. The chunk inputs are untouched, so this adds rows and never clips a
        body or a configured field.

        Args:
            title: Resolved note title.
            frontmatter_or_json: Parsed or JSON-encoded frontmatter.

        Returns:
            The non-empty pieces; empty when the note has neither.
        """
        description = fields_text(frontmatter_or_json, ("description",))
        text = "\n".join(part for part in (title, description) if part)
        if self.listed_limit is None:
            return [text] if text else []
        from markdown_vault_mcp.scanner import HeadingChunker

        head = len(
            self.build(
                title=title,
                heading=None,
                content="",
                fields_text="",
                is_first_chunk=False,
            )
        )
        room = max(self.listed_limit - head, self.listed_limit // 2)
        pieces = HeadingChunker(max_chunk_chars=room).chunk(text, {})
        return [piece.content.strip() for piece in pieces if piece.content.strip()]

    def build(
        self,
        *,
        title: str,
        heading: str | None,
        content: str,
        fields_text: str,
        is_first_chunk: bool,
    ) -> str:
        """Return the embedding input text for one chunk.

        Args:
            title: Resolved document title.
            heading: The chunk's heading, or ``None``/``""`` when absent.
            content: Raw chunk content.
            fields_text: Pre-computed preamble (see :meth:`fields_text`);
                only used on the first chunk.
            is_first_chunk: ``True`` for the document's chunk-0 row.

        Returns:
            v1: *content* unchanged.
            v2: the title line,
            the heading line (omitted when empty), and — first chunk only —
            the non-empty *fields_text*, followed by a blank line and the
            content.
        """
        if not self.is_v2:
            return content
        lines = [title]
        if heading:
            lines.append(heading)
        if is_first_chunk and fields_text:
            lines.append(fields_text)
        return "\n".join(lines) + "\n\n" + content
