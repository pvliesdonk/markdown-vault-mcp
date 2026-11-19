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
    build_auth,
    build_bearer_auth,
    domain_env_suffixes,
    domain_env_surface,
    get_claims,
    get_current_auth_mode,
    get_subject,
)
from fastmcp_pvl_core import __version__ as CORE_VERSION
from fastmcp_pvl_core._log_render import (
    _ACCESS_FIELDS_ATTR,
    JsonFormatter,
    _AccessLogFields,
    _NeverRaiseFilter,
    _or_fallback,
    bind_record,
    render_rich,
)

__all__ = [  # noqa: RUF022 - grouped by area, not sorted
    # Configuration, environment helpers and the config error (#2275).
    "ConfigurationError",
    "ServerConfig",
    "domain_env_suffixes",
    "domain_env_surface",
    # The log renderer (#2342).
    "_ACCESS_FIELDS_ATTR",
    "JsonFormatter",
    "_AccessLogFields",
    "_NeverRaiseFilter",
    "_or_fallback",
    "bind_record",
    "render_rich",
    # Authentication and the request subject (#2277).
    "build_auth",
    "build_bearer_auth",
    "get_claims",
    "get_current_auth_mode",
    "get_subject",
    # The installed pvl-core version get_server_info reports (#2283).
    "CORE_VERSION",
]
