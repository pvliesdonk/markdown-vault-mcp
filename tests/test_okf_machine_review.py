"""Machine checks retain their actor, check and snapshot in review evidence."""

from pathlib import Path

import frontmatter
import pytest
from fastmcp import Client

from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.okf import derive_trust_tier
from tests.conftest import _parse_tool_data, wait_for_mcp_writer_drain
from tests.server_factory import make_server


@pytest.mark.parametrize("check", ["links", "schema", "source-comparison", "x-local"])
async def test_service_records_machine_check(
    _clear_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: str
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    note = root / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
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
    snapshot = concept_snapshot(original)
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_verify",
            {"path": "note.md", "reviewed_snapshot": snapshot, "check": check},
        )
        await wait_for_mcp_writer_drain(client)
    assert _parse_tool_data(result)["verifier"] == "process:checker"
    metadata = frontmatter.loads(note.read_text()).metadata
    assert metadata["verified"][0]["by"] == "process:checker"
    assert metadata["x-mvmcp"]["reviews"][0]["snapshot"] == snapshot
    assert metadata["x-mvmcp"]["reviews"][0]["check"] == check
    assert derive_trust_tier(metadata) == "machine-confirmed"


@pytest.mark.parametrize(
    "check,enabled,expected",
    [
        ("human-review", True, "cannot record human-review"),
        ("links", False, "OKF_MACHINE_REVIEW"),
        ("bogus", True, "Unknown check"),
        ("x-", True, "Unknown check"),
    ],
)
async def test_service_refusals(
    _clear_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    check: str,
    enabled: bool,
    expected: str,
) -> None:
    from fastmcp.exceptions import ToolError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    note = root / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    for key, value in {
        "SOURCE_DIR": str(root),
        "READ_ONLY": "false",
        "OKF_WRITE": "true",
        "OKF_MACHINE_REVIEW": str(enabled),
    }.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "checker")
    monkeypatch.setattr(
        "fastmcp_pvl_core.get_claims", lambda: {"sub": "checker", "idtyp": "app"}
    )
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match=expected):
            await client.call_tool(
                "okf_verify",
                {
                    "path": "note.md",
                    "reviewed_snapshot": concept_snapshot(original),
                    "check": check,
                },
            )
        await wait_for_mcp_writer_drain(client)
    assert note.read_text() == original


def test_machine_receipt_and_existing_human_tier(tmp_path: Path) -> None:
    import json

    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    text = '---\ntype: concept\nverified: [{by: "human:alice", at: "2026-01-01T00:00:00Z"}]\n---\nBody\n'
    (root / "note.md").write_text(text)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        result = vault.writer.okf_verify(
            "note.md",
            "checker",
            concept_snapshot(text),
            "schema",
            actor="process:checker",
        )
        metadata = frontmatter.loads((root / "note.md").read_text()).metadata
        receipt = json.loads((tmp_path / "state.receipts.json").read_text())["rows"][0]
        assert (
            result["verifier"]
            == receipt["by"]
            == metadata["verified"][-1]["by"]
            == "process:checker"
        )
        assert receipt["check"] == "schema"
        assert derive_trust_tier(metadata) == "human-reviewed"
    finally:
        vault.close()


async def test_machine_setting_does_not_bypass_human_confirmation(
    _clear_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp.exceptions import ToolError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    note = root / "note.md"
    original = "---\ntype: concept\n---\nBody\n"
    note.write_text(original)
    for key, value in {
        "SOURCE_DIR": str(root),
        "READ_ONLY": "false",
        "OKF_WRITE": "true",
        "OKF_MACHINE_REVIEW": "true",
    }.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "alice")
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "alice"})
    async with Client(make_server(), mode="legacy") as client:
        with pytest.raises(ToolError, match=r"human|confirm|elicitation"):
            await client.call_tool(
                "okf_verify",
                {"path": "note.md", "reviewed_snapshot": concept_snapshot(original)},
            )
        await wait_for_mcp_writer_drain(client)
    assert note.read_text() == original


@pytest.mark.parametrize(
    ("subject", "claims", "expected"),
    [
        ("bad identity", {"sub": "bad identity", "idtyp": "app"}, "usable identity"),
        ("alice", {"sub": "alice"}, "require a service principal"),
    ],
)
async def test_machine_checks_require_service_with_usable_identity(
    _clear_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    claims: dict[str, str],
    expected: str,
) -> None:
    from fastmcp.exceptions import ToolError

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    original = "---\ntype: concept\n---\nBody\n"
    note = root / "note.md"
    note.write_text(original)
    for key, value in {
        "SOURCE_DIR": str(root),
        "READ_ONLY": "false",
        "OKF_WRITE": "true",
        "OKF_MACHINE_REVIEW": "true",
    }.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: subject)
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: claims)
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match=expected):
            await client.call_tool(
                "okf_verify",
                {
                    "path": "note.md",
                    "reviewed_snapshot": concept_snapshot(original),
                    "check": "links",
                },
            )
        await wait_for_mcp_writer_drain(client)
    assert note.read_text() == original
