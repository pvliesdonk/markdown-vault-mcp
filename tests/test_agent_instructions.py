"""Agent-instruction surface contract (template-owned).

AGENTS.md is the single always-loaded instruction file every coding agent
reads; CLAUDE.md is a stub importing it; skills live under .agents/skills/
with .claude/skills/ symlinks for Claude Code.  Claude Code warns above
40 000 characters of always-loaded instructions, and the template owns
~20k of AGENTS.md, so the DOMAIN blocks are the lever when this fails.

Every template-owned skill gets a `.claude/skills/<name>` symlink
into `.agents/skills/<name>`; every other entry under `.claude/skills/` is
project-owned and left alone, except that a symlink there must still resolve
inside `.agents/skills/` (a stray symlink pointing outside the skills tree
would silently defeat the copier-update guard). `TEMPLATE_SKILLS` below must
stay identical to the tuple of the same name in
`scripts/migrate_agent_instructions.py`; `scripts/tests/test_shared_skill_paths.py`
reads this file's tuple back out and asserts the two agree, so a fourth copy
of the skill-name list cannot drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_LIMIT = 40_000
STUB = (
    "@AGENTS.md\n\nProject instructions live in `AGENTS.md` (domain content between its "
    "`DOMAIN-START` / `DOMAIN-END` markers). This file is template-owned; do not add content here.\n"
)
TEMPLATE_SKILLS: tuple[str, ...] = (
    "applying-template-updates",
    "authoring-issues-prs",
    "code-review",
    "config-contract",
    "logging-standard",
    "releasing",
    "repository-protection",
    "tool-registration",
    "writing-release-notes",
)


def test_agents_md_within_always_loaded_budget() -> None:
    size = len((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert size <= AGENTS_LIMIT, (
        f"AGENTS.md is {size} chars; Claude Code degrades above {AGENTS_LIMIT}. "
        "Trim the DOMAIN blocks (move detail into docs/ or a project skill under "
        ".agents/skills/) — the template-owned sections are budgeted separately."
    )


def test_claude_md_is_the_stub() -> None:
    assert (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8") == STUB, (
        "CLAUDE.md is template-owned and must stay the @AGENTS.md stub; put content in AGENTS.md"
    )


def _claude_skills_dir() -> Path:
    claude_skills = REPO_ROOT / ".claude" / "skills"
    assert claude_skills.is_dir(), f"{claude_skills} does not exist"
    return claude_skills


@pytest.mark.parametrize("name", TEMPLATE_SKILLS)
def test_template_skill_link_resolves_into_agents_skills(name: str) -> None:
    entry = _claude_skills_dir() / name
    agents_skills = (REPO_ROOT / ".agents" / "skills").resolve()
    assert entry.exists(), f"{entry} does not exist"
    assert entry.is_symlink(), f"{entry} must be a symlink into .agents/skills/"
    assert entry.resolve().parent == agents_skills, entry
    assert (entry / "SKILL.md").is_file(), f"{entry} does not resolve to a SKILL.md"


def test_other_claude_skills_only_constrained_when_symlinked() -> None:
    claude_skills = _claude_skills_dir()
    agents_skills = (REPO_ROOT / ".agents" / "skills").resolve()
    for entry in claude_skills.iterdir():
        if entry.name in TEMPLATE_SKILLS:
            continue
        # Project-owned real directories are allowed and left untouched; only
        # a symlink is constrained, and only to resolve inside .agents/skills/.
        if entry.is_symlink():
            assert entry.resolve().parent == agents_skills, (
                f"{entry} is a symlink but does not resolve inside .agents/skills/"
            )
