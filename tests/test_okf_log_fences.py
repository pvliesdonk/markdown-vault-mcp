"""Log entries stay outside authored fenced examples (roadmap #1416)."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pytest
from markdown_it import MarkdownIt

from markdown_vault_mcp.okf import append_okf_log_entry
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("opener", "closer"),
    [("```markdown", "```"), ("~~~md", "~~~"), ("````", "`````"), ("   ~~~", "  ~~~~")],
)
@pytest.mark.parametrize("position", ["preamble", "current", "older"])
@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_append_preserves_fenced_examples(
    opener: str, closer: str, position: str, ending: str
) -> None:
    """A fake H2 neither captures an entry nor creates a date section."""
    example = f"{opener}\n## 2026-08-09\n- quoted entry\n{closer}\n"
    header = "# Log\n\n"
    section = "## 2026-08-09\n\n- existing event\n\nA quoted example follows.\n"
    if position == "preamble":
        text = header + example + "\n## 2026-08-01\n\n- older event\n"
    elif position == "older":
        text = header + "## 2026-08-01\n\n" + example
    else:
        text = header + section + "\n" + example
    text = text.replace("\n", ending) if ending else text.rstrip("\n")
    out = append_okf_log_entry(text, date="2026-08-09", summary="new event")
    assert example in out
    tokens = MarkdownIt("commonmark").parse(out)
    assert any(t.type == "inline" and t.content == "new event" for t in tokens)
    assert not any(t.type == "fence" and "new event" in t.content for t in tokens)
    assert sum(t.type == "inline" and t.content == "2026-08-09" for t in tokens) == 1
    if position == "current":
        assert out.index("new event") > out.index(
            closer, out.index(opener) + len(opener)
        )


@pytest.mark.parametrize(
    "example",
    [
        "````\n## fake\n```\n## also fake\n````\n",
        "```\n## fake\n~~~\n## also fake\n```\n",
        "```\n## fake\n``` not a closer\n## also fake\n```\n",
    ],
)
def test_only_matching_fence_closes_example(example: str) -> None:
    text = "# Log\n\n## 2026-08-09\n\n" + example
    out = append_okf_log_entry(text, date="2026-08-09", summary="new event")
    assert out == text + "- new event\n"


@pytest.mark.parametrize("opener", ["```", "~~~", "````md"])
@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{f}\n## fake\n", "{f}\n\n## 2026-08-09\n\n- new event\n\n## fake\n"),
        ("{f}\n## 2026-08-09\n", "{f}\n## 2026-08-09\n- new event\n"),
        ("{f}\nexample\n", "{f}\nexample\n\n## 2026-08-09\n\n- new event\n"),
        (
            "# Log\n\n## 2026-08-09\n\n{f}\n## fake\n",
            "# Log\n\n## 2026-08-09\n\n{f}\n- new event\n## fake\n",
        ),
        (
            "# Log\n\n## 2026-08-01\n\n{f}\n## fake\n",
            "# Log\n\n## 2026-08-09\n\n- new event\n\n## 2026-08-01\n\n{f}\n## fake\n",
        ),
        (
            "# Log\n\n## 2026-08-09\n\n## 2026-08-01\n\n{f}\n## fake\n",
            "# Log\n\n## 2026-08-09\n- new event\n\n## 2026-08-01\n\n{f}\n## fake\n",
        ),
        (
            "~~~\n## 2026-08-09\n~~~\n{f}\n## fake\n",
            "~~~\n## 2026-08-09\n~~~\n{f}\n- new event\n## fake\n",
        ),
    ],
)
def test_unclosed_fence_retains_legacy_output(
    opener: str, indent: str, ending: str, text: str, expected: str
) -> None:
    """An unmatched opener preserves raw-heading placement, even inside code."""
    text = text.format(f=indent + opener)
    expected = expected.format(f=indent + opener)
    text = text.replace("\n", ending) if ending else text.rstrip("\n")
    assert (
        append_okf_log_entry(text, date="2026-08-09", summary="new event") == expected
    )


def test_only_fenced_date_does_not_supply_a_real_section() -> None:
    text = "# Log\n\n```\n## 2026-08-09\n```\n"
    out = append_okf_log_entry(text, date="2026-08-09", summary="new event")
    assert out == text + "\n## 2026-08-09\n\n- new event\n"


def test_backtick_in_info_is_not_an_opening_fence() -> None:
    text = "# Log\n\n```not`a-fence\n\n## 2026-08-09\n\n- old\n"
    out = append_okf_log_entry(text, date="2026-08-09", summary="new event")
    assert out == text + "- new event\n"


@pytest.mark.parametrize("closed", [True, False])
@pytest.mark.parametrize("operation", ["write", "edit", "append"])
def test_maintained_write_preserves_log_examples(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, closed: bool, operation: str
) -> None:
    """Maintenance preserves closed examples and legacy unclosed behavior."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n\nBefore.\n", encoding="utf-8")
    example = "```markdown\n## fake\n- quoted entry\n" + ("```\n" if closed else "")
    body = f"# Log\n\n## {date.today().isoformat()}\n\n- earlier decision\n\n{example}"
    original = ("---\ntitle: History\ncustom: kept\n---\n" + body).encode()
    log = root / "log.md"
    log.write_bytes(original)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_write=True, write_protect_existing=False
        ),
    )
    try:
        vault.index.build_index()
        with caplog.at_level(logging.WARNING):
            if operation == "write":
                vault.writer.write("note.md", "# Note\n\nAfter.\n")
            elif operation == "edit":
                vault.writer.edit("note.md", "Before.", "After.")
            else:
                vault.writer.append("note.md", "After.")
        wait_for_writer_drain(vault)
        saved = vault.reader.read("note.md")
        assert saved is not None and "After." in saved.content
        assert (root / "index.md").exists()
        result = log.read_text(encoding="utf-8")
        assert "custom: kept" in result
        tokens = MarkdownIt("commonmark").parse(result)
        if closed:
            assert example in result
            assert any(t.type == "inline" and "`note.md`" in t.content for t in tokens)
        else:
            # Existing behavior is intentionally retained, not silently repaired.
            assert any(t.type == "fence" and "`note.md`" in t.content for t in tokens)
        assert "okf_convention_log_failed" not in caplog.text
    finally:
        vault.close()
