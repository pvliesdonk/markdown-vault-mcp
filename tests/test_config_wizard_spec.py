"""Drift cross-check + schema validity for the docs config-wizard spec.

The wizard's env-var knowledge lives in
``docs/javascripts/config-wizard/wizard-spec.json``. This module enumerates the
canonical env-var inventory from code and enforces two-way containment so the
spec cannot silently drift from ``config.py`` (see the design spec
``docs/superpowers/specs/2026-06-16-config-generator-design.md``).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fastmcp_pvl_core import ServerConfig

import markdown_vault_mcp.config as config_mod
from markdown_vault_mcp import config_sections

PREFIX = "MARKDOWN_VAULT_MCP"

# Helper call names whose 2nd positional arg is a prefixed suffix literal.
_PREFIXED_HELPERS = {"env", "env_int", "env_float", "opt_int"}

REPO_ROOT = Path(__file__).resolve().parent.parent


def _iter_config_source_files() -> list[Path]:
    """Domain-tier source files that read env vars."""
    sections_dir = Path(config_sections.__file__).parent
    files = [Path(config_mod.__file__)]
    files += sorted(p for p in sections_dir.glob("*.py") if p.name != "__init__.py")
    return files


def _server_config_source_file() -> Path:
    """Resolve the installed ``ServerConfig`` source file (server tier)."""
    src = inspect.getsourcefile(ServerConfig)
    assert src is not None, "ServerConfig source file not found"
    return Path(src)


def extract_env_vars_from_source(source: str) -> set[str]:
    """Extract env-var names referenced in a Python source string.

    Recognizes ``env(prefix, "SUFFIX")`` / ``env_int|env_float|opt_int`` calls
    (prefixed -> ``MARKDOWN_VAULT_MCP_SUFFIX``) and ``os.environ.get("NAME")``
    literals (bare names).
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # env(prefix, "SUFFIX", ...) family - 2nd positional arg is the suffix.
        if (
            isinstance(func, ast.Name)
            and func.id in _PREFIXED_HELPERS
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            suffix = node.args[1].value
            if isinstance(suffix, str):
                found.add(f"{PREFIX}_{suffix}")
        # os.environ.get("NAME") - bare ecosystem-standard names.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def domain_inventory() -> set[str]:
    out: set[str] = set()
    for path in _iter_config_source_files():
        out |= extract_env_vars_from_source(path.read_text(encoding="utf-8"))
    return out


def server_inventory() -> set[str]:
    src = _server_config_source_file().read_text(encoding="utf-8")
    return extract_env_vars_from_source(src)


def test_domain_inventory_includes_known_prefixed_var() -> None:
    assert f"{PREFIX}_GIT_TOKEN" in domain_inventory()


def test_domain_inventory_includes_bare_name_var() -> None:
    # OLLAMA_HOST / OPENAI_API_KEY are read via os.environ.get (bare).
    inv = domain_inventory()
    assert "OLLAMA_HOST" in inv
    assert "OPENAI_API_KEY" in inv


def test_server_inventory_includes_oidc_var() -> None:
    assert f"{PREFIX}_OIDC_CLIENT_ID" in server_inventory()
