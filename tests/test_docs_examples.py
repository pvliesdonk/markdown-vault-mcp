"""Published documentation examples do what their text says (#1660-#1663).

A reader copies these blocks as written, so each is checked against its claim:
the Python API Quick Starts must run against a real vault and return what they
print. Configuration examples carry a ``.config`` tag instead, which
``tests/test_published_examples.py`` checks.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

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


@pytest.fixture
def example_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "Journal").mkdir(parents=True)
    (vault / "Journal" / "note.md").write_text(
        "# Note\n\nSome query text to find, linking [[other]].\n",
        encoding="utf-8",
    )
    (vault / "other.md").write_text("# Other\n\nLinks back.\n", encoding="utf-8")
    return tmp_path


def _quick_start(page: str) -> str:
    return _blocks(_DOCS / page, "python")[0][1]


@pytest.mark.parametrize("page", ["api/vault.md", "api/facets.md"])
def test_api_quick_start_runs_as_written(page: str, example_vault: Path) -> None:
    """The first Python block on each API page runs and finds the note.

    Only the placeholder paths change; everything else is the published text.
    """
    code = _quick_start(page)
    code = code.replace("/path/to/vault", str(example_vault / "vault"))
    code = code.replace("/path/to/index.db", str(example_vault / "index.db"))
    namespace: dict[str, object] = {}
    exec(compile(code, page, "exec"), namespace)
    results = namespace["results"]
    assert isinstance(results, list)
    assert results, f"{page}: the Quick Start search found nothing"


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

    documented = json.loads(
        _section_blocks(_DOCS / "tools" / "index.md", "`read`", "json")[0]
    )
    assert set(documented) == set(live), (
        f"docs/tools/index.md read example keys {sorted(documented)} "
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

    text = (_DOCS / "tools" / "index.md").read_text(encoding="utf-8")
    section = text.split("### `get_index_status`", 1)[1].split("\n### ", 1)[0]
    documented = set(re.findall(r"^- `(\w+)`:", section, flags=re.MULTILINE))
    missing = sorted(set(live) - documented)
    assert not missing, f"get_index_status keys not in its Returns list: {missing}"
