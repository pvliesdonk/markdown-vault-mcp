"""Markdown Vault MCP.

Generic markdown vault MCP with hybrid search
"""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _distribution_version

# The installed distribution's version, which the release flow stamps into
# pyproject.toml; a literal here drifted from it on every release (#615).
# The fallback covers an import from a source checkout that is not installed,
# such as a script run against the tree without ``uv sync``.
try:
    __version__ = _distribution_version("markdown-vault-mcp")
except _PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0+unknown"
