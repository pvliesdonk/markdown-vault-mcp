"""Tests for the MCP Apps SPA vendoring + serving wiring."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown_vault_mcp._server_apps as apps


def test_app_html_is_served_with_tools_rewritten() -> None:
    """_SPA_SHELL_HTML loads from static/app.html and the app___ source
    literals are rewritten away to the fastmcp hash form at import."""
    html = apps._SPA_SHELL_HTML
    assert "<html" in html
    assert "app___" not in html  # every literal rewritten


def test_note_view_reaches_served_html() -> None:
    """The extracted note-preview view (views/note.js) must reach the served
    app.html.

    Guards against its ``/*@@FILE:views/note.js@@*/`` marker being dropped from
    core.js — a regression the build/vendor ``--check`` gates would not catch
    (they compare committed vs freshly assembled, both of which would then lack
    the view). Asserts on note.js body markers, not the shell's ``data-tab``
    markup (which survives even if the view module is orphaned)."""
    html = apps._SPA_SHELL_HTML
    assert "loadPreview" in html
    assert "preview-browse-btn" in html


def test_build_check_is_clean() -> None:
    """The committed app.src.html matches the spa/ partials (offline --check)."""
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "build_spa.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr


def test_vendor_check_is_clean() -> None:
    """The committed app.html matches app.src.html (offline --check)."""
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "vendor_spa.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr


def test_vendor_check_detects_drift() -> None:
    """Mutating app.src.html makes --check fail (anti-drift gate works)."""
    repo = Path(__file__).resolve().parent.parent
    src = repo / "src" / "markdown_vault_mcp" / "static" / "app.src.html"
    original = src.read_text(encoding="utf-8")
    try:
        src.write_text(original + "\n<!-- drift -->\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(repo / "scripts" / "vendor_spa.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=repo,
        )
        assert result.returncode == 1
    finally:
        src.write_text(original, encoding="utf-8")
