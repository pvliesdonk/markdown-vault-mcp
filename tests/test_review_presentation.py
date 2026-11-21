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
from markdown_vault_mcp.okf import derive_annotation

_NOTICE = "Applicability to current content: unknown."
_APP_NOTICE = (
    "Supplied verified field. Review applicability to current content: unknown."
)


_NO_REVIEW = {"state": "none", "basis": "no_review_supplied"}
_MALFORMED = {"state": "unknown", "basis": "malformed_review"}
_RECORDED = {"state": "unknown", "basis": "no_snapshot_binding"}


@pytest.mark.parametrize("tier", ["human-reviewed", "machine-confirmed", "unverified"])
def test_read_text_labels_recorded_review_without_changing_content(
    capsys: pytest.CaptureFixture[str], tier: str
) -> None:
    data = {
        "path": "note.md",
        "content": "# Changed content\nUnreviewed replacement.",
        "okf": {"trust_tier": tier, "review_applicability": _RECORDED},
    }
    render_reply("read", RemoteReply(data), "text")
    output = capsys.readouterr()
    assert output.out == data["content"] + "\n"
    assert output.err == f"Recorded review metadata: {tier}. {_NOTICE}\n"


@pytest.mark.parametrize("tier", ["human-reviewed", "machine-confirmed", "unverified"])
def test_search_text_labels_recorded_review(
    capsys: pytest.CaptureFixture[str], tier: str
) -> None:
    okf = {"trust_tier": tier, "review_applicability": _RECORDED}
    data = [{"path": "note.md", "score": 1.0, "okf": okf}]
    render_reply("search", RemoteReply(data), "text")
    output = capsys.readouterr()
    assert output.out == (
        f"  note.md (1.0000)\n    Recorded review metadata: {tier}. {_NOTICE}\n"
    )
    assert not output.err


@pytest.mark.parametrize(
    ("tier", "applicability"),
    [
        ("unverified", _MALFORMED),
        ("human-reviewed", _RECORDED),
        ("unverified", _RECORDED),
        # A payload that cannot rule a review out keeps the notice.
        ("unverified", None),
        ("unverified", {}),
        ("unverified", {"state": "other"}),
    ],
)
def test_supplied_review_keeps_notice(
    capsys: pytest.CaptureFixture[str], tier: str, applicability: Any
) -> None:
    okf: dict[str, Any] = {"trust_tier": tier}
    if applicability is not None:
        okf["review_applicability"] = applicability
    data = {"path": "note.md", "content": "body", "score": 1.0, "okf": okf}
    notice = f"Recorded review metadata: {tier}. {_NOTICE}"
    render_reply("read", RemoteReply(data), "text")
    assert capsys.readouterr().err == notice + "\n"
    render_reply("search", RemoteReply([data]), "text")
    assert capsys.readouterr().out == f"  note.md (1.0000)\n    {notice}\n"


