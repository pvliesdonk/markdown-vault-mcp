"""Vault setup shared by designated-index behavior tests."""

from pathlib import Path

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.vault import Vault, VaultSettings


def designated_vault(root: Path, folders: tuple[str, ...] = ("guides",)) -> Vault:
    """Open a writable OKF vault with generated indexes in selected folders."""
    capabilities = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=folders
    ).okf_capabilities
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_capabilities=capabilities
        ),
    )
