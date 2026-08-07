"""Tests for OKF detection and annotation derivation (`okf.py`)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.vault import Vault

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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE ", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("No", False),
    ],
)
def test_parse_stale_filter(value: str, expected: bool) -> None:
    from markdown_vault_mcp.okf import parse_stale_filter

    assert parse_stale_filter(value) is expected


def test_parse_stale_filter_rejects_garbage() -> None:
    from markdown_vault_mcp.okf import parse_stale_filter

    with pytest.raises(ValueError, match="stale filter"):
        parse_stale_filter("banana")


@pytest.mark.parametrize(
    ("metadata", "okf_filters", "expected"),
    [
        ({}, {"status": "stable"}, True),  # absent status means stable
        ({"status": "draft"}, {"status": "stable"}, False),
        ({"status": "deprecated"}, {"status": "deprecated"}, True),
        ({"status": "archived"}, {"status": "archived"}, True),  # unknown passes
        ({}, {"stale": "false"}, True),
        ({"stale_after": "2000-01-01"}, {"stale": "true"}, True),
        ({"stale_after": "2999-01-01"}, {"stale": "true"}, False),
        ({}, {"trust_tier": "unverified"}, True),
        (
            {"verified": [{"by": "human:a"}]},
            {"trust_tier": "human-reviewed"},
            True,
        ),
        ({"verified": [{"by": "process:x"}]}, {"trust_tier": "human-reviewed"}, False),
        (
            {"type": "Playbook", "stale_after": "2000-01-01"},
            {"stale": "true", "status": "stable"},
            True,
        ),
        (
            {"status": "deprecated", "stale_after": "2000-01-01"},
            {"stale": "true", "status": "stable"},
            False,
        ),
    ],
)
def test_matches_okf_filters(
    metadata: dict, okf_filters: dict[str, str], expected: bool
) -> None:
    from markdown_vault_mcp.okf import matches_okf_filters

    assert matches_okf_filters(metadata, okf_filters, today=TODAY) is expected


PLAYBOOK_NOTE = """---
type: Playbook
status: deprecated
stale_after: 2000-01-01
verified:
  - by: human:peter
---
# Playbook

Zebra steps.

## Checklist

Zebra checklist details.
"""

PLAIN_NOTE = """# Plain

