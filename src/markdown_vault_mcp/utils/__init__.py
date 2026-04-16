"""Shared pure-function utilities for markdown-vault-mcp."""

from markdown_vault_mcp.utils.links import (
    apply_link_replacement,
    compute_new_raw_target,
)
from markdown_vault_mcp.utils.text import (
    CHAR_SUBS,
    build_position_map,
    find_closest_match,
    normalize_text,
)

__all__ = [
    "CHAR_SUBS",
    "apply_link_replacement",
    "build_position_map",
    "compute_new_raw_target",
    "find_closest_match",
    "normalize_text",
]
