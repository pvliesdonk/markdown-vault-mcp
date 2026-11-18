"""OKF write-state reporting reflects the configured and live gates."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("declared", "write", "read_only", "effective"),
    [
        (True, True, False, True),
        (True, False, False, False),
        (True, True, True, False),
        (False, True, False, False),
    ],
)
def test_write_state_uses_vault_settings(
    tmp_path: Path, declared: bool, write: bool, read_only: bool, effective: bool
) -> None:
    if declared:
        (tmp_path / "index.md").write_text(
            '---\nokf_version: "0.2"\n---\n', encoding="utf-8"
        )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=read_only, okf_mode="auto", okf_write=write),
    )
    try:
        state = vault.okf.state()
        assert state.active is declared
        assert vault.okf_write_state(active=state.active) == (write, effective)
        if declared:
            section = vault.reader.okf_stats()
            assert section is not None
            assert section["write_configured"] is write
            assert section["write_effective"] is effective
    finally:
        vault.close()


async def test_mcp_reports_write_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n', encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        config = json.loads((await client.read_resource("config://vault"))[0].text)
        stats = (await client.call_tool("stats", {})).structured_content
    assert config["okf_write_configured"] is True
    assert config["okf_write_effective"] is False
    assert stats["okf"]["write_configured"] is True
    assert stats["okf"]["write_effective"] is False


@pytest.mark.parametrize(
    ("flags", "caps", "expected"),
    [
        (
            (False, False, False),
            (False, "off", False, "off", (".",), False),
            [(False, None), (False, None), (False, None), (False, None)],
        ),
        (
            (False, True, False),
            (False, "off", False, "off", (), False),
            [(False, "read_only")] * 4,
        ),
        (
            (False, False, False),
            (True, "elicit", True, "writes", ("**",), False),
            [(False, "okf_inactive")] * 4,
        ),
        (
            (False, False, True),
            (True, "elicit", True, "writes", ("**",), False),
            [
                (False, "okf_inactive"),
                (True, None),
                (False, "okf_inactive"),
                (False, "okf_inactive"),
            ],
        ),
        (
            (True, False, False),
            (True, "trust-auth", True, "events", ("guides",), False),
            [
                (False, "not_implemented"),
                (True, None),
                (True, None),
                (False, "not_implemented"),
            ],
        ),
        (
            (True, False, False),
            (False, "elicit", True, "writes", (), False),
            [
                (False, None),
                (True, None),
                (False, "not_designated"),
                (False, "not_designated"),
            ],
        ),
        (
            (True, False, False),
            (False, "prompt", False, "off", (".",), False),
            [(False, None), (False, "not_implemented"), (False, None), (False, None)],
        ),
        (
            (True, False, False),
            (False, "off", False, "write", (".",), False),
            [(False, None), (False, None), (False, None), (False, "not_implemented")],
        ),
        (
            (False, False, False),
            (False, "prompt", False, "write", (".",), False),
            [
                (False, None),
                (False, "okf_inactive"),
                (False, None),
                (False, "okf_inactive"),
            ],
        ),
        (
            (True, True, False),
            (False, "prompt", False, "write", (".",), False),
            [(False, "read_only")] * 4,
        ),
        (
            (True, False, False),
            (False, "off", True, "writes", ("guides",), False),
            [(False, None), (False, None), (True, None), (True, None)],
        ),
    ],
)
def test_capability_report_matrix(
    tmp_path: Path,
    flags: tuple[bool, bool, bool],
    caps: tuple[bool, str, bool, str, tuple[str, ...], bool],
    expected: list[tuple[bool, str | None]],
) -> None:
    from markdown_vault_mcp.okf_capabilities import OkfCapabilities

    declared, read_only, alias = flags
    if declared:
        (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=read_only,
            okf_write=alias,
            okf_capabilities=OkfCapabilities(*caps),
        ),
    )
    try:
        report = vault.okf_capability_report(active=vault.okf.state().active)
        assert report["folders"] == list(caps[4])
        for name, configured, (effective, reason) in zip(
            ("author", "verify", "index", "log"), caps[:4], expected, strict=True
        ):
            assert report[name] == {
                "configured": configured,
                "effective": effective,
                "reason": reason,
            }
    finally:
        vault.close()


@pytest.mark.parametrize("declared", [False, True])
async def test_mcp_capability_report_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: bool
) -> None:
    if declared:
        (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    async with Client(make_server()) as client:
        config = json.loads((await client.read_resource("config://vault"))[0].text)
        stats = (await client.call_tool("stats", {})).structured_content
    assert stats["okf"]["capabilities"] == config["okf_capabilities"]
    if not declared:
        assert set(stats["okf"]) == {"capabilities"}
        assert config["okf_capabilities"]["verify"]["effective"] is True
        assert config["okf_capabilities"]["index"]["reason"] == "okf_inactive"


def test_capabilities_follow_live_declaration(tmp_path: Path) -> None:
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(okf_write=True, read_only=False)
    )
    try:
        assert (
            vault.okf_capability_report(active=vault.okf.state().active)["index"][
                "reason"
            ]
            == "okf_inactive"
        )
        (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
        assert (
            vault.okf_capability_report(active=vault.okf.state().active)["index"][
                "effective"
            ]
            is True
        )
    finally:
        vault.close()


@pytest.mark.parametrize("declared", [False, True])
def test_library_okf_stats_preserves_original_shape(
    tmp_path: Path, declared: bool
) -> None:
    if declared:
        (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        section = vault.reader.okf_stats()
        if not declared:
            assert section is None
        else:
            assert section is not None
            assert set(section) == {
                "mode",
                "declared_version",
                "types",
                "untyped_count",
                "status",
                "trust",
                "stale_count",
                "reserved_count",
                "write_configured",
                "write_effective",
            }
    finally:
        vault.close()
