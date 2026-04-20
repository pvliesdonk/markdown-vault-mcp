"""Tests for config.py — env var loading and auth builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.config import (
    CollectionConfig,
    build_bearer_auth,
    build_oidc_auth,
    build_remote_auth,
    load_config,
    resolve_auth_mode,
)


class TestParseHelpers:
    """Test boolean and list parsing edge cases via load_config."""

    def test_bool_true_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "YES", " true "):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is True, f"Expected True for {val!r}"

    def test_bool_false_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "False", "0", "no", "anything"):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = load_config()
            assert config.read_only is False, f"Expected False for {val!r}"


class TestLoadConfig:
    def test_missing_source_dir_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", raising=False)
        with pytest.raises(ValueError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
            load_config()

    def test_minimal_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        # Clear all optional vars
        for var in (
            "MARKDOWN_VAULT_MCP_READ_ONLY",
            "MARKDOWN_VAULT_MCP_INDEX_PATH",
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
            "MARKDOWN_VAULT_MCP_STATE_PATH",
            "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
            "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
            "MARKDOWN_VAULT_MCP_EXCLUDE",
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL",
            "MARKDOWN_VAULT_MCP_GIT_USERNAME",
            "MARKDOWN_VAULT_MCP_GIT_TOKEN",
            "MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S",
        ):
            monkeypatch.delenv(var, raising=False)

        config = load_config()

        assert config.source_dir == Path("/tmp/vault")
        assert config.read_only is True  # default
        assert config.index_path is None
        assert config.embeddings_path is None
        assert config.state_path is None
        assert config.indexed_frontmatter_fields is None
        assert config.required_frontmatter is None
        assert config.exclude_patterns is None
        assert config.git_repo_url is None
        assert config.git_username == "x-access-token"
        assert config.git_token is None
        assert config.git_pull_interval_s == 600
        assert config.templates_folder == "_templates"

    def test_full_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/data/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", "/data/index.db")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", "/data/embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", "/data/state.json")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "cluster, topics")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title,cluster")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", ".obsidian/**, .trash/**")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://github.com/acme/vault.git"
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_USERNAME", "oauth2")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "ghp_test123")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "300")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates")

        config = load_config()

        assert config.source_dir == Path("/data/vault")
        assert config.read_only is False
        assert config.index_path == Path("/data/index.db")
        assert config.embeddings_path == Path("/data/embeddings")
        assert config.state_path == Path("/data/state.json")
        assert config.indexed_frontmatter_fields == ["cluster", "topics"]
        assert config.required_frontmatter == ["title", "cluster"]
        assert config.exclude_patterns == [".obsidian/**", ".trash/**"]
        assert config.git_repo_url == "https://github.com/acme/vault.git"
        assert config.git_username == "oauth2"
        assert config.git_token == "ghp_test123"
        assert config.git_pull_interval_s == 300
        assert config.templates_folder == "Templates"

    def test_git_username_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_USERNAME", raising=False)
        config = load_config()
        assert config.git_username == "x-access-token"

    def test_templates_folder_trailing_slash_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates/")
        config = load_config()
        assert config.templates_folder == "Templates"

    def test_templates_folder_backslashes_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates\\Notes\\")
        config = load_config()
        assert config.templates_folder == "Templates/Notes"

    def test_templates_folder_slash_only_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "/")
        config = load_config()
        assert config.templates_folder == "_templates"

    def test_token_without_repo_url_logs_deprecation(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "ghp_legacy")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_REPO_URL", raising=False)
        _ = load_config()
        assert "legacy mode is deprecated" in caplog.text

    def test_invalid_pull_interval_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "nope")
        config = load_config()
        assert config.git_pull_interval_s == 600

    def test_negative_pull_interval_disables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "-5")
        config = load_config()
        assert config.git_pull_interval_s == 0

    def test_comma_separated_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", " a , b , c ")
        config = load_config()
        assert config.indexed_frontmatter_fields == ["a", "b", "c"]

    def test_empty_comma_list_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "")
        config = load_config()
        assert config.indexed_frontmatter_fields is None


class TestToCollectionKwargs:
    def test_includes_exclude_patterns(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            exclude_patterns=[".obsidian/**"],
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["exclude_patterns"] == [".obsidian/**"]
        assert kwargs["source_dir"] == Path("/tmp/vault")

    def test_excludes_git_token(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_secret",
        )
        kwargs = config.to_collection_kwargs()
        assert "git_token" not in kwargs

    def test_includes_all_collection_params(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            read_only=False,
            index_path=Path("/tmp/index.db"),
            embeddings_path=Path("/tmp/emb"),
            state_path=Path("/tmp/state.json"),
            indexed_frontmatter_fields=["cluster"],
            required_frontmatter=["title"],
            exclude_patterns=[".obsidian/**"],
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["source_dir"] == Path("/tmp/vault")
        assert kwargs["read_only"] is False
        assert kwargs["index_path"] == Path("/tmp/index.db")
        assert kwargs["embeddings_path"] == Path("/tmp/emb")
        assert kwargs["state_path"] == Path("/tmp/state.json")
        assert kwargs["indexed_frontmatter_fields"] == ["cluster"]
        assert kwargs["required_frontmatter"] == ["title"]
        assert kwargs["exclude_patterns"] == [".obsidian/**"]
        assert kwargs["attachment_extensions"] is None
        assert kwargs["max_attachment_size_mb"] == 10.0
        assert kwargs["git_pull_interval_s"] == 0
        assert "git_strategy" in kwargs
        assert "on_write" in kwargs

    def test_managed_mode_wires_pull_and_on_write(self, tmp_path: Path) -> None:
        import subprocess

        bare = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )
        source_dir = tmp_path / "vault"
        source_dir.mkdir()

        config = CollectionConfig(
            source_dir=source_dir,
            git_repo_url=str(bare),
            git_token="ghp_secret",
            git_pull_interval_s=123,
        )
        kwargs = config.to_collection_kwargs()
        assert kwargs["git_pull_interval_s"] == 123
        assert "git_strategy" in kwargs
        assert "on_write" in kwargs


class TestGitCommitterConfig:
    """Tests for git committer identity configuration."""

    def test_default_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() uses default git_commit_name when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", raising=False)
        config = load_config()
        assert config.git_commit_name == "markdown-vault-mcp"

    def test_default_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() uses default git_commit_email when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", raising=False)
        config = load_config()
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_override_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "MyBot")
        config = load_config()
        assert config.git_commit_name == "MyBot"

    def test_override_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "bot@example.com")
        config = load_config()
        assert config.git_commit_email == "bot@example.com"

    def test_both_git_committer_vars_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() reads both GIT_COMMIT_NAME and GIT_COMMIT_EMAIL together."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "DeployBot")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "deploy@corp.local")
        config = load_config()
        assert config.git_commit_name == "DeployBot"
        assert config.git_commit_email == "deploy@corp.local"

    def test_empty_git_commit_name_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default when GIT_COMMIT_NAME is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "")
        config = load_config()
        assert config.git_commit_name == "markdown-vault-mcp"

    def test_empty_git_commit_email_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default when GIT_COMMIT_EMAIL is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "")
        config = load_config()
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_defaults(self) -> None:
        """CollectionConfig has correct default committer values."""
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.git_commit_name == "markdown-vault-mcp"
        assert config.git_commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_custom_values(self) -> None:
        """CollectionConfig accepts custom committer name and email."""
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_commit_name="CI",
            git_commit_email="ci@example.com",
        )
        assert config.git_commit_name == "CI"
        assert config.git_commit_email == "ci@example.com"

    def test_to_collection_kwargs_includes_commit_identity(self) -> None:
        """to_collection_kwargs() passes commit identity to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_test",
            git_commit_name="TestBot",
            git_commit_email="test@example.com",
        )
        kwargs = config.to_collection_kwargs()

        assert "on_write" in kwargs
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert kwargs["git_strategy"] is strategy
        assert strategy._commit_name == "TestBot"
        assert strategy._commit_email == "test@example.com"

    def test_to_collection_kwargs_with_default_identity(self) -> None:
        """to_collection_kwargs() uses defaults when no custom identity is set."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()

        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._commit_name == "markdown-vault-mcp"
        assert strategy._commit_email == "noreply@markdown-vault-mcp"


class TestAttachmentConfig:
    """Tests for attachment extension and size limit configuration."""

    def test_default_attachment_extensions_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() returns None attachment_extensions when env var not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", raising=False)
        config = load_config()
        assert config.attachment_extensions is None

    def test_attachment_extensions_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses ATTACHMENT_EXTENSIONS as comma-separated list."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png,docx")
        config = load_config()
        assert config.attachment_extensions == ["pdf", "png", "docx"]

    def test_attachment_extensions_wildcard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses ATTACHMENT_EXTENSIONS=* as ['*']."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "*")
        config = load_config()
        assert config.attachment_extensions == ["*"]

    def test_attachment_extensions_empty_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() returns None when ATTACHMENT_EXTENSIONS is empty."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "")
        config = load_config()
        assert config.attachment_extensions is None

    def test_default_max_attachment_size_mb(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults max_attachment_size_mb to 10.0."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", raising=False)
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_max_attachment_size_mb_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses MAX_ATTACHMENT_SIZE_MB from env var."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "25.5")
        config = load_config()
        assert config.max_attachment_size_mb == 25.5

    def test_max_attachment_size_mb_zero_disables_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() accepts 0 as a valid value for MAX_ATTACHMENT_SIZE_MB."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0")
        config = load_config()
        assert config.max_attachment_size_mb == 0.0

    def test_max_attachment_size_mb_invalid_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to 10.0 for invalid MAX_ATTACHMENT_SIZE_MB."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "not-a-number")
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_max_attachment_size_mb_negative_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() resets negative MAX_ATTACHMENT_SIZE_MB to 10.0."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "-5")
        config = load_config()
        assert config.max_attachment_size_mb == 10.0

    def test_attachment_config_passed_through_to_collection_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """to_collection_kwargs() includes attachment_extensions and max_attachment_size_mb."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "5.0")
        config = load_config()
        kwargs = config.to_collection_kwargs()
        assert kwargs["attachment_extensions"] == ["pdf", "png"]
        assert kwargs["max_attachment_size_mb"] == 5.0


