"""The one place this project imports ``fastmcp_pvl_core`` (#2274).

#2241 dissolves pvl-core one area at a time: each name below either becomes a
direct FastMCP call or moves into a module of this project, and then leaves
this file. When the file is empty, the dependency goes (#2283).
``tests/test_pvl_core_imports.py`` fails on any other import of
``fastmcp_pvl_core``, and on a ``monkeypatch``/``patch`` target that names it;
patch ``markdown_vault_mcp._pvl_core.<name>`` instead, from code that looks the
name up at call time.

``__all__`` groups the names by the area feature that removes them.
"""

from __future__ import annotations

from fastmcp_pvl_core import (
    ConfigurationError,
    ServerConfig,
    server_config_env_suffixes,
)
from fastmcp_pvl_core import __version__ as CORE_VERSION
from fastmcp_pvl_core._config import DEFAULT_BEARER_SUBJECT

__all__ = [  # noqa: RUF022 - grouped by area, not sorted
    # Configuration, environment helpers and the config error (#2275).
    "ConfigurationError",
    # ServerConfig and its readers (#2288).
    "DEFAULT_BEARER_SUBJECT",
    "ServerConfig",
    "server_config_env_suffixes",
    # The installed pvl-core version get_server_info reports (#2283).
    "CORE_VERSION",
]
