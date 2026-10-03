"""MkDocs hook: build the llms.txt sections from `nav:` (#714).

Registered under `hooks:` in mkdocs.yml.  The `llmstxt` plugin reads its
`sections` option when files are collected, after every `on_config` has run,
so filling that option here means `llms.txt` is derived from the navigation
and never kept as a second list.

- Each top-level nav entry is a section: a titled group lists its pages in
  nav order, nesting flattened; a titled single page is a one-page section.
- A published page that is not in the nav joins the section of a nav page
  in the same directory (per-minor release notes join the landing page's
  section). When several sections list pages in that directory, the first
  one in nav order takes it.
- Each page's description is its `description:` front matter, or empty; a
  front-matter block that is not valid YAML is logged as a warning, which
  `mkdocs build --strict` turns into a failure.

External links are skipped, and so are pages outside the nav that
`exclude_docs` matches (a nav entry for an excluded page is an error mkdocs
reports itself).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

Sections = dict[str, list[dict[str, str]]]

logger = logging.getLogger("mkdocs.hooks.llmstxt_sections")
_FRONT_MATTER = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)
_HEADING = re.compile(r"^#[ \t]+(\S.*)$", re.MULTILINE)  # the caller strips the title


def _pages(node: Any) -> list[str]:
    """Return the page paths under a nav node, in order."""
    if isinstance(node, str):
        return [] if "://" in node else [node]
    if isinstance(node, list):
        return [page for item in node for page in _pages(item)]
    if isinstance(node, dict):
        return [page for value in node.values() for page in _pages(value)]
    return []


def _read(docs_dir: Path, rel: str) -> str:
    try:
        return (docs_dir / rel).read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _description(docs_dir: Path, rel: str) -> str:
    """Return the page's `description:` front matter, or an empty string."""
    match = _FRONT_MATTER.match(_read(docs_dir, rel))
    if match is None:
        return ""
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        logger.warning("llmstxt_front_matter_unparseable path=%s", rel)
        return ""
    if not isinstance(front, dict):
        return ""
    value = front.get("description", "")
    return value if isinstance(value, str) else ""


def _title(docs_dir: Path, rel: str) -> str:
    """Return a bare nav entry's title: its first `# ` heading, else its path."""
    heading = _HEADING.search(_read(docs_dir, rel))
    return heading.group(1).rstrip() if heading else rel


def _nav_sections(nav: list[Any], docs_dir: Path) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for entry in nav:
        if isinstance(entry, dict):
            title, value = next(iter(entry.items()))
        else:
            title, value = _title(docs_dir, str(entry)), entry
        pages = _pages(value)
        if pages:
            sections.setdefault(str(title), []).extend(pages)
    return sections


def sections_from_nav(
    nav: list[Any], docs_dir: Path, excluded: Callable[[str], bool]
) -> Sections:
    """Return llms.txt sections derived from *nav* and the files in *docs_dir*."""
    by_section = _nav_sections(nav, docs_dir)
    listed = {page for pages in by_section.values() for page in pages}
    home: dict[str, str] = {}
    for title, pages in by_section.items():
        for page in pages:
            home.setdefault(Path(page).parent.as_posix(), title)
    for path in sorted(docs_dir.rglob("*.md")):
        rel = path.relative_to(docs_dir).as_posix()
        if rel in listed or excluded(rel):
            continue
        section = home.get(Path(rel).parent.as_posix())
        if section is not None:
            by_section[section].append(rel)
    return {
        title: [{page: _description(docs_dir, page)} for page in pages]
        for title, pages in by_section.items()
    }


def on_config(config: Any) -> Any:
    """Fill the llmstxt plugin's `sections` from the nav."""
    plugin = config["plugins"].get("llmstxt")
    if plugin is None or not config["nav"]:
        return config
    spec = config["exclude_docs"]

    def excluded(rel: str) -> bool:
        return spec is not None and bool(spec.match_file(rel))

    plugin.config["sections"] = sections_from_nav(
        config["nav"], Path(config["docs_dir"]), excluded
    )
    return config
