"""Serve the bundled agent skills over MCP (SEP-2640).

Every folder under ``skills/`` in this package that holds a ``SKILL.md`` is
served as ``skill://<name>/SKILL.md`` through FastMCP's
``SkillsDirectoryProvider``, which also registers the skills extension
(``skills/list``, ``skills/get``). A host that implements the extension shows
the model each skill's name and description and loads the body on demand;
other clients see the files as ordinary resources.

A skill that belongs to a feature is listed in :data:`SKILL_TAGS` under that
feature's tag, and :func:`disable_skills` hides it wherever the server hides
the feature's tools: ``okf-spec`` goes with the ``okf`` tools.

The folders ship inside the package, so the wheel and the Docker image carry
them. What Claude does with served skills, and its limits on them, is in
``docs/design/reference/claude-mcp-client.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Mapping, Set

    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).parent / "skills"
"""The folder whose skill subfolders this server serves."""

SKILL_TAGS: Mapping[str, frozenset[str]] = {"okf-spec": frozenset({"okf"})}
"""The feature tags of each skill that belongs to a feature, by skill name."""


def skill_file_uris(name: str) -> frozenset[str]:
    """Return every URI the skill *name* is served under.

    Args:
        name: A skill folder under :data:`SKILLS_ROOT`.

    Returns:
        The ``skill://`` URI of each of its files, and of its ``_manifest``.
    """
    from fastmcp.skills import Skill

    files = Skill.from_directory(SKILLS_ROOT / name).files
    # Quoted as the provider quotes the URIs it serves.
    return frozenset(
        f"skill://{quote(name, safe='/')}/{quote(path, safe='/')}"
        for path in [*files, "_manifest"]
    )


def disable_skills(mcp: FastMCP, tags: Set[str]) -> None:
    """Hide every skill whose feature tags meet *tags*.

    The skills provider serves its files untagged, and the skills extension
    refuses a transformed provider, so ``mcp.disable(tags=...)`` cannot reach
    them. Hiding a skill's files by URI does: ``skills/list`` lists a skill
    only when every one of its files is readable.

    Args:
        mcp: The server under assembly.
        tags: The tags of the features being hidden.
    """
    uris: set[str] = set()
    for name, skill_tags in SKILL_TAGS.items():
        if skill_tags & tags:
            uris |= skill_file_uris(name)
    if uris:
        mcp.disable(names=uris, components={"resource"})
        logger.debug("skills_disabled count=%s tags=%s", len(uris), sorted(tags))


def register_skills(mcp: FastMCP) -> None:
    """Serve every skill folder under :data:`SKILLS_ROOT` on *mcp*.

    Args:
        mcp: The server under assembly.

    Raises:
        ValueError: A folder under :data:`SKILLS_ROOT` is not a valid Agent
            Skill (FastMCP refuses to start rather than serve a partial set).
    """
    from fastmcp.skills.provider import SkillsDirectoryProvider

    mcp.add_provider(SkillsDirectoryProvider(SKILLS_ROOT))
    logger.debug("skills_registered root=%s", SKILLS_ROOT)
