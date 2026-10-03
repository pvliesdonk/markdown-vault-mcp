"""Published pages that link into this repository on GitHub link to something.

``mkdocs build --strict`` and ``scripts/check_docs_structure.py`` check links
between site pages. A link to a repository file the site doesn't serve (an
example folder, the README's fit section) is an absolute GitHub URL, and
nothing else notices when that file moves or its heading changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_UNPUBLISHED = ("design", "decisions", "superpowers")
_REPO_URL = "https://github.com/pvliesdonk/markdown-vault-mcp"
# A link to the repository root (the README) or to a path on ``main``; a link
# pinned to a commit names content that can't change, so it is left alone.
_LINK = re.compile(
    re.escape(_REPO_URL)
    + r"(?:/(?:tree|blob)/main/(?P<path>[^)#\s\"'>]*))?(?:#(?P<anchor>[\w-]+))?"
    + r"(?=[)\s\"'>]|$)"
)
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*\s*$", re.MULTILINE)


def _pages() -> list[Path]:
    docs = _REPO_ROOT / "docs"
    published = [
        p
        for p in docs.rglob("*.md")
        if p.relative_to(docs).parts[0] not in _UNPUBLISHED
    ]
    return [_REPO_ROOT / "README.md", *sorted(published)]


def _slug(title: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to dashes."""
    title = re.sub(r"`([^`]*)`", r"\1", title).strip().lower()
    return re.sub(r"[^\w\- ]", "", title).replace(" ", "-")


def _links() -> list[tuple[str, str, str | None]]:
    found = []
    for page in _pages():
        rel = page.relative_to(_REPO_ROOT).as_posix()
        for match in _LINK.finditer(page.read_text(encoding="utf-8")):
            path, anchor = match["path"], match["anchor"]
            if path is None and anchor is None:
                continue
            target = (path or "README.md").rstrip("/")
            found.append((rel, target, anchor))
    return found


def test_links_are_found() -> None:
    """Guard the scanner: the Overview links to the README's fit section."""
    assert ("docs/index.md", "README.md", "does-it-fit") in _links()


@pytest.mark.parametrize(
    ("page", "target", "anchor"),
    _links(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_repository_link_resolves(page: str, target: str, anchor: str | None) -> None:
    """The linked path exists on this tree, and an anchor names one of its headings."""
    path = _REPO_ROOT / target
    assert path.exists(), f"{page}: links to {target}, which does not exist"
    if anchor is None or path.suffix != ".md":
        return
    anchors = {_slug(m["title"]) for m in _HEADING.finditer(path.read_text("utf-8"))}
    assert anchor in anchors, f"{page}: {target} has no heading for #{anchor}"
