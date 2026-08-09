"""Acceptance tests for the OKF example packs (#966).

Verifies the issue's acceptance criterion — "a new vault started from either
methodology's examples passes ``okf_validate`` from the first note" — by
building a declared vault from the real ``examples/*/templates`` files and
asserting the conformance audit counts every note conformant. This also guards
the shipped example templates against drifting into non-conformance (e.g. a
template losing its ``type``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO_ROOT / "examples"

_DECLARED_INDEX = '---\nokf_version: "0.2"\ntitle: Root\n---\n# Root\n'

# The note templates each pack ships (index.md is the reserved declaration and
# is excluded from the conformance count).
_PACKS = {
    "okf": ["concept.md", "capture.md"],
    "para": ["inbox.md", "project.md", "area.md", "resource.md", "weekly-review.md"],
    "zettelkasten": ["fleeting.md", "literature.md", "permanent.md", "moc.md"],
}


def _build_vault_from_templates(dest: Path, pack: str, templates: list[str]) -> Vault:
    dest.mkdir(parents=True, exist_ok=True)
    # Declare the bundle so OKF detection is active.
    (dest / "index.md").write_text(_DECLARED_INDEX, encoding="utf-8")
    tdir = _EXAMPLES / pack / "templates"
    for name in templates:
        (dest / name).write_text((tdir / name).read_text(encoding="utf-8"))
    vault = Vault(source_dir=dest, okf_mode="auto")
    vault.index.build_index()
    return vault


@pytest.mark.parametrize("pack", list(_PACKS))
def test_example_templates_pass_okf_validate(tmp_path: Path, pack: str) -> None:
    templates = _PACKS[pack]
    vault = _build_vault_from_templates(tmp_path / pack, pack, templates)
    try:
        report = vault.reader.okf_validate()
        assert report.active is True
        # index.md is reserved → excluded; every template note is counted.
        assert report.total_notes == len(templates)
        assert report.conformant_notes == report.total_notes
        assert report.missing_type.count == 0
        assert report.unparseable_frontmatter.count == 0
    finally:
        vault.close()


@pytest.fixture
def okf_prompt_dir() -> Iterator[Path]:
    yield _EXAMPLES / "okf" / "prompts"


def test_okf_prompt_pack_is_complete(okf_prompt_dir: Path) -> None:
    # The four prompts the issue calls for, each present and non-empty.
    expected = {
        "okf-author-concept.md",
        "okf-verify-note.md",
        "okf-triage-stale.md",
        "okf-migrate-vault.md",
    }
    present = {p.name for p in okf_prompt_dir.glob("*.md")}
    assert expected <= present
    for name in expected:
        text = (okf_prompt_dir / name).read_text(encoding="utf-8")
        # Prompt frontmatter carries a description the client surfaces.
        assert text.startswith("---")
        assert "description:" in text
