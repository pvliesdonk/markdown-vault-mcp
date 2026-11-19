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
    HealthCheck,
    ServerConfig,
    apply_tool_visibility,
    build_auth,
    build_bearer_auth,
    configure_logging_from_env,
    domain_env_suffixes,
    domain_env_surface,
    find_nonconforming_log_calls,
    get_claims,
    get_current_auth_mode,
    get_subject,
    maybe_start_debugpy,
    normalise_http_path,
    register_health_routes,
    register_server_info_tool,
    run_http,
    wire_middleware_stack,
)
from fastmcp_pvl_core._logging_middleware import RequestLoggingMiddleware
from fastmcp_pvl_core._visibility import effective_tool_names, registered_tool_names

__all__ = [  # noqa: RUF022 - grouped by area, not sorted
    # Configuration, environment helpers and the config error (#2275).
    "ConfigurationError",
    "ServerConfig",
    "domain_env_suffixes",
    "domain_env_surface",
    # Logging setup and the request-logging middleware (#2276).
    "RequestLoggingMiddleware",
    "configure_logging_from_env",
    "find_nonconforming_log_calls",
    "wire_middleware_stack",
    # Authentication and the request subject (#2277).
    "build_auth",
    "build_bearer_auth",
    "get_claims",
    "get_current_auth_mode",
    "get_subject",
    # HTTP serving, health routes, server info and tool visibility (#2278).
    "HealthCheck",
    "apply_tool_visibility",
    "effective_tool_names",
    "registered_tool_names",
    "maybe_start_debugpy",
    "normalise_http_path",
    "register_health_routes",
    "register_server_info_tool",
    "run_http",
]
