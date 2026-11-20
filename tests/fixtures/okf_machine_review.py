"""Synthetic vault and service identity for machine review tests."""

from pathlib import Path

import pytest

NOTE = "---\ntype: concept\n---\nBody\n"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """Create a synthetic declared bundle with one concept note."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text(NOTE, encoding="utf-8")
    (root / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Bundle\n', encoding="utf-8"
    )
    return root


@pytest.fixture
def machine_review_env(
    _clear_env: None, vault_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Configure a writable declared bundle and authenticated service principal."""
    for key, value in {
        "SOURCE_DIR": str(vault_root),
        "READ_ONLY": "false",
        "OKF_WRITE": "true",
        "OKF_MACHINE_REVIEW": "true",
    }.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "checker")
    monkeypatch.setattr(
        "markdown_vault_mcp._subject.get_claims",
        lambda: {"sub": "checker", "idtyp": "app"},
    )
    return vault_root
