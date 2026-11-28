"""Serve the bundled agent skills over MCP (SEP-2640).

Every folder under ``skills/`` in this package that holds a ``SKILL.md`` is
served as ``skill://<name>/SKILL.md`` through FastMCP's
``SkillsDirectoryProvider``, which also registers the skills extension
(``skills/list``, ``skills/get``). A host that implements the extension shows
the model each skill's name and description and loads the body on demand;
other clients see the files as ordinary resources.

The folders ship inside the package, so the wheel and the Docker image carry
them. What Claude does with served skills, and its limits on them, is in
``docs/design/reference/claude-mcp-client.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).parent / "skills"
"""The folder whose skill subfolders this server serves."""


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
