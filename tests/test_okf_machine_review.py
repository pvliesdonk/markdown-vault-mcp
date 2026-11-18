"""Machine checks retain their actor, check and snapshot in review evidence."""

from pathlib import Path

import frontmatter
import pytest
from fastmcp import Client

from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.okf import derive_trust_tier
from tests.conftest import _parse_tool_data, wait_for_mcp_writer_drain
from tests.fixtures.okf_machine_review import NOTE
from tests.fixtures.okf_machine_review import machine_review_env as machine_review_env
from tests.server_factory import make_server


@pytest.mark.parametrize("mode", ["elicit", "trust-auth"])
async def test_service_records_machine_check(
    machine_review_env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", mode)
    snapshot = concept_snapshot(NOTE)
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_verify",
            {"path": "note.md", "reviewed_snapshot": snapshot, "check": "links"},
        )
        await wait_for_mcp_writer_drain(client)
    assert _parse_tool_data(result)["verifier"] == "process:checker"
    metadata = frontmatter.loads((machine_review_env / "note.md").read_text()).metadata
    assert metadata["verified"][0]["by"] == "process:checker"
    assert metadata["x-mvmcp"]["reviews"][0]["snapshot"] == snapshot
    assert derive_trust_tier(metadata) == "machine-confirmed"


@pytest.mark.parametrize(
    "check,enabled,expected",
    [("links", False, "OKF_MACHINE_REVIEW"), ("bogus", True, "Unknown check")],
)
async def test_service_refusals(
    machine_review_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    check: str,
    enabled: bool,
    expected: str,
) -> None:
    from fastmcp.exceptions import ToolError

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MACHINE_REVIEW", str(enabled))
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match=expected):
            await client.call_tool(
                "okf_verify",
                {
                    "path": "note.md",
                    "reviewed_snapshot": concept_snapshot(NOTE),
                    "check": check,
                },
            )
        await wait_for_mcp_writer_drain(client)
    assert (machine_review_env / "note.md").read_text() == NOTE


@pytest.mark.parametrize(
    "subject,claims",
    [
        ("bad identity", {"sub": "bad identity", "idtyp": "app"}),
        ("alice", {"sub": "alice"}),
    ],
)
async def test_machine_checks_require_service_with_usable_identity(
    machine_review_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    claims: dict[str, str],
) -> None:
    from fastmcp.exceptions import ToolError

    monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: subject)
    monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: claims)
    async with Client(make_server()) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "okf_verify",
                {
                    "path": "note.md",
                    "reviewed_snapshot": concept_snapshot(NOTE),
                    "check": "links",
                },
            )
        await wait_for_mcp_writer_drain(client)
    assert (machine_review_env / "note.md").read_text() == NOTE


@pytest.mark.parametrize("check", ["links", "schema", "source-comparison", "x-local"])
def test_machine_receipt_and_check(tmp_path: Path, check: str) -> None:
    import json

    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text(NOTE)
    snapshot = concept_snapshot(NOTE)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        result = vault.writer.okf_verify("note.md", "checker", snapshot, check)
        metadata = frontmatter.loads((root / "note.md").read_text()).metadata
        receipt = json.loads((tmp_path / "state.receipts.json").read_text())["rows"][0]
        assert (
            result["verifier"]
            == receipt["by"]
            == metadata["verified"][-1]["by"]
            == "process:checker"
        )
        assert receipt["check"] == metadata["x-mvmcp"]["reviews"][-1]["check"] == check
    finally:
        vault.close()


@pytest.mark.parametrize(
    "subject,check",
    [
        ("bad identity", "links"),
        ("", "schema"),
        ("checker", "bogus"),
        ("checker", "x-"),
    ],
)
def test_invalid_machine_review_writes_no_note_or_receipt(
    tmp_path: Path, subject: str, check: str
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.utils.fs import is_regular_file
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text(NOTE)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_verify("note.md", subject, concept_snapshot(NOTE), check)
        assert note.read_text() == NOTE
        assert not is_regular_file(tmp_path / "state.receipts.json")
    finally:
        vault.close()


async def test_machine_check_preserves_human_tier_through_tool(
    machine_review_env: Path,
) -> None:
    note = machine_review_env / "note.md"
    original = '---\ntype: concept\nverified: [{by: "human:alice", at: "2026-01-01T00:00:00Z"}]\n---\nBody\n'
    note.write_text(original)
    async with Client(make_server()) as client:
        await client.call_tool(
            "okf_verify",
            {
                "path": "note.md",
                "reviewed_snapshot": concept_snapshot(original),
                "check": "links",
            },
        )
        await wait_for_mcp_writer_drain(client)
    metadata = frontmatter.loads(note.read_text()).metadata
    assert metadata["verified"][-1]["by"] == "process:checker"
    assert derive_trust_tier(metadata) == "human-reviewed"


async def test_service_human_review_refused_before_stale_snapshot(
    machine_review_env: Path,
) -> None:
    from fastmcp.exceptions import ToolError

    snapshot = concept_snapshot(NOTE)
    (machine_review_env / "note.md").write_text(NOTE + "changed\n")
    async with Client(make_server()) as client:
        with pytest.raises(
            ToolError, match="service principal cannot record human-review"
        ):
            await client.call_tool(
                "okf_verify", {"path": "note.md", "reviewed_snapshot": snapshot}
            )
        await wait_for_mcp_writer_drain(client)


def test_check_selects_actor_and_invalid_check_writes_nothing(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.okf import append_okf_verification
    from markdown_vault_mcp.utils.fs import is_regular_file
    from markdown_vault_mcp.vault import Vault, VaultSettings

    snapshot = concept_snapshot(NOTE)
    text = append_okf_verification(
        NOTE,
        subject="alice",
        now=datetime.now(UTC),
        reviewed_snapshot=snapshot,
        check="links",
    )
    assert frontmatter.loads(text).metadata["verified"][0]["by"] == "process:alice"
    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text(NOTE)
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        with pytest.raises(InvalidRequestError, match="Unknown check"):
            vault.writer.okf_verify("note.md", "alice", snapshot, "bogus")
        assert note.read_text() == NOTE
        assert not is_regular_file(tmp_path / "state.receipts.json")
    finally:
        vault.close()
