"""Move a downstream from CLAUDE.md to AGENTS.md during `copier update`.

Runs from copier.yml `_migrations` (after-stage, every update, after the
old-render → project diff has been applied).  Copier re-renders CLAUDE.md as
the stub and leaves inline conflict markers where the project's DOMAIN
content used to be; the project's real pre-update CLAUDE.md is still at git
HEAD, so recover from there — never from the conflict-marked file.

Steps (each printed when taken):
1. Splice HEAD:CLAUDE.md's filled DOMAIN blocks into AGENTS.md's empty ones
   (by ordinal).  A filled AGENTS.md block is never overwritten.
2. Overwrite CLAUDE.md with the stub.
3. Replace any real directory at .claude/skills/<template skill> with the
   relative symlink into .agents/skills/ the template ships.
4. Point at `git show HEAD:CLAUDE.md` so hand edits outside DOMAIN blocks
   can be re-applied to AGENTS.md — the only case that needs a human.

Idempotent; a no-op on `copier copy` (no HEAD:CLAUDE.md) and on a project
that already migrated.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

STUB = (
    "@AGENTS.md\n"
    "\n"
    "Project instructions live in `AGENTS.md` (domain content between its "
    "`DOMAIN-START` / `DOMAIN-END` markers). This file is template-owned; "
    "do not add content here.\n"
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

DOMAIN_RE = re.compile(
    r"(<!-- DOMAIN-START -->\n)(.*?)(<!-- DOMAIN-END -->)", re.DOTALL
)
_PLACEHOLDER_RE = re.compile(r"^<!--.*Kept across copier update\.\s*-->$")


def domain_blocks(text: str) -> list[str]:
    return [m.group(2) for m in DOMAIN_RE.finditer(text)]


def is_placeholder(block: str) -> bool:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    return all(_PLACEHOLDER_RE.match(ln) for ln in lines)


def splice(agents_text: str, head_claude_text: str) -> tuple[str, int]:
    head = domain_blocks(head_claude_text)
    moved = 0
    counter = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal moved, counter
        i = counter
        counter += 1
        if i < len(head) and is_placeholder(m.group(2)) and not is_placeholder(head[i]):
            moved += 1
            return m.group(1) + head[i] + m.group(3)
        return m.group(0)

    return DOMAIN_RE.sub(repl, agents_text), moved


def _fix_skill_links(root: Path, actions: list[str]) -> None:
    claude_skills = root / ".claude" / "skills"
    for name in TEMPLATE_SKILLS:
        if not (root / ".agents" / "skills" / name).is_dir():
            continue
        link = claude_skills / name
        target = f"../../.agents/skills/{name}"
        if link.is_symlink():
            if str(link.readlink()) == target:
                continue
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
            actions.append(
                f"removed real directory .claude/skills/{name} (now a symlink)"
            )
        claude_skills.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        actions.append(f"linked .claude/skills/{name} -> {target}")


def migrate(root: Path, *, head_claude: str | None) -> list[str]:
    actions: list[str] = []
    agents = root / "AGENTS.md"
    if head_claude is None or not agents.is_file() or not DOMAIN_RE.search(head_claude):
        return actions
    text = agents.read_text(encoding="utf-8")
    new_text, moved = splice(text, head_claude)
    if moved:
        agents.write_text(new_text, encoding="utf-8")
        actions.append(
            f"spliced {moved} DOMAIN block(s) from HEAD:CLAUDE.md into AGENTS.md"
        )
    claude = root / "CLAUDE.md"
    if not claude.is_file() or claude.read_text(encoding="utf-8") != STUB:
        claude.write_text(STUB, encoding="utf-8")
        actions.append("rewrote CLAUDE.md as the @AGENTS.md stub")
    _fix_skill_links(root, actions)
    actions.append(
        "CLAUDE.md content before this update is at `git show HEAD:CLAUDE.md`; "
        "re-apply any hand edits made outside its DOMAIN blocks to AGENTS.md"
    )
    return actions


def _head_claude(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "show", "HEAD:CLAUDE.md"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def main() -> int:
    root = Path.cwd()
    for line in migrate(root, head_claude=_head_claude(root)):
        print(f"migrate_agent_instructions: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
