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
