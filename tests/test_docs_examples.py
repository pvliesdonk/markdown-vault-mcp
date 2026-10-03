"""Published reference examples match what the tools return (#1662, #1663).

The `read` worked example on the generated reader page names every key the
tool returns, and `get_index_status`'s Returns list names every key it
returns. Runnable Python examples and configuration examples carry `.run`
and `.config` tags instead, which `tests/test_published_examples.py` checks.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _REPO_ROOT / "docs"

# A backtick fence's info string holds no backtick (CommonMark 4.5), so a line
# opening with an inline ```code``` span is prose, not a fence.
_FENCE = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,})(?P<lang>\w*)[^`\n]*$")
_HEADING = re.compile(r"^(?P<hashes>#{1,6}) (?P<title>.+)$")


def _blocks(page: Path, lang: str) -> list[tuple[list[str], str]]:
    """Return ``(heading trail, code)`` for every fenced block in *lang*."""
    trail: list[tuple[int, str]] = []
    blocks: list[tuple[list[str], str]] = []
    lines = page.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        heading = _HEADING.match(lines[i])
        if heading:
            level = len(heading["hashes"])
            trail = [(lv, t) for lv, t in trail if lv < level]
            trail.append((level, heading["title"]))
        fence = _FENCE.match(lines[i])
        if fence:
            body: list[str] = []
            i += 1
            while lines[i].strip() != fence["ticks"]:
                body.append(lines[i])
                i += 1
            if fence["lang"] == lang:
                code = textwrap.dedent("\n".join(body))
                blocks.append(([t for _, t in trail], code))
        i += 1
    return blocks


def _section_blocks(page: Path, heading: str, lang: str) -> list[str]:
    """Code blocks in *lang* under the first heading equal to *heading*."""
    return [code for trail, code in _blocks(page, lang) if heading in trail]


async def test_read_return_example_lists_the_live_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented ``read`` return shape names every key the tool returns (#1662).

    The write tools take the etag ``read`` returns as ``if_match``, so an
    example that drops a key sends the reader looking for it.
    """
    from fastmcp import Client

    from tests.conftest import _CLEAR_VARS
    from tests.server_factory import make_server

    vault = tmp_path / "vault"
    (vault / "Journal").mkdir(parents=True)
    (vault / "Journal" / "note.md").write_text(
        "---\ntitle: My Note\ntags: [journal]\n---\n\nThe note body.\n",
        encoding="utf-8",
    )
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")

    async with Client(make_server()) as client:
        result = await client.call_tool("read", {"path": "Journal/note.md"})
    live = result.structured_content
    assert isinstance(live, dict)

    page = _DOCS / "reference" / "tools" / "reader.md"
    response = next(
        block for block in _section_blocks(page, "`read`", "json") if '"etag"' in block
    )
    documented = json.loads(response)
    assert set(documented) == set(live), (
        f"{page.name} read example keys {sorted(documented)} "
        f"!= live keys {sorted(live)}"
    )


async def test_index_status_returns_list_documents_every_live_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_index_status``'s Returns list names every key the tool returns (#1663).

    Other sections tell the reader to watch ``dirty_embeddings`` and the writer
    fields, so the list they look them up in has to carry them.
    """
    from fastmcp import Client

    from tests.conftest import _CLEAR_VARS
    from tests.server_factory import make_server

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")

    async with Client(make_server()) as client:
        result = await client.call_tool("get_index_status", {})
    live = result.structured_content
    assert isinstance(live, dict)

    text = (_DOCS / "reference" / "tools" / "indexing.md").read_text(encoding="utf-8")
    section = text.split("## `get_index_status`", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\s*- `?(\w+)`? \(", section, flags=re.MULTILINE))
    missing = sorted(set(live) - documented)
    assert not missing, f"get_index_status keys not in its Returns list: {missing}"
