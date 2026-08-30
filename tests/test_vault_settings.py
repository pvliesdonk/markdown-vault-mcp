"""Settings-first Vault construction (#1158).

Pins the dual-mode contract mechanically:

- a signature drift-guard asserts every :class:`VaultSettings` field mirrors a
  same-named ``Vault.__init__`` keyword with an identical default (including
  the two deliberate drifts — ``chunk_overlap_words=0`` vs SearchConfig's 40,
  and the ``read_only=True`` library default vs the server env default);
- an equivalence test constructs one vault per mode from the same values and
  compares the resolved wiring;
- mixing ``settings=`` with a non-default config-derived legacy kwarg is an
  explicit ``ValueError``;
- ``VaultSettings.from_project_config`` absorbs the historical
  ``to_vault_kwargs`` renames and weight-map conversions.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any, ClassVar

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_instances,
    to_vault_kwargs,
    to_vault_settings,
)
from markdown_vault_mcp.vault import Vault

# The Vault.__init__ keywords that are NOT config-derived and therefore have
# no VaultSettings field: the root, the five collaborators, and settings.
_NON_SETTINGS_PARAMS = {
    "self",
    "source_dir",
    "embedding_provider",
    "summarizer",
    "git_strategy",
    "on_write",
    "chunk_strategy",
    "settings",
}


class TestSignatureDriftGuard:
    """VaultSettings fields and Vault legacy kwargs must not drift apart."""

    def test_fields_mirror_init_defaults(self) -> None:
        """Every field has a same-named Vault kwarg with the same default."""
        params = inspect.signature(Vault.__init__).parameters
        for field in dataclasses.fields(VaultSettings):
            assert field.name in params, (
                f"VaultSettings.{field.name} has no matching Vault.__init__ kwarg"
            )
            assert params[field.name].default == field.default, (
                f"default drift on {field.name}: Vault.__init__ has "
                f"{params[field.name].default!r}, VaultSettings has "
                f"{field.default!r}"
            )

    def test_every_config_derived_kwarg_has_a_field(self) -> None:
        """The 31 config-derived Vault kwargs are exactly the settings fields."""
        params = set(inspect.signature(Vault.__init__).parameters)
        config_derived = params - _NON_SETTINGS_PARAMS
        field_names = {field.name for field in dataclasses.fields(VaultSettings)}
        assert config_derived == field_names
        assert len(field_names) == 31

    def test_deliberate_default_drifts_are_preserved(self) -> None:
        """The two known library-vs-server default drifts stay pinned."""
        settings = VaultSettings()
        # Library chunker default: no overlap (SearchConfig defaults to 40).
        assert settings.chunk_overlap_words == 0
        # Library fail-safe default: read-only (the server env default is
        # False, #1113); see the Vault docstring rationale.
        assert settings.read_only is True


class TestDualModeEquivalence:
    """Legacy kwargs and settings-first construction wire identically."""

    _VALUES: ClassVar[dict[str, Any]] = {
        "read_only": False,
        "write_protect_existing": True,
        "indexed_frontmatter_fields": ["cluster"],
        "required_frontmatter": ["title"],
        "exclude_patterns": [".trash/**"],
        "attachment_extensions": ["pdf"],
        "max_attachment_size_mb": 2.5,
        "max_note_read_bytes": 1024,
        "chunks_per_file": 3,
        "snippet_words": 50,
        "length_downweight_alpha": 0.5,
        "default_search_mode": "keyword",
        "max_chunk_words": 100,
        "max_chunk_chars_override": 900,
        "chunk_overlap_words": 10,
        "summarize_max_notes": 7,
        "summarize_max_input_chars": 5000,
        "title_field": "name",
        "searchable_frontmatter_fields": ["cluster"],
        "embed_context": True,
        "embedding_batch_size": 8,
        "folder_weights": {"notes": 2.0},
        "fts_weights": {"title": 3.0},
        "conventions_file": "_house_rules.md",
    }

    _COMPARED_ATTRS = (
        "_index_path",
        "_embeddings_path",
        "_read_only",
        "_write_protect_existing",
        "_state_path",
        "_indexed_frontmatter_fields",
        "_required_frontmatter",
        "_git_pull_interval_s",
        "_exclude_patterns",
        "_attachment_extensions",
        "_max_attachment_size_mb",
        "_max_note_read_bytes",
        "_max_chunk_chars_override",
        "_summarize_max_notes",
        "_summarize_max_input_chars",
        "_title_field",
        "_searchable_frontmatter_fields",
        "_embedding_batch_size",
    )

    def test_same_values_resolve_to_same_wiring(self, tmp_path: Path) -> None:
        values = dict(self._VALUES)
        values["index_path"] = tmp_path / "idx.db"
        values["state_path"] = tmp_path / "state.json"
        legacy = Vault(source_dir=tmp_path, **values)
        settings_first = Vault(source_dir=tmp_path, settings=VaultSettings(**values))
        try:
            for attr in self._COMPARED_ATTRS:
                assert getattr(legacy, attr) == getattr(settings_first, attr), attr
            # Chunker construction consumes the same settings values.
            assert type(legacy._chunk_strategy) is type(settings_first._chunk_strategy)
            assert vars(legacy._chunk_strategy) == vars(settings_first._chunk_strategy)
            # Derived exclude patterns include the conventions-file forms.
            assert legacy.exclude_patterns == [
                ".trash/**",
                "_house_rules.md",
                "**/_house_rules.md",
            ]
        finally:
            legacy.close()
            settings_first.close()

    def test_default_state_path_matches(self, tmp_path: Path) -> None:
        """Both modes derive the same default state path under the root."""
        legacy = Vault(source_dir=tmp_path)
        settings_first = Vault(source_dir=tmp_path, settings=VaultSettings())
        try:
            expected = tmp_path / ".markdown_vault_mcp" / "state.json"
            assert legacy._state_path == expected
            assert settings_first._state_path == expected
        finally:
            legacy.close()
            settings_first.close()


class TestConflictRejection:
    """settings= combined with non-default legacy kwargs is an error."""

    def test_non_default_legacy_kwarg_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="read_only"):
            Vault(source_dir=tmp_path, settings=VaultSettings(), read_only=False)

    def test_error_names_every_conflicting_kwarg(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"max_note_read_bytes.*title_field"):
            Vault(
                source_dir=tmp_path,
                settings=VaultSettings(),
                title_field="name",
                max_note_read_bytes=1,
            )

    def test_default_legacy_values_do_not_conflict(self, tmp_path: Path) -> None:
        """Explicitly passing a default value alongside settings is accepted."""
        vault = Vault(
            source_dir=tmp_path, settings=VaultSettings(read_only=False), read_only=True
        )
        try:
            assert vault._read_only is False  # settings wins; default is inert
        finally:
            vault.close()

    def test_collaborator_kwargs_are_not_conflicts(self, tmp_path: Path) -> None:
        """The five non-config-derived kwargs combine freely with settings."""
        vault = Vault(
            source_dir=tmp_path, settings=VaultSettings(), chunk_strategy="whole"
        )
        try:
            from markdown_vault_mcp.scanner import WholeDocumentChunker

            assert isinstance(vault._chunk_strategy, WholeDocumentChunker)
        finally:
            vault.close()


class TestSettingsDerivations:
    """The pure derivation methods own the construction-time normalisation."""

    def test_effective_indexed_fields_extends_when_okf_active(self) -> None:
        settings = VaultSettings(indexed_frontmatter_fields=["cluster", "status"])
        assert settings.effective_indexed_fields(okf_active=False) == [
            "cluster",
            "status",
        ]
        assert settings.effective_indexed_fields(okf_active=True) == [
            "cluster",
            "status",
            "type",
            "stale_after",
        ]

    def test_effective_exclude_patterns_passthrough_without_conventions(self) -> None:
        assert VaultSettings(conventions_file=None).effective_exclude_patterns() is None
        settings = VaultSettings(conventions_file=None, exclude_patterns=(".git/**",))
        assert settings.effective_exclude_patterns() == (".git/**",)

    def test_effective_exclude_patterns_rejects_metacharacters(self) -> None:
        settings = VaultSettings(conventions_file="a*.md")
        with pytest.raises(ValueError, match="fnmatch metacharacters"):
            settings.effective_exclude_patterns()

    def test_effective_state_path_prefers_explicit(self, tmp_path: Path) -> None:
        explicit = tmp_path / "elsewhere" / "s.json"
        assert (
            VaultSettings(state_path=explicit).effective_state_path(tmp_path)
            == explicit
        )
        assert (
            VaultSettings().effective_state_path(tmp_path)
            == tmp_path / ".markdown_vault_mcp" / "state.json"
        )


class TestFromProjectConfig:
    """from_project_config absorbs the renames and conversions."""

    def test_renames_and_weight_conversion(self) -> None:
        config = ProjectConfig(
            source_dir=Path("/tmp/vault"),
            searchable_fields=["cluster"],
            default_search_mode="keyword",
            folder_weights={"notes/": 2.0},
            fts_weights={"title": 3.0},
        )
        settings = VaultSettings.from_project_config(config)
        # Renames: searchable_frontmatter -> searchable_frontmatter_fields,
        # default_mode -> default_search_mode.
        assert settings.searchable_frontmatter_fields == ("cluster",)
        assert settings.default_search_mode == "keyword"
        # Weight maps: frozen config tuples back to plain dicts (#639).
        assert settings.folder_weights == {"notes": 2.0}
        assert settings.fts_weights == {"title": 3.0}

    def test_chunk_char_cap_derivation(self) -> None:
        config = ProjectConfig(source_dir=Path("/tmp/vault"))
        # No provider context: bounded ceiling fallback (#790).
        assert VaultSettings.from_project_config(config).max_chunk_chars == 1500
        derived = VaultSettings.from_project_config(
            config, embedding_context_length=512
        )
        assert derived.max_chunk_chars == round(512 * 2.8)

    def test_git_pull_interval_resolution(self, tmp_path: Path) -> None:
        """Only remote-configured modes resolve a non-zero pull interval."""
        commit_only = ProjectConfig(source_dir=tmp_path, git_pull_interval_s=123)
        assert VaultSettings.from_project_config(commit_only).git_pull_interval_s == 0
        token_mode = ProjectConfig(
            source_dir=tmp_path, git_token="ghp_secret", git_pull_interval_s=123
        )
        assert VaultSettings.from_project_config(token_mode).git_pull_interval_s == 123


class TestAssemblyBridges:
    """to_vault_settings / to_vault_instances and the legacy kwargs bridge."""

    def test_settings_and_instances_agree_on_pull_interval(
        self, tmp_path: Path
    ) -> None:
        """The pure settings derivation matches the git-assembly resolution."""
        for config in (
            ProjectConfig(source_dir=tmp_path, git_pull_interval_s=42),
            ProjectConfig(
                source_dir=tmp_path, git_token="ghp_secret", git_pull_interval_s=42
            ),
        ):
            instances = to_vault_instances(config)
            settings = to_vault_settings(config, instances=instances)
            assert settings.git_pull_interval_s == instances.git_pull_interval_s

    def test_to_vault_kwargs_is_the_settings_explosion(self, tmp_path: Path) -> None:
        """The deprecated bridge reproduces settings + instances exactly."""
        config = ProjectConfig(
            source_dir=tmp_path,
            read_only=False,
            exclude=[".obsidian/**"],
        )
        instances = to_vault_instances(config)
        settings = to_vault_settings(config, instances=instances)
        kwargs = to_vault_kwargs(config)
        for field in dataclasses.fields(VaultSettings):
            if field.name in ("summarize_max_notes", "summarize_max_input_chars"):
                # Historical dict shape: only present with a summarizer.
                assert field.name not in kwargs
                continue
            assert kwargs[field.name] == getattr(settings, field.name), field.name
        assert kwargs["source_dir"] == config.source_dir
        assert "embedding_provider" not in kwargs
        assert kwargs["on_write"] is kwargs["git_strategy"]

    def test_replace_override_pattern(self, tmp_path: Path) -> None:
        """The CLI-style dataclasses.replace override lands in the vault."""
        config = ProjectConfig(source_dir=tmp_path)
        settings = dataclasses.replace(
            to_vault_settings(config), index_path=tmp_path / "cli.db"
        )
        vault = Vault(source_dir=tmp_path, settings=settings)
        try:
            assert vault._index_path == tmp_path / "cli.db"
        finally:
            vault.close()


class TestServiceStart:
    """domain.Service.start builds settings+instances and uses them as oracle."""

    async def test_start_without_embedding_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.domain import Service, set_vault_singleton

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "n.md").write_text("# N\n", encoding="utf-8")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", raising=False)
        service = Service(
            ProjectConfig(
                source_dir=vault_dir,
                index_path=tmp_path / "fts.db",
                state_path=tmp_path / "s.json",
            )
        )
        try:
            await service.start()
            assert service.vault._embedding_provider is None
            assert service.vault.index.embeddings_status()["available"] is False
        finally:
            await service.stop()
            set_vault_singleton(None)

    async def test_start_with_embedding_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp import domain as domain_mod
        from markdown_vault_mcp.domain import Service, set_vault_singleton
        from tests.conftest import MockEmbeddingProvider

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "n.md").write_text("# N\n", encoding="utf-8")
        provider = MockEmbeddingProvider()
        original_to_instances = domain_mod.to_vault_instances
        monkeypatch.setattr(
            domain_mod,
            "to_vault_instances",
            lambda config: dataclasses.replace(
                original_to_instances(config), embedding_provider=provider
            ),
        )
        service = Service(
            ProjectConfig(
                source_dir=vault_dir,
                index_path=tmp_path / "fts.db",
                state_path=tmp_path / "s.json",
                embeddings_path=tmp_path / "vectors",
            )
        )
        try:
            await service.start()
            assert service.vault._embedding_provider is provider
        finally:
            await service.stop()
            set_vault_singleton(None)