class TestGitLfsConfig:
    """Tests for GIT_LFS env var parsing."""

    def test_git_lfs_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults git_lfs to True when GIT_LFS is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_LFS", raising=False)
        config = load_config()
        assert config.git_lfs is True

    def test_git_lfs_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() parses GIT_LFS=false as False."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "false")
        config = load_config()
        assert config.git_lfs is False

    def test_git_lfs_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() parses GIT_LFS=true as True."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "true")
        config = load_config()
        assert config.git_lfs is True

    def test_git_lfs_passed_to_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_lfs to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=tmp_path,
            git_token="ghp_test",
            git_lfs=False,
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is False

    def test_git_lfs_default_true_in_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_lfs=True to strategy by default."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = CollectionConfig(
            source_dir=tmp_path,
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is True


class TestCollectionConfigDefaults:
    """Verify all new fields on CollectionConfig have correct defaults."""

    def test_server_identity_defaults(self) -> None:
        """Server name defaults to 'markdown-vault-mcp', instructions to None."""
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.server_name == "markdown-vault-mcp"
        assert config.instructions is None

    def test_auth_defaults(self) -> None:
        """All auth fields default to None/False."""
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.auth_mode is None
        assert config.base_url is None
        assert config.oidc_config_url is None
        assert config.oidc_client_id is None
        assert config.oidc_client_secret is None
        assert config.oidc_audience is None
        assert config.oidc_required_scopes is None
        assert config.oidc_jwt_signing_key is None
        assert config.oidc_verify_access_token is False
        assert config.bearer_token is None

    def test_embedding_provider_defaults(self) -> None:
        """Embedding fields have correct defaults."""
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.embedding_provider is None
        assert config.ollama_host == "http://localhost:11434"
        assert config.ollama_model == "nomic-embed-text"
        assert config.ollama_cpu_only is False
        assert config.openai_api_key is None
        assert config.fastembed_model == "BAAI/bge-small-en-v1.5"
        assert config.fastembed_cache_dir is None

    def test_custom_values_accepted(self) -> None:
        """CollectionConfig accepts custom values for all new fields."""
        config = CollectionConfig(
            source_dir=Path("/tmp/vault"),
            server_name="my-server",
            instructions="Be helpful",
            auth_mode="bearer",
            base_url="https://example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
            oidc_client_id="client-123",
            oidc_client_secret="secret-456",
            oidc_audience="my-api",
            oidc_required_scopes="openid,profile",
            oidc_jwt_signing_key="signing-key-789",
            oidc_verify_access_token=True,
            bearer_token="tok_abc",
            embedding_provider="ollama",
            ollama_host="http://gpu-server:11434",
            ollama_model="mxbai-embed-large",
            ollama_cpu_only=True,
            openai_api_key="sk-test",
            fastembed_model="BAAI/bge-base-en-v1.5",
            fastembed_cache_dir="/tmp/cache",
        )
        assert config.server_name == "my-server"
        assert config.instructions == "Be helpful"
        assert config.auth_mode == "bearer"
        assert config.base_url == "https://example.com"
        assert config.oidc_client_id == "client-123"
        assert config.oidc_client_secret == "secret-456"
        assert config.oidc_verify_access_token is True
        assert config.bearer_token == "tok_abc"
        assert config.embedding_provider == "ollama"
        assert config.ollama_host == "http://gpu-server:11434"
        assert config.ollama_model == "mxbai-embed-large"
        assert config.ollama_cpu_only is True
        assert config.openai_api_key == "sk-test"
        assert config.fastembed_model == "BAAI/bge-base-en-v1.5"
        assert config.fastembed_cache_dir == "/tmp/cache"


