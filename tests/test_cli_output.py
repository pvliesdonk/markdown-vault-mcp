"""Output stays machine-readable without emitting terminal controls."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._cli_remote import RemoteReply

if TYPE_CHECKING:
    import pytest


def test_agent_c1_and_non_bmp_controls_are_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from markdown_vault_mcp._cli_output import render_reply

    path = "文\x7f\x85\U000e0001.md"
    render_reply("search", RemoteReply([{"path": path, "score": 1.0}]), "agent")
    header, record = capsys.readouterr().out.splitlines()
    assert header.startswith("OK search ")
    assert json.loads(record)["path"] == path


def test_untrusted_freshness_cannot_add_header_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from markdown_vault_mcp._cli_output import render_reply

    render_reply("search", RemoteReply([], {"index_stale": "\nBAD\x1b[31m"}), "agent")
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert "index_stale=unknown" in output


def test_search_json_preserves_payload_and_warns_on_stale(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from markdown_vault_mcp._cli_output import render_reply

    data = [{"path": "文.md", "score": 1.0, "okf": {"status": "stable"}}]
    render_reply("search", RemoteReply(data, {"index_stale": True}), "json")
    output = capsys.readouterr()
    assert json.loads(output.out) == data
    assert "stale" in output.err.lower()


def test_agent_header_and_unicode_records(capsys: pytest.CaptureFixture[str]) -> None:
    from markdown_vault_mcp._cli_output import render_reply

    data = [{"path": "文\x1b[31m\n\u202e.md", "score": 1.0, "search_type": "keyword"}]
    render_reply(
        "search",
        RemoteReply(data, {"requested_mode": "auto", "index_stale": False}),
        "agent",
    )
    output = capsys.readouterr()
    header, record = output.out.splitlines()
    assert (
        header
        == "OK search count=1 requested_mode=auto actual_mode=keyword index_stale=false"
    )
    assert json.loads(record) == data[0]
    assert "文" in record
    assert "\x1b" not in output.out and "\u202e" not in output.out
    assert output.err == ""


@given(st.text(st.integers(0, 0x10FFFF).map(chr)))
@settings(max_examples=100, deadline=None)
def test_agent_record_roundtrip(value: str) -> None:
    from unittest.mock import patch

    from markdown_vault_mcp._cli_output import render_reply

    with patch("typer.echo") as echo:
        data = {"path": value, "score": 1.0, "frontmatter": {value: [value]}}
        render_reply("search", RemoteReply([data]), "agent")
    record = echo.call_args_list[-1].args[0]
    assert json.loads(record) == data
    assert len(record.splitlines()) == 1
    assert "\x1b" not in record


@given(
    st.dictionaries(st.text(min_size=1).filter(lambda key: "=" not in key), st.text())
)
@settings(max_examples=100, deadline=None)
def test_filter_roundtrip(values: dict[str, str]) -> None:
    from markdown_vault_mcp._read_payloads import parse_filters

    assert parse_filters([key + "=" + value for key, value in values.items()]) == values


@given(st.text(min_size=1).filter(lambda key: "=" not in key), st.text(), st.text())
@settings(max_examples=100, deadline=None)
def test_duplicate_filters_refused(key: str, first: str, second: str) -> None:
    import pytest

    from markdown_vault_mcp._read_payloads import ReadPayloadError, parse_filters

    with pytest.raises(ReadPayloadError):
        parse_filters([key + "=" + first, key + "=" + second])


@given(st.floats(allow_nan=False, allow_infinity=False))
@settings(max_examples=100, deadline=None)
def test_finite_scores_unchanged(score: float) -> None:
    from markdown_vault_mcp._read_payloads import validate_search

    data = [{"path": "x", "score": score}]
    validate_search(data)
    assert data == [{"path": "x", "score": score}]


def test_broken_pipe_exits_without_traceback() -> None:
    import subprocess
    import sys

    code = (
        "from markdown_vault_mcp._cli_output import render_reply; "
        "from markdown_vault_mcp._cli_remote import RemoteReply; "
        "render_reply('search', RemoteReply([{'path': 'x'*100000, 'score':1.}]), 'agent')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert process.stdout is not None
    process.stdout.close()
    assert process.stderr is not None
    error = process.stderr.read()
    assert process.wait(timeout=30) == 0, error
    assert error == b""


def test_huge_score_is_a_protocol_error() -> None:
    import pytest

    from markdown_vault_mcp._read_payloads import ReadPayloadError, validate_search

    with pytest.raises(ReadPayloadError):
        validate_search([{"path": "x", "score": 10**400}])


def test_payload_helpers_do_not_import_fastmcp() -> None:
    import subprocess
    import sys

    code = """
import sys
from importlib.abc import MetaPathFinder
class Deny(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fastmcp' or fullname.startswith('fastmcp.'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Deny())
from markdown_vault_mcp._read_payloads import parse_filters, annotate_hits
assert parse_filters(['tag=文=a']) == {'tag':'文=a'}
assert annotate_hits([{'frontmatter':{}}], lambda _: {'status':'stable'}) == [{'frontmatter':{},'okf':{'status':'stable'}}]
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_nonfinite_nested_numbers_are_protocol_errors() -> None:
    import pytest

    from markdown_vault_mcp._read_payloads import ReadPayloadError, validate_search

    for invalid in [float("nan"), float("inf"), -float("inf")]:
        with pytest.raises(ReadPayloadError):
            validate_search(
                [{"path": "x", "score": 1.0, "frontmatter": {"value": invalid}}]
            )


def test_invalid_render_payload_fails_before_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import pytest

    from markdown_vault_mcp._cli_output import render_reply
    from markdown_vault_mcp._read_payloads import ReadPayloadError

    for output in ("json", "agent"):
        with pytest.raises(ReadPayloadError) as error:
            render_reply("search", RemoteReply([{"value": float("nan")}]), output)
        assert error.value.code == 1
        assert capsys.readouterr().out == ""


def test_generic_text_reply(capsys: pytest.CaptureFixture[str]) -> None:
    from markdown_vault_mcp._cli_output import render_reply

    render_reply("stats", RemoteReply({"notes": 3}), "text")
    assert json.loads(capsys.readouterr().out) == {"notes": 3}


@given(st.integers(max_value=0))
@settings(max_examples=100, deadline=None)
def test_search_options_refuse_nonpositive_chunk_caps(chunks: int) -> None:
    import pytest

    from markdown_vault_mcp._read_payloads import ReadPayloadError, SearchOptions

    with pytest.raises(ReadPayloadError):
        SearchOptions(chunks_per_file=chunks)


@given(st.integers(max_value=-1))
@settings(max_examples=100, deadline=None)
def test_search_options_refuse_negative_snippets(words: int) -> None:
    import pytest

    from markdown_vault_mcp._read_payloads import ReadPayloadError, SearchOptions

    with pytest.raises(ReadPayloadError):
        SearchOptions(snippet_words=words)
