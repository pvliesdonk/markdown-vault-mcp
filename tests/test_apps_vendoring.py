"""Tests for the MCP Apps SPA vendoring + serving wiring."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from fastmcp import Client, FastMCP
from fastmcp.server.providers.addressing import TOOL_HASH_META_KEY, hash_tool

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


def test_vendor_check_detects_drift(tmp_path: Path) -> None:
    """Mutating app.src.html makes --check fail (anti-drift gate works).

    Mirrors the script and the static dir into ``tmp_path`` and drifts the COPY
    rather than editing the committed ``app.src.html`` in place.  An in-place
    edit with a try/finally restore leaves the working tree dirty if the test
    is SIGKILLed mid-run, and two ``pytest-xdist`` workers hitting it
    concurrently would race on the same file.

    ``vendor_spa.py`` finds its tree via ``Path(__file__).parent.parent / "src"``,
    so copying the script alongside a ``src/`` mirror is what redirects it — the
    subprocess boundary puts ``monkeypatch`` out of reach.
    """
    repo = Path(__file__).resolve().parent.parent
    static_src = repo / "src" / "markdown_vault_mcp" / "static"

    script = tmp_path / "scripts" / "vendor_spa.py"
    script.parent.mkdir(parents=True)
    shutil.copy2(repo / "scripts" / "vendor_spa.py", script)

    static_copy = tmp_path / "src" / "markdown_vault_mcp" / "static"
    static_copy.mkdir(parents=True)
    for name in ("app.src.html", "app.html"):
        shutil.copy2(static_src / name, static_copy / name)

    # Sanity: the untouched mirror is clean, so a failure below is the drift we
    # introduced and not a broken copy.
    clean = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert clean.returncode == 0, clean.stderr

    drifted = static_copy / "app.src.html"
    drifted.write_text(
        drifted.read_text(encoding="utf-8") + "\n<!-- drift -->\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 1

    # The committed source is untouched — the whole point of the tmp mirror.
    assert "<!-- drift -->" not in (static_src / "app.src.html").read_text(
        encoding="utf-8"
    )


def test_app_tool_hash_survives_protocol_serialisation() -> None:
    """The hash reaches a client under fastmcp's public key.

    FastMCP strips underscore-prefixed keys from ``meta["fastmcp"]`` when it
    serialises a tool, so the previous ``_tool_hash`` key was present in
    process and absent on the wire (#614). The round trip through a client
    is the assertion, because that is where the addressing code reads it.
    """
    tool_name = next(iter(apps._APP_TOOL_NAMES))
    meta = apps._app_tool_meta(tool_name)
    assert not any(key.startswith("_") for key in meta["fastmcp"])
    assert meta["fastmcp"][TOOL_HASH_META_KEY] == hash_tool(apps._APP_NAME, tool_name)

    mcp = FastMCP("apps-meta-probe")

    @mcp.tool(name=tool_name, meta=meta)
    def _probe() -> str:
        return "ok"

    async def _listed() -> dict[str, object]:
        async with Client(mcp) as client:
            (tool,) = [t for t in await client.list_tools() if t.name == tool_name]
        return dict(tool.meta or {})

    on_the_wire = asyncio.run(_listed())
    assert on_the_wire["fastmcp"][TOOL_HASH_META_KEY] == hash_tool(  # type: ignore[index]
        apps._APP_NAME, tool_name
    )