class TestLoadConfigServerIdentityFields:
    """Verify server identity env vars are read by load_config()."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_server_name_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults server_name to 'markdown-vault-mcp'."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_SERVER_NAME", raising=False)
        config = load_config()
        assert config.server_name == "markdown-vault-mcp"

    def test_server_name_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_SERVER_NAME."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        config = load_config()
        assert config.server_name == "my-vault"

    def test_server_name_empty_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default when SERVER_NAME is empty."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "")
        config = load_config()
        assert config.server_name == "markdown-vault-mcp"

    def test_instructions_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults instructions to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", raising=False)
        config = load_config()
        assert config.instructions is None

    def test_instructions_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_INSTRUCTIONS."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "Be concise")
        config = load_config()
        assert config.instructions == "Be concise"


class TestLoadConfigAuthFields:
    """Verify auth env vars are read correctly by load_config()."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_auth_mode_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults auth_mode to None (auto-detect)."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_AUTH_MODE", raising=False)
        config = load_config()
        assert config.auth_mode is None

    def test_auth_mode_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_AUTH_MODE."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_AUTH_MODE", "bearer")
        config = load_config()
        assert config.auth_mode == "bearer"

    def test_base_url_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_BASE_URL."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://vault.example.com")
        config = load_config()
        assert config.base_url == "https://vault.example.com"

    def test_oidc_fields_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads all OIDC env vars."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
            "https://auth.example.com/.well-known/openid-configuration",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID", "my-client")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET", "s3cret")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_AUDIENCE", "my-api")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "openid,profile")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY", "jwt-key-123")
        config = load_config()
        assert (
            config.oidc_config_url
            == "https://auth.example.com/.well-known/openid-configuration"
        )
        assert config.oidc_client_id == "my-client"
        assert config.oidc_client_secret == "s3cret"
        assert config.oidc_audience == "my-api"
        assert config.oidc_required_scopes == "openid,profile"
        assert config.oidc_jwt_signing_key == "jwt-key-123"

    def test_oidc_verify_access_token_default_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults oidc_verify_access_token to False."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", raising=False)
        config = load_config()
        assert config.oidc_verify_access_token is False

    def test_oidc_verify_access_token_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() parses OIDC_VERIFY_ACCESS_TOKEN=true."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "true")
        config = load_config()
        assert config.oidc_verify_access_token is True

    def test_bearer_token_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_BEARER_TOKEN."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "tok_abc123")
        config = load_config()
        assert config.bearer_token == "tok_abc123"

    def test_bearer_token_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults bearer_token to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", raising=False)
        config = load_config()
        assert config.bearer_token is None


