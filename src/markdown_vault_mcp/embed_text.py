"""Shared builder for context-enriched embedding text.

One :class:`EmbedTextBuilder` instance is constructed by
:class:`~markdown_vault_mcp.vault.Vault` and shared by every site that
produces embedding input text (hot reindex, cold build, boot convergence,
deferred flush). Using a single builder everywhere is load-bearing: if any
site embedded plain chunk content while another embedded enriched text, the
boot convergence pass would "heal" the enriched vectors back to plain.

The builder has two formats:

* **v1** — ``EMBED_INPUT=body``: :meth:`EmbedTextBuilder.build` returns the
  chunk content byte-for-byte unchanged.
* **v2** — ``EMBED_INPUT=context`` or ``context+fields``: the chunk content
  is prefixed with the document title and the chunk heading (when present);
  under ``context+fields`` the first chunk also carries the newline-joined
  scalar frontmatter values of the searchable fields.

With ``EMBED_INPUT`` unset, :func:`resolve_embed_input` derives the input
from the deprecated ``EMBED_CONTEXT`` and the searchable fields exactly as
before #2311, so an upgrade re-embeds nothing.

:meth:`EmbedTextBuilder.format_token` canonicalises the active format into
a string persisted in the vector sidecar's ``index_metadata`` so a format
flip is detected at load time and routes to a full re-embed. Identical
input gives an identical token: ``context`` shares ``v2;fields=`` with
``context+fields`` over no fields.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

#: What reaches the embedding provider for each chunk (``EMBED_INPUT``).
EmbedInput = Literal["body", "context", "context+fields"]

#: Every accepted ``EMBED_INPUT`` value, in documentation order.
EMBED_INPUTS: tuple[EmbedInput, ...] = ("body", "context", "context+fields")


def check_embed_input(
    embed_input: str | None, *, embed_context: bool
) -> EmbedInput | None:
    """Validate an explicit ``EMBED_INPUT`` against the deprecated ``EMBED_CONTEXT``.

    ``EMBED_CONTEXT=true`` asserts that the note title and chunk heading reach
    the embedder, so it conflicts with ``body`` alone. ``EMBED_CONTEXT=false``
    asserts nothing on its own (searchable fields still enriched the input),
    so it conflicts with no value.

    Args:
        embed_input: The ``EMBED_INPUT`` value, or ``None`` when unset.
        embed_context: The deprecated ``EMBED_CONTEXT`` flag.

    Returns:
        The value typed as :data:`EmbedInput`, or ``None`` when unset.

    Raises:
        ValueError: If *embed_input* is not one of :data:`EMBED_INPUTS`, or
            if it is ``body`` while *embed_context* is ``True``.
    """
    if embed_input is None:
        return None
    if embed_input not in EMBED_INPUTS:
        raise ValueError(
            "MARKDOWN_VAULT_MCP_EMBED_INPUT must be one of "
            f"{', '.join(EMBED_INPUTS)}, got {embed_input!r}"
        )
    if embed_context and embed_input == "body":
        raise ValueError(
            "MARKDOWN_VAULT_MCP_EMBED_CONTEXT=true conflicts with "
            "MARKDOWN_VAULT_MCP_EMBED_INPUT=body: EMBED_CONTEXT adds the note "
            "title and chunk heading, which body leaves out. Remove the "
            "deprecated MARKDOWN_VAULT_MCP_EMBED_CONTEXT."
        )
    return embed_input


def resolve_embed_input(
    embed_input: str | None,
    *,
    embed_context: bool,
    searchable_fields: Sequence[str],
) -> EmbedInput:
    """Return the effective embedding input for a configuration.

    An explicit *embed_input* wins. Unset, the rule from before #2311
    applies: ``context+fields`` when *embed_context* is on or any searchable
    field is configured, else ``body``.

    Args:
        embed_input: The ``EMBED_INPUT`` value, or ``None`` when unset.
        embed_context: The deprecated ``EMBED_CONTEXT`` flag.
        searchable_fields: The effective searchable frontmatter fields.

    Returns:
        The effective :data:`EmbedInput`.

    Raises:
        ValueError: As :func:`check_embed_input`.
    """
    explicit = check_embed_input(embed_input, embed_context=embed_context)
    if explicit is not None:
        return explicit
    if embed_context or searchable_fields:
        return "context+fields"
    return "body"


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
        embed_context: The deprecated ``EMBED_CONTEXT`` flag; only consulted
            when *embed_input* is ``None`` (see :func:`resolve_embed_input`).
        searchable_fields: The effective searchable frontmatter fields, kept
            whole whatever the input so the keyword index's provenance does
            not depend on what reaches the embedder.
        embed_input: The explicit ``EMBED_INPUT``, or ``None`` to derive it.

    Raises:
        ValueError: As :func:`check_embed_input`, at construction.
    """

    embed_context: bool = False
    searchable_fields: tuple[str, ...] = field(default=())
    embed_input: EmbedInput | None = None
    mode: EmbedInput = field(init=False, compare=False)

    def __post_init__(self) -> None:
        """Resolve the effective input once, refusing a conflicting pair."""
        mode = resolve_embed_input(
            self.embed_input,
            embed_context=self.embed_context,
            searchable_fields=self.searchable_fields,
        )
        object.__setattr__(self, "mode", mode)

    @property
    def embedded_fields(self) -> tuple[str, ...]:
        """The searchable fields whose values reach the embedder."""
        return self.searchable_fields if self.mode == "context+fields" else ()

    @property
    def is_v2(self) -> bool:
        """``True`` when the title and heading prefix the chunk."""
        return self.mode != "body"

    def format_token(self) -> str:
        """Canonical format token persisted in the vector sidecar.

        Returns:
            ``"v1"`` for ``body``, else ``"v2;fields=<comma-joined embedded
            field names>"`` (empty after ``=`` under ``context``).
        """
        if not self.is_v2:
            return "v1"
        return "v2;fields=" + ",".join(self.embedded_fields)

    def fields_text(self, frontmatter_or_json: Mapping[str, Any] | str | None) -> str:
        """Return the first-chunk preamble for this builder's embedded fields.

        See the module-level :func:`fields_text` for the exact contract; it
        is ``""`` unless the input is ``context+fields``.
        """
        return fields_text(frontmatter_or_json, self.embedded_fields)

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
            v1: *content* unchanged (byte-exact no-op). v2: the title line,
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
