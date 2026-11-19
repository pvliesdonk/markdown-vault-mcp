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
    CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16,
    GENERATED_INSTRUCTIONS_TARGET_UTF16,
    ConfigurationError,
    HealthCheck,
    InstructionRole,
    ServerConfig,
    TransferConfig,
    apply_tool_visibility,
    build_auth,
    build_bearer_auth,
    configure_logging_from_env,
    domain_env_suffixes,
    domain_env_surface,
    finalize_instructions,
    find_nonconforming_log_calls,
    get_claims,
    get_current_auth_mode,
    get_subject,
    instructions_for,
    maybe_start_debugpy,
    normalise_http_path,
    register_health_routes,
    register_server_info_tool,
    run_http,
    utf16_code_units,
    wire_middleware_stack,
)
from fastmcp_pvl_core._logging_middleware import RequestLoggingMiddleware
from fastmcp_pvl_core._transfer.store import (
    TokenInFlightError,
    TransferStore,
    TransferToken,
    TransferTokenError,
)

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
    "maybe_start_debugpy",
    "normalise_http_path",
    "register_health_routes",
    "register_server_info_tool",
    "run_http",
    # The server-instructions builder (#2305).
    "CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16",
    "GENERATED_INSTRUCTIONS_TARGET_UTF16",
    "InstructionRole",
    "finalize_instructions",
    "instructions_for",
    "utf16_code_units",
    # The transfer token store (#2320).
    "TokenInFlightError",
    "TransferStore",
    "TransferToken",
    "TransferTokenError",
    # The transfer settings (#2321).
    "TransferConfig",
]