class TestLoadConfigEmbeddingFields:
    """Verify embedding env vars are read correctly by load_config()."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_embedding_provider_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "ollama")
        config = load_config()
        assert config.embedding_provider == "ollama"

    def test_embedding_provider_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults embedding_provider to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", raising=False)
        config = load_config()
        assert config.embedding_provider is None

    def test_ollama_host_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads OLLAMA_HOST (bare, not prefixed)."""
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu:11434")
        config = load_config()
        assert config.ollama_host == "http://gpu:11434"

    def test_ollama_host_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults ollama_host to http://localhost:11434."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        config = load_config()
        assert config.ollama_host == "http://localhost:11434"

    def test_ollama_host_empty_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() treats empty OLLAMA_HOST as default."""
        monkeypatch.setenv("OLLAMA_HOST", "")
        config = load_config()
        assert config.ollama_host == "http://localhost:11434"

    def test_ollama_host_trailing_slash_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() strips trailing slash from OLLAMA_HOST."""
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu:11434/")
        config = load_config()
        assert config.ollama_host == "http://gpu:11434"

    def test_ollama_model_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_OLLAMA_MODEL."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "mxbai-embed-large")
        config = load_config()
        assert config.ollama_model == "mxbai-embed-large"

    def test_ollama_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults ollama_model to nomic-embed-text."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", raising=False)
        config = load_config()
        assert config.ollama_model == "nomic-embed-text"

    def test_ollama_cpu_only_default_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults ollama_cpu_only to False."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", raising=False)
        config = load_config()
        assert config.ollama_cpu_only is False

    def test_ollama_cpu_only_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() parses OLLAMA_CPU_ONLY=true."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "true")
        config = load_config()
        assert config.ollama_cpu_only is True

    def test_openai_api_key_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads OPENAI_API_KEY (bare, not prefixed)."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
        config = load_config()
        assert config.openai_api_key == "sk-test123"

    def test_openai_api_key_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults openai_api_key to None."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = load_config()
        assert config.openai_api_key is None

    def test_fastembed_model_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_FASTEMBED_MODEL."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", "BAAI/bge-base-en-v1.5"
        )
        config = load_config()
        assert config.fastembed_model == "BAAI/bge-base-en-v1.5"

    def test_fastembed_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_config() defaults fastembed_model to BAAI/bge-small-en-v1.5."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", raising=False)
        config = load_config()
        assert config.fastembed_model == "BAAI/bge-small-en-v1.5"

    def test_fastembed_cache_dir_prefixed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() reads MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", "/tmp/fe-cache")
        config = load_config()
        assert config.fastembed_cache_dir == "/tmp/fe-cache"

    def test_fastembed_cache_dir_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() defaults fastembed_cache_dir to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", raising=False)
        config = load_config()
        assert config.fastembed_cache_dir is None


