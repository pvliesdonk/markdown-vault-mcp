"""Tests for OKF detection and annotation derivation (`okf.py`)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.okf import (
    OKF_STATUS_DEFAULT,
    TRUST_HUMAN,
    TRUST_MACHINE,
    TRUST_UNVERIFIED,
    OkfDetector,
    derive_annotation,
    derive_stale,
    derive_trust_tier,
)

TODAY = dt.date(2026, 8, 7)


def _write_root_index(vault: Path, text: str) -> None:
    (vault / "index.md").write_text(text, encoding="utf-8")


class TestOkfDetector:
    def test_no_marker_auto_inactive(self, tmp_path: Path) -> None:
        state = OkfDetector(tmp_path, mode="auto").state()
        assert state.active is False
        assert state.declared_version is None
        assert state.mode == "auto"

    def test_declared_auto_active(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, '---\nokf_version: "0.2"\n---\n# Bundle\n')
        state = OkfDetector(tmp_path, mode="auto").state()
        assert state.active is True
        assert state.declared_version == "0.2"

    def test_mode_off_ignores_marker(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, '---\nokf_version: "0.2"\n---\n')
        state = OkfDetector(tmp_path, mode="off").state()
        assert state.active is False
        assert state.declared_version is None

    def test_mode_on_forces_active_without_marker(self, tmp_path: Path) -> None:
        state = OkfDetector(tmp_path, mode="on").state()
        assert state.active is True
        assert state.declared_version is None

    def test_unquoted_yaml_scalar_version(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, "---\nokf_version: 0.2\n---\n")
        state = OkfDetector(tmp_path, mode="auto").state()
        assert state.declared_version == "0.2"
        assert state.active is True

    def test_unknown_version_warns_once_and_detects(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_root_index(tmp_path, '---\nokf_version: "9.9"\n---\n')
        detector = OkfDetector(tmp_path, mode="auto")
        with caplog.at_level(logging.WARNING):
            first = detector.state()
            second = detector.state()
        assert first.active is True and second.active is True
        warnings = [r for r in caplog.records if "okf_unknown_version" in r.message]
        assert len(warnings) == 1

    def test_invalid_frontmatter_is_not_detected(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, "---\n: [broken\n---\n")
        assert OkfDetector(tmp_path, mode="auto").state().active is False

    def test_missing_frontmatter_is_not_detected(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, "# Just a heading\n")
        assert OkfDetector(tmp_path, mode="auto").state().active is False

    def test_empty_version_is_not_detected(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, '---\nokf_version: "  "\n---\n')
        state = OkfDetector(tmp_path, mode="auto").state()
        assert state.declared_version is None
        assert state.active is False

    def test_non_scalar_version_is_not_detected(self, tmp_path: Path) -> None:
        _write_root_index(tmp_path, "---\nokf_version: [0.2]\n---\n")
        assert OkfDetector(tmp_path, mode="auto").state().declared_version is None

    def test_unreadable_file_is_not_detected(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_bytes(b"\xff\xfe\x00broken")
        assert OkfDetector(tmp_path, mode="auto").state().active is False

    def test_mid_session_declaration_flips_state(self, tmp_path: Path) -> None:
        detector = OkfDetector(tmp_path, mode="auto")
        assert detector.state().active is False
        _write_root_index(tmp_path, '---\nokf_version: "0.2"\n---\n')
        assert detector.state().active is True

    def test_mode_property(self, tmp_path: Path) -> None:
        assert OkfDetector(tmp_path, mode="on").mode == "on"


class TestVaultIntegration:
    def test_okf_stats_none_without_detector(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.managers.search import SearchManager

        manager = SearchManager(FTSIndex(db_path=":memory:"), tmp_path)
        assert manager.okf_stats() is None

    def test_extension_skipped_when_fields_already_indexed(
        self, tmp_path: Path
    ) -> None:
        from markdown_vault_mcp.okf import OKF_INDEXED_FIELDS
        from markdown_vault_mcp.vault import Vault

        _write_root_index(tmp_path, '---\nokf_version: "0.2"\n---\n')
        vault = Vault(
            source_dir=tmp_path,
            okf_mode="auto",
            indexed_frontmatter_fields=list(OKF_INDEXED_FIELDS),
        )
        try:
            fields = vault.reader.stats().indexed_frontmatter_fields
            assert fields == list(OKF_INDEXED_FIELDS)
        finally:
            vault.close()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, TRUST_UNVERIFIED),
        ({"verified": []}, TRUST_UNVERIFIED),
        ({"verified": "yes"}, TRUST_UNVERIFIED),
        ({"verified": ["human:alice"]}, TRUST_UNVERIFIED),  # malformed entries
        ({"verified": [{"by": "process:ci"}]}, TRUST_MACHINE),
        ({"verified": [{"at": "2026-01-01"}]}, TRUST_MACHINE),
        ({"verified": [{"by": "human:alice"}]}, TRUST_HUMAN),
        (
            {"verified": [{"by": "process:ci"}, {"by": "human:alice"}]},
            TRUST_HUMAN,
        ),
        ({"generated": {"by": "human:alice"}}, TRUST_UNVERIFIED),
    ],
)
def test_derive_trust_tier(metadata: dict, expected: str) -> None:
    assert derive_trust_tier(metadata) == expected


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, False),
        ({"stale_after": dt.date(2026, 8, 6)}, True),
        ({"stale_after": dt.date(2026, 8, 7)}, False),  # strictly before
        ({"stale_after": dt.date(2027, 1, 1)}, False),
        ({"stale_after": dt.datetime(2026, 1, 1, 12, 0)}, True),
        ({"stale_after": "2026-01-01"}, True),
        ({"stale_after": " 2026-01-01 "}, True),
        ({"stale_after": "not-a-date"}, False),
        ({"stale_after": 20260101}, False),
    ],
)
def test_derive_stale(metadata: dict, expected: bool) -> None:
    assert derive_stale(metadata, today=TODAY) is expected


class TestDeriveAnnotation:
    def test_empty_metadata_defaults(self) -> None:
        annotation = derive_annotation({}, today=TODAY)
        assert annotation == {
            "status": OKF_STATUS_DEFAULT,
            "stale": False,
            "trust_tier": TRUST_UNVERIFIED,
        }

    def test_full_metadata(self) -> None:
        annotation = derive_annotation(
            {
                "type": " Playbook ",
                "status": "deprecated",
                "stale_after": "2020-01-01",
                "verified": [{"by": "human:peter"}],
            },
            today=TODAY,
        )
        assert annotation == {
            "type": "Playbook",
            "status": "deprecated",
            "stale": True,
            "trust_tier": TRUST_HUMAN,
        }

    def test_unknown_status_passes_through(self) -> None:
        annotation = derive_annotation({"status": "archived"}, today=TODAY)
        assert annotation["status"] == "archived"

    def test_non_string_type_omitted(self) -> None:
        annotation = derive_annotation({"type": 5}, today=TODAY)
        assert "type" not in annotation

    def test_blank_status_defaults(self) -> None:
        annotation = derive_annotation({"status": "  "}, today=TODAY)
        assert annotation["status"] == OKF_STATUS_DEFAULT

    def test_search_mode_counts_sources(self) -> None:
        annotation = derive_annotation(
            {"sources": [{"resource": "https://a"}, {"resource": "https://b"}]},
            today=TODAY,
        )
        assert annotation["sources_count"] == 2
        assert "sources" not in annotation

    def test_read_mode_includes_sources(self) -> None:
        sources = [{"resource": "https://a", "id": "a"}]
        annotation = derive_annotation(
            {"sources": sources}, today=TODAY, include_sources=True
        )
        assert annotation["sources"] == sources
        assert "sources_count" not in annotation

    def test_empty_or_malformed_sources_omitted(self) -> None:
        for metadata in ({"sources": []}, {"sources": "https://a"}):
            annotation = derive_annotation(metadata, today=TODAY, include_sources=True)
            assert "sources" not in annotation
            assert "sources_count" not in annotation


class TestOkfConfig:
    def test_invalid_mode_rejected(self) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="okf_mode"):
            ContentConfig(okf_mode="banana")

    @pytest.mark.parametrize("mode", ["auto", "off", "on"])
    def test_valid_modes_accepted(self, mode: str) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig

        assert ContentConfig(okf_mode=mode).okf_mode == mode

    def test_from_env_normalizes_case_and_whitespace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from markdown_vault_mcp.config import ProjectConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", " ON ")
        config = ProjectConfig.from_env()
        assert config.okf_mode == "on"
        assert config.content.okf_mode == "on"

    def test_from_env_defaults_to_auto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from markdown_vault_mcp.config import ProjectConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OKF_MODE", raising=False)
        assert ProjectConfig.from_env().content.okf_mode == "auto"


class TestOkfInstructions:
    def test_off_omits_okf_guidance(self) -> None:
        from markdown_vault_mcp._instructions import build_default_instructions

        text = build_default_instructions(read_only=True, okf_mode="off")
        assert "OKF" not in text

    @pytest.mark.parametrize("mode", ["auto", "on"])
    def test_permitting_modes_emit_okf_guidance(self, mode: str) -> None:
        from markdown_vault_mcp._instructions import build_default_instructions

        text = build_default_instructions(read_only=True, okf_mode=mode)
        assert "OKF" in text
        assert "okf_version" in text
        assert "trust tier" in text

    def test_default_omits_okf_guidance(self) -> None:
        from markdown_vault_mcp._instructions import build_default_instructions

        assert "OKF" not in build_default_instructions(read_only=True)
