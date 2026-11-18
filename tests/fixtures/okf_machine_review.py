"""Synthetic vault and service identity for machine review tests."""

from pathlib import Path

import pytest

NOTE = "---\ntype: concept\n---\nBody\n"


@pytest.fixture
def machine_review_env(
    _clear_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Configure a writable declared bundle and authenticated service principal."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    (root / "note.md").write_text(NOTE)
    for key, value in {
        "SOURCE_DIR": str(root),
        "READ_ONLY": "false",
        "OKF_WRITE": "true",
        "OKF_MACHINE_REVIEW": "true",
    }.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "checker")
    monkeypatch.setattr(
        "fastmcp_pvl_core.get_claims", lambda: {"sub": "checker", "idtyp": "app"}
    )
    return root