# ---------------------------------------------------------------------------
# Auth builder helpers
# ---------------------------------------------------------------------------


def _oidc_config(**overrides: Any) -> CollectionConfig:
    """Build a CollectionConfig pre-filled with all required OIDC fields."""
    defaults: dict[str, Any] = {
        "source_dir": Path("/tmp"),
        "base_url": "https://mcp.example.com",
        "oidc_config_url": "https://auth.example.com/.well-known/openid-configuration",
        "oidc_client_id": "test-client",
        "oidc_client_secret": "test-secret",
    }
    defaults.update(overrides)
    return CollectionConfig(**defaults)


# ---------------------------------------------------------------------------
# resolve_auth_mode()
# ---------------------------------------------------------------------------


class TestResolveAuthModeConfig:
    """Tests for resolve_auth_mode() accepting CollectionConfig."""

    def test_explicit_remote(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="remote")
        assert resolve_auth_mode(config) == "remote"

    def test_explicit_oidc_proxy(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="oidc-proxy")
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_auto_detect_oidc_proxy(self) -> None:
        """All four OIDC fields set -> oidc-proxy."""
        config = _oidc_config()
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_auto_detect_remote(self) -> None:
        """Only base_url + oidc_config_url -> remote."""
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        assert resolve_auth_mode(config) == "remote"

    def test_no_auth_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert resolve_auth_mode(config) is None

    def test_invalid_mode_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="invalid")
        assert resolve_auth_mode(config) is None


# ---------------------------------------------------------------------------
# build_bearer_auth()
# ---------------------------------------------------------------------------


class TestBuildBearerAuthConfig:
    """Tests for build_bearer_auth() accepting CollectionConfig."""

    def test_returns_none_when_no_token(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_bearer_auth(config) is None

    def test_returns_verifier_when_token_set(self) -> None:
        from fastmcp.server.auth import StaticTokenVerifier

        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="tok123")
        result = build_bearer_auth(config)
        assert isinstance(result, StaticTokenVerifier)
        assert "tok123" in result.tokens


