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


@pytest.mark.parametrize("check", ["links", "schema", "source-comparison", "x-local"])
async def test_service_records_machine_check(
    machine_review_env: Path, check: str
) -> None:
    note = machine_review_env / "note.md"
    original = NOTE
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
    machine_review_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    check: str,
    enabled: bool,
    expected: str,
) -> None:
    from fastmcp.exceptions import ToolError

    note = machine_review_env / "note.md"
    original = NOTE
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MACHINE_REVIEW", str(enabled))
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
    machine_review_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp.exceptions import ToolError

    note = machine_review_env / "note.md"
    original = NOTE
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
    machine_review_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    claims: dict[str, str],
    expected: str,
) -> None:
    from fastmcp.exceptions import ToolError

    note = machine_review_env / "note.md"
    original = NOTE
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


@pytest.mark.usefixtures("machine_review_env")
async def test_trust_auth_allows_service_machine_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_verify",
            {
                "path": "note.md",
                "reviewed_snapshot": concept_snapshot(NOTE),
                "check": "links",
            },
        )
        await wait_for_mcp_writer_drain(client)
    assert _parse_tool_data(result)["verifier"] == "process:checker"


async def test_verify_off_hides_machine_review(
    machine_review_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "off")
    async with Client(make_server()) as client:
        assert "okf_verify" not in [tool.name for tool in await client.list_tools()]
        await wait_for_mcp_writer_drain(client)
    assert (machine_review_env / "note.md").read_text() == NOTE


@pytest.mark.parametrize(
    ("actor", "check"),
    [
        ("process:checker", "human-review"),
        ("", "schema"),
        ("bad actor", "links"),
        ("process:checker", "bogus"),
    ],
)
def test_explicit_actor_refusal_writes_no_note_or_receipt(
    tmp_path: Path, actor: str, check: str
) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.okf import append_okf_verification
    from markdown_vault_mcp.utils.fs import is_regular_file
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    note = root / "note.md"
    note.write_text(NOTE)
    snapshot = concept_snapshot(NOTE)
    with pytest.raises(InvalidRequestError):
        append_okf_verification(
            NOTE,
            subject="checker",
            now=datetime.now(UTC),
            reviewed_snapshot=snapshot,
            actor=actor,
            check=check,
        )
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_verify("note.md", "checker", snapshot, check, actor=actor)
        assert note.read_text() == NOTE
        assert not is_regular_file(tmp_path / "state.receipts.json")
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("actor", "check"),
    [
        ("checker/1.2", "schema"),
        ("process:checker", "x-local"),
        ("human:alice", "human-review"),
    ],
)
def test_explicit_valid_actor_is_recorded(actor: str, check: str) -> None:
    from datetime import UTC, datetime

    from markdown_vault_mcp.okf import append_okf_verification

    result = append_okf_verification(
        NOTE,
        subject="ignored",
        now=datetime.now(UTC),
        reviewed_snapshot=concept_snapshot(NOTE),
        actor=actor,
        check=check,
    )
    metadata = frontmatter.loads(result).metadata
    assert metadata["verified"][0]["by"] == actor
    assert metadata["x-mvmcp"]["reviews"][0]["check"] == check
