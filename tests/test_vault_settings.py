"""Settings-only Vault construction and typed server assembly (#1225)."""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_instances,
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


class TestConstructorContract:
    """The public constructor accepts settings and explicit collaborators only."""

    def test_only_root_settings_and_collaborators(self) -> None:
        params = inspect.signature(Vault.__init__).parameters
        assert set(params) == _NON_SETTINGS_PARAMS
        assert all(
            param.kind is inspect.Parameter.KEYWORD_ONLY
            for name, param in params.items()
            if name != "self"
        )
        assert params["source_dir"].default is inspect.Parameter.empty
        assert params["settings"].default is None

    @pytest.mark.parametrize("explicit_settings", [False, True])
    @pytest.mark.parametrize(
        "field", dataclasses.fields(VaultSettings), ids=lambda f: f.name
    )
    def test_removed_keywords_rejected_even_at_defaults(
        self, tmp_path: Path, explicit_settings: bool, field: dataclasses.Field[Any]
    ) -> None:
        kwargs: dict[str, Any] = {field.name: field.default}
        if explicit_settings:
            kwargs["settings"] = VaultSettings()
        with pytest.raises(
            TypeError, match=f"unexpected keyword argument '{field.name}'"
        ):
            Vault(source_dir=tmp_path, **kwargs)

    @pytest.mark.parametrize("settings", [None, VaultSettings()])
    def test_library_defaults_preserved(
        self, tmp_path: Path, settings: VaultSettings | None
    ) -> None:
        from markdown_vault_mcp.exceptions import ReadOnlyError

        vault = Vault(source_dir=tmp_path, settings=settings)
        try:
            with pytest.raises(ReadOnlyError):
                vault.writer.write("note.md", "body")
            assert vault._chunk_strategy.chunk_overlap_words == 0
            assert vault._state_path == tmp_path / ".markdown_vault_mcp" / "state.json"
        finally:
            vault.close()

    def test_collaborator_combines_with_settings(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.scanner import WholeDocumentChunker

        vault = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(read_only=False),
            chunk_strategy="whole",
        )
        try:
            vault.writer.write("note.md", "body")
            assert vault.reader.read("note.md").content == "body"
            assert isinstance(vault._chunk_strategy, WholeDocumentChunker)
        finally:
            vault.close()

    def test_removed_bridge_is_not_importable(self) -> None:
        import markdown_vault_mcp.config as config
        import markdown_vault_mcp.config_sections._assembly as assembly

        assert not hasattr(config, "to_vault_kwargs")
        assert not hasattr(assembly, "to_vault_kwargs")


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
    """Typed settings and collaborator assembly."""

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
