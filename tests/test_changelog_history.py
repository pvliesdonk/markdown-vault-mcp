"""CHANGELOG.md history pins: content knope never rewrites and only a hand edit
can lose (#1123)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# historical breaking-change content (#1123)
# ---------------------------------------------------------------------------


def _changelog_sections() -> list[tuple[str, str]]:
    """Return ``(heading, body)`` for every ``## <version>`` section."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (?=\d)", text, flags=re.MULTILINE)[1:]
    out = []
    for part in parts:
        head, _, body = part.partition("\n")
        out.append((head.strip(), body))
    return out


def test_changelog_keeps_the_3_0_0_breaking_block() -> None:
    """The 3.0.0 section still carries its restored breaking changes (#1123).

    The 2026-08-16 reconstruction (#1067) generated pre-4.0 sections with
    only Features / Bug Fixes / Chores headings, flattening sixteen `!`
    markers and dropping the operator migration instructions that had
    shipped in the tagged changelogs — leaving 3.0.0, a major release,
    presenting an empty section. A support question then arrived from
    someone who had set the renamed env var and found it inert, which is
    exactly the failure the deleted entry predicted.

    This pins the recovered content rather than the whole file: knope never
    rewrites a historical section, so nothing but a hand edit can remove it.
    """
    sections = dict(_changelog_sections())
    body = sections.get("3.0.0 (2026-06-17)")
    assert body is not None, "the 3.0.0 section disappeared from CHANGELOG.md"

    assert "### Breaking Changes" in body
    for marker in (
        "MARKDOWN_VAULT_MCP_READY_TIMEOUT_S",
        "MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S",
        "IndexNotReadyError",
        "IndexUnavailableError",
        "is_index_ready",
        "wait_for_index_ready",
        "needs_queryable",
        "GroupedResult",
        "MAX_ATTACHMENT_SIZE_MB",
    ):
        assert marker in body, f"3.0.0 breaking block lost {marker}"


def test_changelog_preamble_explains_the_empty_promotion_sections() -> None:
    """Empty sections are documented, not mysterious (#1123).

    A stable release whose whole range is the promotion commit has nothing
    for knope to count — its work sits in the ``-rc`` sections beneath it.
    That is by design, but a reader hitting a blank heading cannot tell it
    apart from lost content, so the preamble above the insertion flag names
    the shape and lists the sections that have it.
    """
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    preamble, flag, _ = text.partition("<!-- version list -->")
    assert flag, "insertion flag missing"

    empty = [head for head, body in _changelog_sections() if not body.strip()]
    for head in empty:
        version = head.split(" ", 1)[0]
        assert version in preamble, (
            f"release section {version} is empty and unexplained — either give "
            f"it content or name it in the CHANGELOG.md preamble"
        )
