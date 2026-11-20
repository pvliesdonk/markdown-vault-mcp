"""``normalise_http_path`` (#2334).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_cli.py`` (``TestNormaliseHttpPath``).
"""

from __future__ import annotations

from markdown_vault_mcp._http_path import normalise_http_path


class TestNormaliseHttpPath:
    def test_none_returns_default(self):
        assert normalise_http_path(None) == "/mcp"

    def test_empty_returns_default(self):
        assert normalise_http_path("") == "/mcp"

    def test_whitespace_returns_default(self):
        assert normalise_http_path("   ") == "/mcp"

    def test_adds_leading_slash(self):
        assert normalise_http_path("mcp") == "/mcp"

    def test_strips_trailing_slash(self):
        assert normalise_http_path("/mcp/") == "/mcp"

    def test_preserves_multi_segment(self):
        assert normalise_http_path("/api/mcp") == "/api/mcp"

    def test_root_path_preserved(self):
        assert normalise_http_path("/") == "/"

    def test_custom_default(self):
        assert normalise_http_path(None, default="/api") == "/api"

    def test_strips_surrounding_whitespace(self):
        assert normalise_http_path("  /mcp  ") == "/mcp"