# ---------------------------------------------------------------------------
# build_oidc_auth()
# ---------------------------------------------------------------------------


class TestBuildOidcAuthConfig:
    """Tests for build_oidc_auth() accepting CollectionConfig."""

    def test_returns_none_when_missing_fields(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_oidc_auth(config) is None

    def test_returns_proxy_when_all_fields_present(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            result = build_oidc_auth(config)
        assert result is not None
        mock_cls.assert_called_once()

    def test_passes_correct_kwargs(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        kw = mock_cls.call_args.kwargs
        assert kw["base_url"] == "https://mcp.example.com"
        assert kw["client_id"] == "test-client"
        assert kw["client_secret"] == "test-secret"
        assert kw["required_scopes"] == ["openid"]
        assert kw["verify_id_token"] is True

    def test_scopes_parsed_from_config(self) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config(oidc_required_scopes="openid, profile")
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)

        assert mock_cls.call_args.kwargs["required_scopes"] == ["openid", "profile"]

    def test_linux_warning_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        from unittest.mock import MagicMock, patch

        config = _oidc_config()
        mock_cls = MagicMock()
        with (
            patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls),
            patch("markdown_vault_mcp.config.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            build_oidc_auth(config)

        assert any(
            "JWT_SIGNING_KEY" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# build_remote_auth()
# ---------------------------------------------------------------------------


class TestBuildRemoteAuthConfig:
    """Tests for build_remote_auth() accepting CollectionConfig."""

    def test_returns_none_when_missing_fields(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_remote_auth(config) is None

    def test_returns_none_on_discovery_failure(self) -> None:
        from unittest.mock import patch

        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        with patch("httpx.get", side_effect=Exception("fail")):
            assert build_remote_auth(config) is None

    def test_happy_path(self) -> None:
        from unittest.mock import MagicMock, patch

        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_resp):
            result = build_remote_auth(config)
        assert result is not None

    def test_scopes_parsed_from_config(self) -> None:
        from unittest.mock import MagicMock, patch

        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
            oidc_required_scopes="openid, profile",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
            "issuer": "https://auth.example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        mock_verifier_cls = MagicMock()
        mock_remote_cls = MagicMock()

        with (
            patch("httpx.get", return_value=mock_resp),
            patch("fastmcp.server.auth.JWTVerifier", mock_verifier_cls),
            patch("fastmcp.server.auth.RemoteAuthProvider", mock_remote_cls),
        ):
            build_remote_auth(config)

        kw = mock_verifier_cls.call_args.kwargs
        assert kw["required_scopes"] == ["openid", "profile"]


class TestEmptyBoolEnvVarsFallToDefault:
    """Empty-string env vars on bool fields fall through to the configured default.

    Adopting fastmcp_pvl_core.env() changed the semantics: blank values are
    treated as unset (the helper strips whitespace and returns the default),
    where MV's old _env() returned the literal empty string and downstream
    parse_bool("") yielded False. These tests lock in the new contract.
    """

    @pytest.fixture(autouse=True)
    def _source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_read_only_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "")
        config = load_config()
        assert config.read_only is True  # default

    def test_git_lfs_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "")
        config = load_config()
        assert config.git_lfs is True  # default

    def test_oidc_verify_access_token_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "")
        config = load_config()
        assert config.oidc_verify_access_token is False  # default

    def test_ollama_cpu_only_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "")
        config = load_config()
        assert config.ollama_cpu_only is False  # default


class TestServerConfigComposition:
    """The composed ServerConfig field on CollectionConfig."""

    def test_server_field_default_is_empty_serverconfig(self) -> None:
        from fastmcp_pvl_core import ServerConfig

        config = CollectionConfig(source_dir=Path("/tmp/v"))
        assert isinstance(config.server, ServerConfig)
        # Defaults match ServerConfig dataclass defaults
        assert config.server.transport == "stdio"
        assert config.server.bearer_token is None

    def test_load_config_populates_server_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSPORT", "http")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "secret-token")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://api.example.com")

        config = load_config()

        assert config.server.transport == "http"
        assert config.server.bearer_token == "secret-token"
        assert config.server.base_url == "https://api.example.com"
