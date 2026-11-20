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
        section = vault.reader.okf_stats()
        if declared:
            assert section is not None
            assert "capabilities" not in section
            assert section["write_configured"] is write
            assert section["write_effective"] is effective
        else:
            assert section is None
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
            (False, "off", False, "off", (".",)),
            [(False, None), (False, None), (False, None), (False, None)],
        ),
        (
            (False, False, False),
            (True, "elicit", True, "writes", ("**",)),
            [(False, "okf_inactive")] * 4,
        ),
        (
            (False, False, True),
            (True, "elicit", True, "writes", ("**",)),
            [
                (False, "okf_inactive"),
                (True, None),
                (False, "okf_inactive"),
                (False, "okf_inactive"),
            ],
        ),
        (
            (True, False, False),
            (True, "trust-auth", True, "events", ("guides",)),
            [
                (True, None),
                (True, None),
                (True, None),
                (False, "not_implemented"),
            ],
        ),
        (
            (True, False, False),
            (False, "elicit", True, "writes", ()),
            [
                (False, None),
                (True, None),
                (False, "not_designated"),
                (False, "not_designated"),
            ],
        ),
        (
            (True, False, False),
            (False, "prompt", False, "write", (".",)),
            [
                (False, None),
                (False, "not_implemented"),
                (False, None),
                (False, "not_implemented"),
            ],
        ),
        (
            (True, True, False),
            (False, "off", True, "writes", ("guides",)),
            [(False, "read_only")] * 4,
        ),
        (
            (True, False, False),
            (False, "off", True, "writes", ("guides",)),
            [(False, None), (False, None), (True, None), (True, None)],
        ),
    ],
)
def test_capability_report_matrix(
    tmp_path: Path,
    flags: tuple[bool, bool, bool],
    caps: tuple[bool, str, bool, str, tuple[str, ...]],
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
@pytest.mark.parametrize("transport", ["local", "mcp"])
async def test_stats_uses_one_detection_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: bool, transport: str
) -> None:
    from markdown_vault_mcp._cli_read import _facet_payload
    from markdown_vault_mcp.facets.reader import ReaderFacet

    index = tmp_path / "index.md"
    declaration = '---\nokf_version: "0.2"\n---\n'
    if declared:
        index.write_text(declaration)
    original = ReaderFacet.okf_stats

    def change_declaration(facet: ReaderFacet) -> dict | None:
        section = original(facet)
        if declared:
            index.unlink()
        else:
            index.write_text(declaration)
        return section

    monkeypatch.setattr(ReaderFacet, "okf_stats", change_declaration)
    if transport == "local":
        vault = Vault(
            source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_write=True)
        )
        try:
            payload = _facet_payload(vault, "stats", {}, vault.reader.stats())
        finally:
            vault.close()
    else:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
        async with Client(make_server()) as client:
            payload = (await client.call_tool("stats", {})).structured_content
    section = payload["okf"]
    assert section["capabilities"]["index"]["effective"] is declared
    if declared:
        assert section["declared_version"] == "0.2"
        assert section["write_effective"] is True
    else:
        assert set(section) == {"capabilities"}
        assert section["capabilities"]["index"]["reason"] == "okf_inactive"
