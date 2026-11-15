"""Recorded review metadata is visible without current-content certification."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp._cli_output import render_reply
from markdown_vault_mcp._cli_remote import RemoteReply

_NOTICE = "Applicability to current content: unknown."


@pytest.mark.parametrize("tier", ["human-reviewed", "machine-confirmed", "unverified"])
def test_read_text_labels_recorded_review_without_changing_content(
    capsys: pytest.CaptureFixture[str], tier: str
) -> None:
    data = {
        "path": "note.md",
        "content": "# Changed content\nUnreviewed replacement.",
        "okf": {"trust_tier": tier},
    }
    render_reply("read", RemoteReply(data), "text")
    output = capsys.readouterr()
    assert output.out == data["content"] + "\n"
    assert output.err == f"Recorded review metadata: {tier}. {_NOTICE}\n"


@pytest.mark.parametrize("tier", ["human-reviewed", "machine-confirmed", "unverified"])
def test_search_text_labels_recorded_review(
    capsys: pytest.CaptureFixture[str], tier: str
) -> None:
    data = [{"path": "note.md", "score": 1.0, "okf": {"trust_tier": tier}}]
    render_reply("search", RemoteReply(data), "text")
    output = capsys.readouterr()
    assert output.out == (
        f"  note.md (1.0000)\n    Recorded review metadata: {tier}. {_NOTICE}\n"
    )
    assert not output.err


@pytest.mark.parametrize(
    "annotation", [None, [], {}, {"trust_tier": None}, {"trust_tier": ""}]
)
def test_absent_review_classification_keeps_text_unchanged(
    capsys: pytest.CaptureFixture[str], annotation: Any
) -> None:
    data = {"path": "note.md", "content": "body", "score": 1.0, "okf": annotation}
    render_reply("read", RemoteReply(data), "text")
    output = capsys.readouterr()
    assert output.out == "body\n"
    assert not output.err
    render_reply("search", RemoteReply([data]), "text")
    output = capsys.readouterr()
    assert output.out == "  note.md (1.0000)\n"
    assert not output.err


def test_review_label_escapes_terminal_controls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = {"path": "note.md", "score": 1.0, "okf": {"trust_tier": "claim\n\x1b[31m"}}
    render_reply("search", RemoteReply([data]), "text")
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 2
    assert r"claim\n\x1b[31m" in output
    assert "\x1b" not in output


@pytest.mark.parametrize("command", ["read", "search"])
@pytest.mark.parametrize("output_mode", ["json", "agent"])
def test_structured_output_preserves_existing_schema(
    capsys: pytest.CaptureFixture[str], command: str, output_mode: Any
) -> None:
    note = {
        "path": "note.md",
        "content": "body",
        "score": 1.0,
        "okf": {"trust_tier": "human-reviewed"},
        "frontmatter": {"verified": [{"by": "human:someone", "at": "malformed"}]},
    }
    data = [note] if command == "search" else note
    render_reply(command, RemoteReply(data), output_mode)
    output = capsys.readouterr()
    assert not output.err
    if output_mode == "agent":
        assert json.loads(output.out.splitlines()[1]) == note
    else:
        assert json.loads(output.out) == data
    assert _NOTICE not in output.out


def _view_function(source: str, name: str) -> str:
    match = re.search(
        rf"^  function {name}\(.*?^  }}", source, re.MULTILINE | re.DOTALL
    )
    assert match is not None, name
    return match.group()


@pytest.mark.parametrize("view", ["note", "context"])
def test_app_review_notice_is_visible_and_preserves_supplied_metadata(
    view: str,
) -> None:
    """Execute the actual render functions, including repeated navigation inputs."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the SPA rendering functions")
    root = Path(__file__).resolve().parents[1]
    source = (root / f"src/markdown_vault_mcp/static/spa/views/{view}.js").read_text()
    names = (
        ["escHtml", "propEntries", "renderProps"]
        if view == "note"
        else ["escapeHtml", "renderChips", "renderFrontmatter"]
    )
    functions = "\n".join(_view_function(source, name) for name in names)
    inputs = [
        {
            "type": "Reference",
            "title": "T",
            "status": "stable",
            "verified": [{"by": "human:<reviewer>", "at": "2099-01-01"}],
        },
        {},
        {"verified": "<malformed>"},
        {"verified": []},
        {"verified": None},
        {"verified": {"by": "process:check", "at": "not a date"}},
    ]
    render = (
        "renderProps(fm)"
        if view == "note"
        else "renderChips(fm) + renderFrontmatter(fm)"
    )
    script = (
        "const PROPS_SHOWN = 3; const CHEVRON = '';\n"
        + functions
        + f"\nconst inputs = {json.dumps(inputs)};"
        + f"\nconsole.log(JSON.stringify([inputs.map(fm => {render}), inputs]));"
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    )
    rendered, after = json.loads(result.stdout)
    assert after == inputs
    for index, html in enumerate(rendered):
        if index == 1:
            assert _NOTICE not in html
            continue
        assert "Recorded review metadata (verified)." in html
        assert _NOTICE in html
        assert "human-reviewed" not in html
    assert "human:&lt;reviewer&gt;" in rendered[0]
    assert "2099-01-01" in rendered[0]
    assert "&lt;malformed&gt;" in rendered[2]
    assert "not a date" in rendered[5]
    if view == "note":
        # The warning sits before the collapsed property container, even when
        # the verified field itself is beyond the three initially shown rows.
        assert rendered[0].index(_NOTICE) < rendered[0].index('id="preview-props"')
        assert "html += renderProps(data.frontmatter)" in source
    else:
        assert (
            "getElementById('ctx-chips').innerHTML = renderChips(data.frontmatter)"
            in source
        )
