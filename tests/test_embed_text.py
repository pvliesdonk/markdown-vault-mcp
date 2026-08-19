"""Tests for the shared embedding-text builder (embed_text.py)."""

from __future__ import annotations

import json
import logging

import pytest

from markdown_vault_mcp.embed_text import (
    EmbedTextBuilder,
    fields_text,
    is_embeddable,
)

# ---------------------------------------------------------------------------
# fields_text (module-level, shared with the FTS summary column)
# ---------------------------------------------------------------------------


class TestFieldsText:
    def test_joins_scalar_values_in_field_order(self) -> None:
        fm = {"summary": "An overview.", "type": "decision", "rank": 3}
        assert (
            fields_text(fm, ("summary", "type", "rank")) == "An overview.\ndecision\n3"
        )

    def test_skips_missing_none_and_non_scalar_values(self) -> None:
        fm = {
            "summary": "kept",
            "tags": ["a", "b"],
            "meta": {"nested": True},
            "empty": None,
        }
        assert fields_text(fm, ("summary", "tags", "meta", "empty", "absent")) == "kept"

    def test_accepts_frontmatter_json_string(self) -> None:
        raw = json.dumps({"summary": "from json"})
        assert fields_text(raw, ("summary",)) == "from json"

    def test_invalid_json_and_non_dict_json_return_empty(self) -> None:
        assert fields_text("{not json", ("summary",)) == ""
        assert fields_text(json.dumps(["a", "b"]), ("summary",)) == ""

    def test_no_fields_or_no_frontmatter_return_empty(self) -> None:
        assert fields_text({"summary": "x"}, ()) == ""
        assert fields_text(None, ("summary",)) == ""
        assert fields_text({}, ("summary",)) == ""

    def test_non_scalar_field_is_skipped_with_a_debug_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A configured field holding a list/dict contributes nothing, but the
        skip is logged so an operator can diagnose it (not a silent no-op)."""
        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp.embed_text"):
            result = fields_text(
                {"tags": ["a", "b"], "summary": "kept"}, ("tags", "summary")
            )
        assert result == "kept"
        assert any(
            "skipping non-scalar" in r.getMessage() and "key=tags" in r.getMessage()
            for r in caplog.records
        )

    def test_date_time_values_are_iso_coerced_and_stay_searchable(self) -> None:
        """YAML parses date-like scalars to datetime types; they must be kept.

        Dropping them (they are not str/int/float/bool) would make a
        configured ``SEARCHABLE_FIELDS=date`` silently index nothing.
        """
        import datetime

        fm = {
            "date": datetime.date(2024, 1, 15),
            "created": datetime.datetime(2024, 1, 15, 15, 30, 0),
            "at": datetime.time(9, 5),
        }
        assert (
            fields_text(fm, ("date", "created", "at"))
            == "2024-01-15\n2024-01-15T15:30:00\n09:05:00"
        )

    def test_live_dict_and_frontmatter_json_paths_agree_on_dates(self) -> None:
        """The build path (live dict) and convergence path (frontmatter_json)
        must produce identical text for date fields, or every date-bearing note
        re-embeds on the first boot. _json_default ISO-stringifies dates for the
        stored JSON; fields_text must match that on the live dict."""
        import datetime

        from markdown_vault_mcp.fts_index import _json_default

        fm = {"date": datetime.date(2024, 1, 15), "name": "Alpha"}
        live = fields_text(fm, ("date", "name"))
        via_json = fields_text(json.dumps(fm, default=_json_default), ("date", "name"))
        assert live == via_json == "2024-01-15\nAlpha"


# ---------------------------------------------------------------------------
# format_token canonicalisation
# ---------------------------------------------------------------------------


class TestFormatToken:
    def test_default_builder_is_v1(self) -> None:
        assert EmbedTextBuilder().format_token() == "v1"

    def test_embed_context_alone_is_v2_with_empty_fields(self) -> None:
        builder = EmbedTextBuilder(embed_context=True)
        assert builder.format_token() == "v2;fields="

    def test_fields_alone_is_v2(self) -> None:
        builder = EmbedTextBuilder(searchable_fields=("summary", "type"))
        assert builder.format_token() == "v2;fields=summary,type"

    def test_field_order_is_canonical_not_sorted(self) -> None:
        a = EmbedTextBuilder(searchable_fields=("b", "a"))
        b = EmbedTextBuilder(searchable_fields=("a", "b"))
        assert a.format_token() != b.format_token()


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


class TestBuildV1:
    def test_v1_returns_content_byte_identical(self) -> None:
        builder = EmbedTextBuilder()
        content = "raw chunk content\nwith lines\n"
        out = builder.build(
            title="Title",
            heading="Heading",
            content=content,
            fields_text="ignored",
            is_first_chunk=True,
        )
        assert out is content or out == content
        assert out == content


class TestBuildV2:
    def test_first_chunk_includes_title_heading_and_fields(self) -> None:
        builder = EmbedTextBuilder(embed_context=True, searchable_fields=("summary",))
        out = builder.build(
            title="My Note",
            heading="Intro",
            content="body text",
            fields_text="A summary.",
            is_first_chunk=True,
        )
        assert out == "My Note\nIntro\nA summary.\n\nbody text"

    def test_first_chunk_without_heading_omits_heading_line(self) -> None:
        builder = EmbedTextBuilder(embed_context=True)
        out = builder.build(
            title="My Note",
            heading=None,
            content="body text",
            fields_text="A summary.",
            is_first_chunk=True,
        )
        assert out == "My Note\nA summary.\n\nbody text"

    def test_later_chunk_omits_fields_text(self) -> None:
        builder = EmbedTextBuilder(embed_context=True, searchable_fields=("summary",))
        out = builder.build(
            title="My Note",
            heading="Details",
            content="body text",
            fields_text="A summary.",
            is_first_chunk=False,
        )
        assert out == "My Note\nDetails\n\nbody text"

    def test_later_chunk_without_heading(self) -> None:
        builder = EmbedTextBuilder(embed_context=True)
        out = builder.build(
            title="My Note",
            heading="",
            content="body text",
            fields_text="",
            is_first_chunk=False,
        )
        assert out == "My Note\n\nbody text"

    def test_empty_fields_text_omitted_on_first_chunk(self) -> None:
        builder = EmbedTextBuilder(embed_context=True)
        out = builder.build(
            title="T",
            heading=None,
            content="c",
            fields_text="",
            is_first_chunk=True,
        )
        assert out == "T\n\nc"


class TestIsEmbeddable:
    """`is_embeddable` gates what may reach an embedding provider (#1087)."""

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "\n", "\t\n  \n", "\u00a0"],
    )
    def test_blank_text_is_not_embeddable(self, text: str) -> None:
        assert is_embeddable(text) is False

    @pytest.mark.parametrize(
        "text",
        ["a", "# Title", "  body  ", "0", "\n\ncontent\n\n"],
    )
    def test_text_with_content_is_embeddable(self, text: str) -> None:
        assert is_embeddable(text) is True

    def test_v1_build_of_a_body_less_note_is_rejected(self) -> None:
        """The exact shape that produced the reported HTTP 400 (#1087)."""
        builder = EmbedTextBuilder()
        text = builder.build(
            title="Empty", heading=None, content="", fields_text="", is_first_chunk=True
        )
        assert text == ""
        assert is_embeddable(text) is False

    def test_v2_build_of_a_body_less_note_still_embeds(self) -> None:
        """Enrichment gives a body-less note real text, so it is kept."""
        builder = EmbedTextBuilder(embed_context=True, searchable_fields=("summary",))
        text = builder.build(
            title="Empty",
            heading=None,
            content="",
            fields_text="A summary.",
            is_first_chunk=True,
        )
        assert is_embeddable(text) is True