@pytest.mark.parametrize(
    "annotation",
    [
        None,
        [],
        {},
        {"trust_tier": None},
        {"trust_tier": ""},
        {"trust_tier": "unverified", "review_applicability": _NO_REVIEW},
    ],
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


@pytest.mark.parametrize(
    ("metadata", "notice"),
    [
        ({}, False),
        ({"verified": []}, False),
        ({"verified": "yes"}, True),
        ({"verified": [{"by": "human:a"}]}, True),
        ({"verified": [{"by": "human:a", "at": "2026-01-01"}]}, True),
    ],
)
def test_server_annotation_decides_read_notice(
    capsys: pytest.CaptureFixture[str], metadata: dict[str, Any], notice: bool
) -> None:
    okf = derive_annotation(metadata, include_sources=True)
    render_reply(
        "read", RemoteReply({"path": "n.md", "content": "b", "okf": okf}), "text"
    )
    assert (_NOTICE in capsys.readouterr().err) is notice


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


_APP_INPUTS: list[dict[str, Any]] = [
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
    {"verified": True},
]


def _render_app_view(view: str, cases: list[tuple[dict[str, Any], Any]]) -> list[str]:
    """Run a view's rendering helpers under node over ``(frontmatter, okf)``."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the SPA rendering functions")
    root = Path(__file__).resolve().parents[1]
    source = (root / f"src/markdown_vault_mcp/static/spa/views/{view}.js").read_text()
    names = (
        ["escHtml", "propEntries", "showsReviewNotice", "renderProps"]
        if view == "note"
        else ["escapeHtml", "showsReviewNotice", "renderChips", "renderFrontmatter"]
    )
    functions = "\n".join(_view_function(source, name) for name in names)
    render = (
        "renderProps(fm, okf)"
        if view == "note"
        else "renderChips(fm, okf) + renderFrontmatter(fm)"
    )
    script = (
        "const PROPS_SHOWN = 3; const CHEVRON = '';\n"
        + functions
        + f"\nconst cases = {json.dumps(cases)};"
        + "\nconst before = JSON.stringify(cases);"
        + "\nconst out = cases.map(([fm, okf]) => "
        + f"{render});"
        + "\nif (JSON.stringify(cases) !== before) throw new Error('mutated input');"
        + "\nconsole.log(JSON.stringify(out));"
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    )
    rendered: list[str] = json.loads(result.stdout)
    return rendered


@pytest.mark.parametrize("view", ["note", "context"])
def test_app_review_notice_follows_server_applicability(view: str) -> None:
    """On an OKF vault, the server's ``review_applicability`` decides the notice."""
    cases = [(fm, derive_annotation(fm)) for fm in _APP_INPUTS]
    rendered = _render_app_view(view, cases)
    for (fm, okf), html in zip(cases, rendered, strict=True):
        expected = okf["review_applicability"]["state"] != "none"
        assert (_APP_NOTICE in html) is expected, fm
        assert "human-reviewed" not in html
    # Absent and empty ``verified`` carry no review, as in the CLI (#2385).
    assert _APP_NOTICE not in rendered[1]
    assert _APP_NOTICE not in rendered[3]
    assert "human:&lt;reviewer&gt;" in rendered[0]
    assert "2099-01-01" in rendered[0]
    assert "&lt;malformed&gt;" in rendered[2]
    assert "not a date" in rendered[5]
    if view == "note":
        # The warning sits before the collapsed property container, even when
        # the verified field itself is beyond the three initially shown rows.
        assert rendered[0].index(_APP_NOTICE) < rendered[0].index('id="preview-props"')


@pytest.mark.parametrize("view", ["note", "context"])
@pytest.mark.parametrize("okf", [None, [], "okf"])
def test_app_review_notice_absent_without_okf(view: str, okf: Any) -> None:
    """A vault without OKF sends no ``okf`` key, so a ``verified`` field stays plain."""
    rendered = _render_app_view(view, [(fm, okf) for fm in _APP_INPUTS])
    assert all(_APP_NOTICE not in html for html in rendered)
    assert "human:&lt;reviewer&gt;" in rendered[0]
    assert "&lt;malformed&gt;" in rendered[2]


@pytest.mark.parametrize("view", ["note", "context"])
@pytest.mark.parametrize(
    "applicability",
    [None, {}, {"state": "other"}, {"state": "unknown"}, "none"],
)
def test_app_review_notice_kept_when_payload_cannot_rule_review_out(
    view: str, applicability: Any
) -> None:
    """Like the CLI, only ``state == "none"`` drops the notice."""
    okf: dict[str, Any] = {"trust_tier": "unverified"}
    if applicability is not None:
        okf["review_applicability"] = applicability
    cases = [({"verified": "x"}, okf), ({"title": "T"}, okf)]
    rendered = _render_app_view(view, cases)
    assert _APP_NOTICE in rendered[0]
    # Without a supplied field there is nothing for the notice to describe.
    assert "T" in rendered[1]
    assert _APP_NOTICE not in rendered[1]