Zebra notes. See [Playbook](playbook.md).
"""


def _build_filter_vault(root: Path, *, declared: bool) -> None:
    (root / "guides").mkdir(parents=True)
    if declared:
        _write_root_index(root, '---\nokf_version: "0.2"\n---\n# Bundle\n')
    (root / "guides" / "playbook.md").write_text(PLAYBOOK_NOTE, encoding="utf-8")
    (root / "guides" / "plain.md").write_text(PLAIN_NOTE, encoding="utf-8")


class TestOkfFilters:
    def _vault(self, root: Path, **kwargs: object) -> Vault:
        from markdown_vault_mcp.vault import Vault

        vault = Vault(source_dir=root, **kwargs)  # type: ignore[arg-type]
        vault.index.build_index()
        return vault

    def test_keyword_filters_on_declared_vault(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=True)
        vault = self._vault(tmp_path)
        try:
            paths = lambda rs: sorted(r.path for r in rs)  # noqa: E731
            search = vault.reader.search
            assert paths(search("zebra", filters={"stale": "true"})) == [
                "guides/playbook.md"
            ]
            assert paths(search("zebra", filters={"status": "stable"})) == [
                "guides/plain.md"
            ]
            assert paths(search("zebra", filters={"trust_tier": "human-reviewed"})) == [
                "guides/playbook.md"
            ]
            assert paths(
                search("zebra", filters={"type": "Playbook", "stale": "true"})
            ) == ["guides/playbook.md"]
            assert (
                search("zebra", filters={"status": "deprecated", "stale": "false"})
                == []
            )
        finally:
            vault.close()

    def test_filters_inert_without_declaration(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=False)
        vault = self._vault(tmp_path)
        try:
            # Plain tag semantics: no 'stale' tag exists, so no matches —
            # and no error either.
            assert vault.reader.search("zebra", filters={"stale": "true"}) == []
        finally:
            vault.close()

    def test_invalid_stale_value_raises(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=True)
        vault = self._vault(tmp_path)
        try:
            with pytest.raises(ValueError, match="stale filter"):
                vault.reader.search("zebra", filters={"stale": "banana"})
        finally:
            vault.close()

    def test_list_documents_filters(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=True)
        (tmp_path / "guides" / "img.png").write_bytes(b"\x89PNG\r\n")
        vault = self._vault(tmp_path)
        try:
            listing = vault.reader.list_documents(filters={"status": "deprecated"})
            assert [n.path for n in listing] == ["guides/playbook.md"]
            stale = vault.reader.list_documents(filters={"stale": "true"})
            assert [n.path for n in stale] == ["guides/playbook.md"]
            with_attachments = vault.reader.list_documents(include_attachments=True)
            assert any(n.path.endswith(".png") for n in with_attachments)
            filtered = vault.reader.list_documents(
                include_attachments=True, filters={"stale": "false"}
            )
            assert not any(n.path.endswith(".png") for n in filtered)
        finally:
            vault.close()

    def test_semantic_and_hybrid_filters(
        self, tmp_path: Path, mock_provider: object
    ) -> None:
        root = tmp_path / "vault"
        _build_filter_vault(root, declared=True)
        vault = self._vault(
            root,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        try:
            vault.index.build_embeddings()
            for mode in ("semantic", "hybrid"):
                results = vault.reader.search(
                    "zebra", mode=mode, filters={"stale": "false"}
                )
                assert results, mode
                assert all(r.path != "guides/playbook.md" for r in results), mode
            # get_similar has its own split/widen/post-filter call site.
            similar = vault.reader.get_similar(
                "guides/plain.md", filters={"stale": "true"}
            )
            assert [r.path for r in similar] == ["guides/playbook.md"]
            none_similar = vault.reader.get_similar(
                "guides/plain.md",
                filters={"trust_tier": "human-reviewed", "status": "stable"},
            )
            assert none_similar == []
        finally:
            vault.close()

    def test_graph_nodes_carry_note_type_when_declared(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=True)
        vault = self._vault(tmp_path)
        try:
            view = vault.graph.get_neighborhood("guides/plain.md")
            by_id = {n.id: n for n in view.nodes}
            assert by_id["guides/playbook.md"].note_type == "Playbook"
            assert by_id["guides/plain.md"].note_type is None
        finally:
            vault.close()

    def test_filters_plain_without_detector(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.managers.search import SearchManager
        from markdown_vault_mcp.scanner import scan_directory

        _build_filter_vault(tmp_path, declared=True)
        fts = FTSIndex(db_path=":memory:")
        for note in scan_directory(tmp_path):
            fts.upsert_note(note)
        manager = SearchManager(fts, tmp_path)
        # No detector wired: 'stale' is a plain frontmatter key nothing
        # carries, so the listing is empty rather than OKF-evaluated.
        assert manager.list(filters={"stale": "true"}) == []

    def test_path_matches_okf_missing_document(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=True)
        vault = self._vault(tmp_path)
        try:
            manager = vault.reader._search_mgr
            assert manager._path_matches_okf("missing.md", {"stale": "true"}) is False
        finally:
            vault.close()

    def test_graph_nodes_untyped_without_declaration(self, tmp_path: Path) -> None:
        _build_filter_vault(tmp_path, declared=False)
        vault = self._vault(tmp_path)
        try:
            view = vault.graph.get_neighborhood("guides/plain.md")
            assert all(n.note_type is None for n in view.nodes)
        finally:
            vault.close()


def test_graph_view_payload_emits_note_type_only_when_set() -> None:
    from markdown_vault_mcp._vault_apps import _graph_view_payload
    from markdown_vault_mcp.types import GraphNode, GraphView

    view = GraphView(
        nodes=[
            GraphNode(id="a.md", label="A", group="note", folder="", backlink_count=0),
            GraphNode(
                id="b.md",
                label="B",
                group="note",
                folder="",
                backlink_count=1,
                note_type="Playbook",
            ),
        ],
        edges=[],
    )
    payload = _graph_view_payload(view, include_truncated=False)
    assert "note_type" not in payload["nodes"][0]
    assert payload["nodes"][1]["note_type"] == "Playbook"


def _build_audit_vault(root: Path) -> None:
    """A mixed-conformance bundle exercising every audit rule."""
    (root / "guides").mkdir(parents=True)
    (root / "junk").mkdir()
    _write_root_index(root, '---\nokf_version: "0.2"\n---\n# Bundle\n')
    (root / "guides" / "good.md").write_text(
        "---\ntype: Playbook\ntitle: Good\ndescription: A conformant note.\n---\n"
        "# Good\n",
        encoding="utf-8",
    )
    (root / "guides" / "untyped.md").write_text(
        "---\ntitle: Untyped\n---\n# Untyped\nSee [[good]].\n", encoding="utf-8"
    )
    (root / "guides" / "broken.md").write_text(
        "---\n: [broken\n---\n# Broken\n", encoding="utf-8"
    )
    (root / "guides" / "misplaced.md").write_text(
        '---\ntype: Note\nokf_version: "0.2"\n---\n# Misplaced\n', encoding="utf-8"
    )
    (root / "guides" / "odd-status.md").write_text(
        "---\ntype: Note\nstatus: archived\n---\n# Odd\n", encoding="utf-8"
    )
    (root / "guides" / "log.md").write_text(
        "# Log\n\n## 2026-08-07\n\n- **Update**: fine\n", encoding="utf-8"
    )
    (root / "junk" / "log.md").write_text(
        "# Log\n\n## Not A Date\n\n- broken\n", encoding="utf-8"
    )
    (root / "junk" / "excluded.md").write_text("# No type here\n", encoding="utf-8")


class TestOkfAudit:
    def test_mixed_vault_report(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import OkfDetector, audit_bundle

        _build_audit_vault(tmp_path)
        report = audit_bundle(tmp_path, detector=OkfDetector(tmp_path, mode="auto"))
        assert report.active is True
        assert report.declared_version == "0.2"
        # Non-reserved notes: good, untyped, broken, misplaced, odd-status,
        # junk/excluded (not excluded in this run).
        assert report.total_notes == 6
        assert report.conformant_notes == 3  # good, misplaced, odd-status
        assert report.missing_type.count == 2  # untyped, junk/excluded
        assert report.unparseable_frontmatter.examples == ("guides/broken.md",)
        assert report.misplaced_okf_version.examples == ("guides/misplaced.md",)
        assert report.unknown_status.examples == ("guides/odd-status.md",)
        assert report.log_heading_shape.examples == ("junk/log.md",)
        assert report.root_index_missing is False
        assert report.wikilink_files.examples == ("guides/untyped.md",)
        assert report.missing_recommended.count >= 3

    def test_exclude_patterns_whitelist(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import audit_bundle

        _build_audit_vault(tmp_path)
        report = audit_bundle(tmp_path, exclude_patterns=["junk/**"])
        assert report.missing_type.count == 1  # junk/excluded.md skipped
        assert report.log_heading_shape.count == 0  # junk/log.md skipped

    def test_unprunable_exclude_pattern_filters_per_file(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import audit_bundle

        # A mid-name glob cannot be pruned at directory level by
        # iter_markdown_files, so the per-file is_path_excluded check (the
        # correctness layer) must fire.
        (tmp_path / "keep.md").write_text("---\ntype: Note\n---\n# K\n")
        (tmp_path / "drop-excluded.md").write_text("# untyped\n")
        report = audit_bundle(tmp_path, exclude_patterns=["*excluded*"])
        assert report.total_notes == 1
        assert report.missing_type.count == 0

    def test_example_cap(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import audit_bundle

        for i in range(5):
            (tmp_path / f"n{i}.md").write_text("# untyped\n", encoding="utf-8")
        report = audit_bundle(tmp_path, example_cap=2)
        assert report.missing_type.count == 5
        assert len(report.missing_type.examples) == 2

    def test_missing_vault_dir_zero_report(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import audit_bundle

        report = audit_bundle(tmp_path / "nope")
        assert report.total_notes == 0
        assert report.root_index_missing is True
        assert report.mode == "off"
        assert report.active is False

    def test_undeclared_vault_reports_inactive(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import OkfDetector, audit_bundle

        (tmp_path / "note.md").write_text("---\ntype: Note\n---\n# N\n")
        report = audit_bundle(tmp_path, detector=OkfDetector(tmp_path, mode="auto"))
        assert report.active is False
        assert report.conformant_notes == 1
        assert report.root_index_missing is True

    def test_unreadable_file_skipped(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.okf import audit_bundle

        (tmp_path / "good.md").write_text("---\ntype: Note\n---\n# G\n")
        (tmp_path / "binary.md").write_bytes(b"\xff\xfe\x00broken")
        report = audit_bundle(tmp_path)
        assert report.total_notes == 1
        assert report.conformant_notes == 1

    def test_facet_without_wired_audit_raises(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.facets.reader import ReaderFacet
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.managers.search import SearchManager

        facet = ReaderFacet(
            search_mgr=SearchManager(FTSIndex(db_path=":memory:"), tmp_path),
            doc_mgr=None,  # type: ignore[arg-type] — unused by okf_validate
            git_query_mgr=None,  # type: ignore[arg-type] — unused
            require_built=lambda: None,
        )
        with pytest.raises(RuntimeError, match="okf_validate"):
            facet.okf_validate()

    def test_vault_facet_wiring(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        _build_audit_vault(tmp_path)
        vault = Vault(source_dir=tmp_path, exclude_patterns=["junk/**"])
        try:
            report = vault.reader.okf_validate()
            assert report.active is True
            assert report.missing_type.count == 1
        finally:
            vault.close()
