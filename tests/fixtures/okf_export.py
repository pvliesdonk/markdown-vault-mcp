"""Shared bytes-level subtree export fixture for OKF tests."""

import io
import zipfile
from pathlib import Path
from typing import Any

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.okf_export_report import OkfExportReport
from markdown_vault_mcp.vault import Vault, VaultSettings


def export_note(
    root: Path, content: bytes, *, source: str = "a.md", **kwargs: Any
) -> tuple[bytes, OkfExportReport]:
    """Export one fixture note from the guides subtree.

    Args:
        root: Fixture vault directory.
        content: Original note bytes.
        source: Note path relative to the subtree.
        **kwargs: Options passed to the bundle builder.

    Returns:
        Exported note bytes and its bundle report.
    """
    guides = root / "guides"
    guides.mkdir(exist_ok=True)
    (guides / source).write_bytes(content)
    (guides / "b.md").write_bytes(b"# B\n")
    vault = Vault(source_dir=root, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=root), folder="guides", **kwargs
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            return archive.read(source), result.report
    finally:
        vault.close()
