#!/usr/bin/env python3
"""Derive the GitHub "About" block (description, website, topics) from pyproject.

``.github/workflows/bootstrap.yml`` runs this and sends the result to the
GitHub API, so a repository's About block states what ``pyproject.toml``
already says instead of staying empty until someone fills it in by hand:

- description: ``[project] description`` (the copier ``domain_description``);
- website: ``[project.urls] Documentation`` (the GitHub Pages docs site);
- topics: the fixed discovery topics below, then ``[project] keywords`` —
  the template's own terms plus whatever the project added in its
  ``PROJECT-KEYWORDS`` seam — normalised to GitHub's topic rules.

Prints one JSON object, ``{"description", "homepage", "names"}``.  A field
pyproject does not carry is left out, so the PATCH leaves it untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

# GitHub rejects the whole topics PUT (422) if any one name breaks these.
MAX_TOPICS = 20
MAX_TOPIC_LENGTH = 50

# `mcp-server` is the topic MCP servers are browsed by on GitHub; the keywords
# carry `mcp` and `model-context-protocol` but not this one.
FIXED_TOPICS = ("mcp-server",)


def normalise_topic(keyword: str) -> str:
    """Turn a keyword into a GitHub topic, or ``""`` if nothing usable is left.

    Topics are lowercase letters, digits and hyphens, start with a letter or
    digit, and are at most 50 characters long.
    """
    topic = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    return topic[:MAX_TOPIC_LENGTH].rstrip("-")


def topics(keywords: list[str]) -> list[str]:
    """Fixed topics first, then keywords in order: normalised, deduplicated, capped."""
    names: list[str] = []
    for keyword in (*FIXED_TOPICS, *keywords):
        topic = normalise_topic(keyword)
        if topic and topic not in names:
            names.append(topic)
    return names[:MAX_TOPICS]


def about(pyproject: dict[str, object]) -> dict[str, object]:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        project = {}
    result: dict[str, object] = {}
    description = project.get("description")
    if isinstance(description, str) and description.strip():
        result["description"] = description.strip()
    urls = project.get("urls", {})
    homepage = urls.get("Documentation") if isinstance(urls, dict) else None
    if isinstance(homepage, str) and homepage.strip():
        result["homepage"] = homepage.strip()
    keywords = project.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    result["names"] = topics([k for k in keywords if isinstance(k, str)])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the GitHub About block pyproject.toml implies, as JSON."
    )
    parser.add_argument(
        "pyproject",
        nargs="?",
        type=Path,
        default=Path("pyproject.toml"),
        help="path to pyproject.toml (default: ./pyproject.toml)",
    )
    args = parser.parse_args()
    with args.pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    json.dump(about(data), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
