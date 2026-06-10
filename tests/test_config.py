"""Tests for config.py — env var loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_vault_mcp.config import (
    VaultConfig,
    derive_max_chunk_chars,
)
from markdown_vault_mcp.config_sections import (
    EmbeddingsConfig,
    GitConfig,
    IndexingConfig,
    SearchConfig,
    TransferConfig,
)
from markdown_vault_mcp.exceptions import ConfigurationError


def test_search_ranking_config_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """New ranking/snippet knobs default to documented values when env unset."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    # Clear any test-runner-leaked overrides.
    for var in (
        "MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE",
        "MARKDOWN_VAULT_MCP_SNIPPET_WORDS",
        "MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA",
        "MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = VaultConfig.from_env()
    assert cfg.search.chunks_per_file == 2
    assert cfg.search.snippet_words == 200
    assert cfg.search.length_downweight_alpha == 0.25
    assert cfg.search.max_chunk_words == 400


def test_search_ranking_config_env_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Env vars override the defaults."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "1")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SNIPPET_WORDS", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA", "0.0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS", "100000")

    cfg = VaultConfig.from_env()
    assert cfg.search.chunks_per_file == 1
    assert cfg.search.snippet_words == 0
    assert cfg.search.length_downweight_alpha == 0.0
    assert cfg.search.max_chunk_words == 100000


def test_search_ranking_config_rejects_zero_chunks_per_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """chunks_per_file=0 is rejected at VaultConfig.from_env time (no useful semantics)."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "0")

    with pytest.raises(ConfigurationError, match="chunks_per_file"):
        VaultConfig.from_env()


@pytest.mark.parametrize(
    "ctx,override,expected",
    [
        (8192, None, round(8192 * 2.8)),
        (512, None, round(512 * 2.8)),
        (None, None, 6000),
        (0, None, 6000),  # degenerate 0 context falls back, not a 0 cap
        (8192, 4096, 4096),
        (None, 4096, 4096),
    ],
)
def test_derive_max_chunk_chars(
    ctx: int | None, override: int | None, expected: int
) -> None:
    """Override wins; else derive from context; else the fixed fallback."""
    assert derive_max_chunk_chars(context_length=ctx, override=override) == expected


def test_max_chunk_chars_override_default_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The char-cap override is None when its env var is unset."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS", raising=False)
    cfg = VaultConfig.from_env()
    assert cfg.search.max_chunk_chars_override is None


def test_max_chunk_chars_override_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A positive MAX_CHUNK_CHARS env var populates the override."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS", "12345")
    cfg = VaultConfig.from_env()
    assert cfg.search.max_chunk_chars_override == 12345


def test_max_chunk_chars_override_rejects_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MAX_CHUNK_CHARS < 1 is rejected like MAX_CHUNK_WORDS."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS", "0")
    with pytest.raises(ConfigurationError, match="max_chunk_chars"):
        VaultConfig.from_env()


def test_max_chunk_chars_override_rejects_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-integer MAX_CHUNK_CHARS raises."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS", "lots")
    with pytest.raises(ConfigurationError, match="MAX_CHUNK_CHARS"):
        VaultConfig.from_env()


class TestParseHelpers:
    """Test boolean and list parsing edge cases via VaultConfig.from_env."""

    def test_bool_true_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "YES", " true "):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = VaultConfig.from_env()
            assert config.read_only is True, f"Expected True for {val!r}"

    def test_bool_false_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "False", "0", "no", "anything"):
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
            monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", val)
            config = VaultConfig.from_env()
            assert config.read_only is False, f"Expected False for {val!r}"


class TestLoadConfig:
    def test_missing_source_dir_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", raising=False)
        with pytest.raises(ConfigurationError, match="MARKDOWN_VAULT_MCP_SOURCE_DIR"):
            VaultConfig.from_env()

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

        config = VaultConfig.from_env()

        assert config.source_dir == Path("/tmp/vault")
        assert config.read_only is True  # default
        assert config.indexing.index_path is None
        assert config.indexing.embeddings_path is None
        assert config.indexing.state_path is None
        assert config.indexing.indexed_frontmatter_fields is None
        assert config.indexing.required_frontmatter is None
        assert config.indexing.exclude_patterns is None
        assert config.git.repo_url is None
        assert config.git.username == "x-access-token"
        assert config.git.token is None
        assert config.git.pull_interval_s == 600
        assert config.content.templates_folder == "_templates"

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

        config = VaultConfig.from_env()

        assert config.source_dir == Path("/data/vault")
        assert config.read_only is False
        assert config.indexing.index_path == Path("/data/index.db")
        assert config.indexing.embeddings_path == Path("/data/embeddings")
        assert config.indexing.state_path == Path("/data/state.json")
        assert config.indexing.indexed_frontmatter_fields == ("cluster", "topics")
        assert config.indexing.required_frontmatter == ("title", "cluster")
        assert config.indexing.exclude_patterns == (".obsidian/**", ".trash/**")
        assert config.git.repo_url == "https://github.com/acme/vault.git"
        assert config.git.username == "oauth2"
        assert config.git.token == "ghp_test123"
        assert config.git.pull_interval_s == 300
        assert config.content.templates_folder == "Templates"

    def test_git_username_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_USERNAME", raising=False)
        config = VaultConfig.from_env()
        assert config.git.username == "x-access-token"

    def test_templates_folder_trailing_slash_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates/")
        config = VaultConfig.from_env()
        assert config.content.templates_folder == "Templates"

    def test_templates_folder_backslashes_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates\\Notes\\")
        config = VaultConfig.from_env()
        assert config.content.templates_folder == "Templates/Notes"

    def test_templates_folder_slash_only_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "/")
        config = VaultConfig.from_env()
        assert config.content.templates_folder == "_templates"

    def test_token_without_repo_url_logs_deprecation(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_TOKEN", "ghp_legacy")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_REPO_URL", raising=False)
        _ = VaultConfig.from_env()
        assert "legacy mode is deprecated" in caplog.text

    def test_invalid_pull_interval_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-numeric GIT_PULL_INTERVAL_S raises (no warn-and-default; #638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "nope")
        with pytest.raises(ConfigurationError):
            VaultConfig.from_env()

    def test_negative_pull_interval_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A negative GIT_PULL_INTERVAL_S raises (no longer clamps to 0; #638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "-5")
        with pytest.raises(ConfigurationError, match="pull_interval_s"):
            VaultConfig.from_env()

    def test_comma_separated_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", " a , b , c ")
        config = VaultConfig.from_env()
        assert config.indexing.indexed_frontmatter_fields == ("a", "b", "c")

    def test_empty_comma_list_yields_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "")
        config = VaultConfig.from_env()
        assert config.indexing.indexed_frontmatter_fields is None


class TestToVaultKwargs:
    def test_includes_exclude_patterns(self) -> None:
        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            indexing=IndexingConfig(exclude_patterns=[".obsidian/**"]),
        )
        kwargs = config.to_vault_kwargs()
        assert kwargs["exclude_patterns"] == (".obsidian/**",)
        assert kwargs["source_dir"] == Path("/tmp/vault")

    def test_excludes_git_token(self) -> None:
        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            git=GitConfig(token="ghp_secret"),
        )
        kwargs = config.to_vault_kwargs()
        assert "git_token" not in kwargs

    def test_includes_all_vault_params(self, monkeypatch) -> None:
        # Monkeypatch the resolver so the test is deterministic (the old form
        # relied on a live FastEmbed model download and was flaky-xfail).
        import markdown_vault_mcp.providers as providers_mod

        class _FakeProvider:
            context_length = 512

        fake = _FakeProvider()
        monkeypatch.setattr(
            providers_mod, "get_embedding_provider", lambda _config: fake
        )
        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            read_only=False,
            indexing=IndexingConfig(
                index_path=Path("/tmp/index.db"),
                embeddings_path=Path("/tmp/emb"),
                state_path=Path("/tmp/state.json"),
                indexed_frontmatter_fields=["cluster"],
                required_frontmatter=["title"],
                exclude_patterns=[".obsidian/**"],
            ),
        )
        kwargs = config.to_vault_kwargs()
        assert kwargs["source_dir"] == Path("/tmp/vault")
        assert kwargs["read_only"] is False
        assert kwargs["index_path"] == Path("/tmp/index.db")
        assert kwargs["embeddings_path"] == Path("/tmp/emb")
        assert kwargs["state_path"] == Path("/tmp/state.json")
        assert kwargs["indexed_frontmatter_fields"] == ("cluster",)
        assert kwargs["required_frontmatter"] == ("title",)
        assert kwargs["exclude_patterns"] == (".obsidian/**",)
        assert kwargs["attachment_extensions"] is None
        assert kwargs["max_attachment_size_mb"] == 1.0
        assert kwargs["git_pull_interval_s"] == 0
        assert kwargs["embedding_provider"] is fake
        # The resolved provider's context length drives the chunk char cap (#649).
        assert kwargs["max_chunk_chars"] == round(512 * 2.8)
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

        config = VaultConfig(
            source_dir=source_dir,
            git=GitConfig(repo_url=str(bare), token="ghp_secret", pull_interval_s=123),
        )
        kwargs = config.to_vault_kwargs()
        assert kwargs["git_pull_interval_s"] == 123
        assert "git_strategy" in kwargs
        assert "on_write" in kwargs


class TestToVaultKwargsProvider:
    """Embedding-provider resolution in to_vault_kwargs (#638 PR2).

    An *explicitly* configured provider that fails to load is a hard
    ConfigurationError; auto-detection failures degrade to keyword-only.
    """

    def _config(self, *, provider: str | None, tmp_path: Path) -> VaultConfig:
        return VaultConfig(
            source_dir=tmp_path,
            embeddings=EmbeddingsConfig(provider=provider),
            indexing=IndexingConfig(embeddings_path=tmp_path / "emb"),
        )

    @pytest.mark.parametrize("exc", [ImportError("missing dep"), RuntimeError("boom")])
    def test_explicit_provider_load_failure_raises(
        self, monkeypatch, tmp_path, exc
    ) -> None:
        """An explicit provider that can't load fails hard (no silent degrade)."""
        import markdown_vault_mcp.providers as providers_mod

        def _boom(_config):
            raise exc

        monkeypatch.setattr(providers_mod, "get_embedding_provider", _boom)
        config = self._config(provider="openai", tmp_path=tmp_path)
        with pytest.raises(ConfigurationError, match="openai"):
            config.to_vault_kwargs()

    def test_unrecognized_provider_name_raises(self, tmp_path) -> None:
        """A bogus EMBEDDING_PROVIDER value surfaces as ConfigurationError."""
        config = self._config(provider="bogus", tmp_path=tmp_path)
        with pytest.raises(ConfigurationError, match="Unrecognised"):
            config.to_vault_kwargs()

    @pytest.mark.parametrize("exc", [ImportError("missing dep"), RuntimeError("none")])
    def test_autodetect_failure_degrades(self, monkeypatch, tmp_path, exc) -> None:
        """Auto-detection (no explicit provider) degrades to keyword-only, no raise."""
        import markdown_vault_mcp.providers as providers_mod

        def _boom(_config):
            raise exc

        monkeypatch.setattr(providers_mod, "get_embedding_provider", _boom)
        config = self._config(provider=None, tmp_path=tmp_path)
        kwargs = config.to_vault_kwargs()
        assert "embedding_provider" not in kwargs
        # No provider → the chunk char cap falls back to the fixed default (#649).
        assert kwargs["max_chunk_chars"] == 6000

    def test_no_embeddings_path_skips_provider(self, monkeypatch, tmp_path) -> None:
        """With no embeddings_path the provider is never resolved, even if broken."""
        import markdown_vault_mcp.providers as providers_mod

        def _boom(_config):
            raise RuntimeError("should not be called")

        monkeypatch.setattr(providers_mod, "get_embedding_provider", _boom)
        config = VaultConfig(
            source_dir=tmp_path,
            embeddings=EmbeddingsConfig(provider="openai"),
            indexing=IndexingConfig(embeddings_path=None),
        )
        kwargs = config.to_vault_kwargs()
        assert "embedding_provider" not in kwargs

    def test_provider_loads_sets_kwarg(self, monkeypatch, tmp_path) -> None:
        """A successfully resolved provider is threaded into the kwargs."""
        import markdown_vault_mcp.providers as providers_mod

        class _FakeProvider:
            context_length = 512

        fake = _FakeProvider()
        monkeypatch.setattr(
            providers_mod, "get_embedding_provider", lambda _config: fake
        )
        config = self._config(provider="openai", tmp_path=tmp_path)
        kwargs = config.to_vault_kwargs()
        assert kwargs["embedding_provider"] is fake
        # The resolved provider's context length drives the chunk char cap (#649).
        assert kwargs["max_chunk_chars"] == round(512 * 2.8)


class TestGitCommitterConfig:
    """Tests for git committer identity configuration."""

    def test_default_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() uses default git_commit_name when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", raising=False)
        config = VaultConfig.from_env()
        assert config.git.commit_name == "markdown-vault-mcp"

    def test_default_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() uses default git_commit_email when env var is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", raising=False)
        config = VaultConfig.from_env()
        assert config.git.commit_email == "noreply@markdown-vault-mcp"

    def test_override_git_commit_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "MyBot")
        config = VaultConfig.from_env()
        assert config.git.commit_name == "MyBot"

    def test_override_git_commit_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL from environment."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "bot@example.com")
        config = VaultConfig.from_env()
        assert config.git.commit_email == "bot@example.com"

    def test_both_git_committer_vars_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads both GIT_COMMIT_NAME and GIT_COMMIT_EMAIL together."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "DeployBot")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "deploy@corp.local")
        config = VaultConfig.from_env()
        assert config.git.commit_name == "DeployBot"
        assert config.git.commit_email == "deploy@corp.local"

    def test_empty_git_commit_name_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() falls back to default when GIT_COMMIT_NAME is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME", "")
        config = VaultConfig.from_env()
        assert config.git.commit_name == "markdown-vault-mcp"

    def test_empty_git_commit_email_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() falls back to default when GIT_COMMIT_EMAIL is empty string."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL", "")
        config = VaultConfig.from_env()
        assert config.git.commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_defaults(self) -> None:
        """VaultConfig has correct default committer values."""
        config = VaultConfig(source_dir=Path("/tmp/vault"))
        assert config.git.commit_name == "markdown-vault-mcp"
        assert config.git.commit_email == "noreply@markdown-vault-mcp"

    def test_config_dataclass_custom_values(self) -> None:
        """VaultConfig accepts custom committer name and email."""
        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            git=GitConfig(commit_name="CI", commit_email="ci@example.com"),
        )
        assert config.git.commit_name == "CI"
        assert config.git.commit_email == "ci@example.com"

    def test_to_vault_kwargs_includes_commit_identity(self) -> None:
        """to_vault_kwargs() passes commit identity to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            git=GitConfig(
                token="ghp_test", commit_name="TestBot", commit_email="test@example.com"
            ),
        )
        kwargs = config.to_vault_kwargs()

        assert "on_write" in kwargs
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert kwargs["git_strategy"] is strategy
        assert strategy._commit_name == "TestBot"
        assert strategy._commit_email == "test@example.com"

    def test_to_vault_kwargs_with_default_identity(self) -> None:
        """to_vault_kwargs() uses defaults when no custom identity is set."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            git=GitConfig(token="ghp_test"),
        )
        kwargs = config.to_vault_kwargs()

        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._commit_name == "markdown-vault-mcp"
        assert strategy._commit_email == "noreply@markdown-vault-mcp"


class TestAttachmentConfig:
    """Tests for attachment extension and size limit configuration."""

    def test_default_attachment_extensions_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() returns None attachment_extensions when env var not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", raising=False)
        config = VaultConfig.from_env()
        assert config.content.attachment_extensions is None

    def test_attachment_extensions_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() parses ATTACHMENT_EXTENSIONS as comma-separated list."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png,docx")
        config = VaultConfig.from_env()
        assert config.content.attachment_extensions == ("pdf", "png", "docx")

    def test_attachment_extensions_wildcard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() parses ATTACHMENT_EXTENSIONS=* as ['*']."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "*")
        config = VaultConfig.from_env()
        assert config.content.attachment_extensions == ("*",)

    def test_attachment_extensions_empty_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() returns None when ATTACHMENT_EXTENSIONS is empty."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "")
        config = VaultConfig.from_env()
        assert config.content.attachment_extensions is None

    def test_default_max_attachment_size_mb(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() defaults max_attachment_size_mb to 1.0 (tightened in #442)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", raising=False)
        config = VaultConfig.from_env()
        assert config.content.max_attachment_size_mb == 1.0

    def test_max_attachment_size_mb_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() parses MAX_ATTACHMENT_SIZE_MB from env var."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "25.5")
        config = VaultConfig.from_env()
        assert config.content.max_attachment_size_mb == 25.5

    def test_max_attachment_size_mb_zero_disables_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() accepts 0 as a valid value for MAX_ATTACHMENT_SIZE_MB."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0")
        config = VaultConfig.from_env()
        assert config.content.max_attachment_size_mb == 0.0

    def test_max_attachment_size_mb_invalid_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() raises on a non-numeric MAX_ATTACHMENT_SIZE_MB (#638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "not-a-number")
        with pytest.raises(ConfigurationError):
            VaultConfig.from_env()

    def test_max_attachment_size_mb_negative_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() raises on a negative MAX_ATTACHMENT_SIZE_MB (#638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "-5")
        with pytest.raises(ConfigurationError, match="max_attachment_size_mb"):
            VaultConfig.from_env()

    def test_attachment_config_passed_through_to_vault_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """to_vault_kwargs() includes attachment_extensions and max_attachment_size_mb."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "5.0")
        config = VaultConfig.from_env()
        kwargs = config.to_vault_kwargs()
        assert kwargs["attachment_extensions"] == ("pdf", "png")
        assert kwargs["max_attachment_size_mb"] == 5.0


class TestGitLfsConfig:
    """Tests for GIT_LFS env var parsing."""

    def test_git_lfs_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults git_lfs to True when GIT_LFS is not set."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_GIT_LFS", raising=False)
        config = VaultConfig.from_env()
        assert config.git.lfs is True

    def test_git_lfs_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() parses GIT_LFS=false as False."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "false")
        config = VaultConfig.from_env()
        assert config.git.lfs is False

    def test_git_lfs_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() parses GIT_LFS=true as True."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "true")
        config = VaultConfig.from_env()
        assert config.git.lfs is True

    def test_git_lfs_passed_to_strategy(self, tmp_path: Path) -> None:
        """to_vault_kwargs() passes git_lfs to GitWriteStrategy."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = VaultConfig(
            source_dir=tmp_path,
            git=GitConfig(token="ghp_test", lfs=False),
        )
        kwargs = config.to_vault_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is False

    def test_git_lfs_default_true_in_strategy(self, tmp_path: Path) -> None:
        """to_vault_kwargs() passes git_lfs=True to strategy by default."""
        from markdown_vault_mcp.git import GitWriteStrategy

        config = VaultConfig(
            source_dir=tmp_path,
            git=GitConfig(token="ghp_test"),
        )
        kwargs = config.to_vault_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._git_lfs is True


class TestGitConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import GitConfig

        for k in (
            "GIT_TOKEN",
            "GIT_REPO_URL",
            "GIT_PULL_INTERVAL_S",
            "GIT_PUSH_DELAY_S",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        g = GitConfig.from_env("MARKDOWN_VAULT_MCP")
        assert g == GitConfig()

    def test_pull_interval_negative_raises(self, monkeypatch):
        """A negative GIT_PULL_INTERVAL_S raises (no longer clamps to 0; #638)."""
        from markdown_vault_mcp.config_sections import GitConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "-5")
        with pytest.raises(ConfigurationError, match="pull_interval_s"):
            GitConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_push_delay_invalid_raises(self, monkeypatch):
        """A non-numeric GIT_PUSH_DELAY_S raises (no warn-and-default; #638)."""
        from markdown_vault_mcp.config_sections import GitConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "nope")
        with pytest.raises(ConfigurationError):
            GitConfig.from_env("MARKDOWN_VAULT_MCP")

    @pytest.mark.parametrize(
        "kwargs", [{"push_delay_s": -1.0}, {"pull_interval_s": -5}]
    )
    def test_direct_construction_validates(self, kwargs):
        """__post_init__ rejects negative cadences on direct construction (#638)."""
        from markdown_vault_mcp.config_sections import GitConfig

        with pytest.raises(ConfigurationError):
            GitConfig(**kwargs)

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import GitConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            GitConfig().token = "x"  # type: ignore[misc]


class TestVaultConfigDefaults:
    """Verify all new fields on VaultConfig have correct defaults."""

    def test_server_identity_defaults(self) -> None:
        """Server name defaults to 'markdown-vault-mcp', instructions to None."""
        config = VaultConfig(source_dir=Path("/tmp/vault"))
        assert config.server_name == "markdown-vault-mcp"
        assert config.instructions is None

    def test_embedding_provider_defaults(self) -> None:
        """Embedding fields have correct defaults."""
        config = VaultConfig(source_dir=Path("/tmp/vault"))
        assert config.embeddings.provider is None
        assert config.embeddings.ollama_host == "http://localhost:11434"
        assert config.embeddings.ollama_model == "nomic-embed-text"
        assert config.embeddings.ollama_cpu_only is False
        assert config.embeddings.openai_api_key is None
        assert config.embeddings.openai_base_url == "https://api.openai.com/v1"
        assert config.embeddings.openai_embedding_model == "text-embedding-3-small"
        assert config.embeddings.fastembed_model == "BAAI/bge-small-en-v1.5"
        assert config.embeddings.fastembed_cache_dir is None

    def test_custom_values_accepted(self) -> None:
        """VaultConfig accepts custom values for all new fields."""
        config = VaultConfig(
            source_dir=Path("/tmp/vault"),
            server_name="my-server",
            instructions="Be helpful",
            embeddings=EmbeddingsConfig(
                provider="ollama",
                ollama_host="http://gpu-server:11434",
                ollama_model="mxbai-embed-large",
                ollama_cpu_only=True,
                openai_api_key="sk-test",
                openai_base_url="https://api.siliconflow.cn/v1",
                openai_embedding_model="BAAI/bge-m3",
                fastembed_model="BAAI/bge-base-en-v1.5",
                fastembed_cache_dir="/tmp/cache",
            ),
        )
        assert config.server_name == "my-server"
        assert config.instructions == "Be helpful"
        assert config.embeddings.provider == "ollama"
        assert config.embeddings.ollama_host == "http://gpu-server:11434"
        assert config.embeddings.ollama_model == "mxbai-embed-large"
        assert config.embeddings.ollama_cpu_only is True
        assert config.embeddings.openai_api_key == "sk-test"
        assert config.embeddings.openai_base_url == "https://api.siliconflow.cn/v1"
        assert config.embeddings.openai_embedding_model == "BAAI/bge-m3"
        assert config.embeddings.fastembed_model == "BAAI/bge-base-en-v1.5"
        assert config.embeddings.fastembed_cache_dir == "/tmp/cache"


class TestLoadConfigServerIdentityFields:
    """Verify server identity env vars are read by VaultConfig.from_env()."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_server_name_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults server_name to 'markdown-vault-mcp'."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_SERVER_NAME", raising=False)
        config = VaultConfig.from_env()
        assert config.server_name == "markdown-vault-mcp"

    def test_server_name_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_SERVER_NAME."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        config = VaultConfig.from_env()
        assert config.server_name == "my-vault"

    def test_server_name_empty_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() falls back to default when SERVER_NAME is empty."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "")
        config = VaultConfig.from_env()
        assert config.server_name == "markdown-vault-mcp"

    def test_instructions_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults instructions to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", raising=False)
        config = VaultConfig.from_env()
        assert config.instructions is None

    def test_instructions_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_INSTRUCTIONS."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "Be concise")
        config = VaultConfig.from_env()
        assert config.instructions == "Be concise"


class TestEmbeddingsConfigNormalization:
    """EmbeddingsConfig.__post_init__ normalizes ollama_host on direct construction.

    VaultConfig.from_env() pre-normalizes ollama_host before building EmbeddingsConfig, so
    these assert the dataclass's own contract independently of the loader.
    """

    def test_empty_ollama_host_falls_back_to_default(self) -> None:
        assert EmbeddingsConfig(ollama_host="").ollama_host == "http://localhost:11434"

    def test_ollama_host_trailing_slash_stripped(self) -> None:
        assert (
            EmbeddingsConfig(ollama_host="http://gpu:11434/").ollama_host
            == "http://gpu:11434"
        )


class TestLoadConfigEmbeddingFields:
    """Verify embedding env vars are read correctly by VaultConfig.from_env()."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_embedding_provider_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "ollama")
        config = VaultConfig.from_env()
        assert config.embeddings.provider == "ollama"

    def test_embedding_provider_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() defaults embedding_provider to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.provider is None

    def test_ollama_host_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads OLLAMA_HOST (bare, not prefixed)."""
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu:11434")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_host == "http://gpu:11434"

    def test_ollama_host_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults ollama_host to http://localhost:11434."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_host == "http://localhost:11434"

    def test_ollama_host_empty_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() treats empty OLLAMA_HOST as default."""
        monkeypatch.setenv("OLLAMA_HOST", "")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_host == "http://localhost:11434"

    def test_ollama_host_trailing_slash_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() strips trailing slash from OLLAMA_HOST."""
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu:11434/")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_host == "http://gpu:11434"

    def test_ollama_model_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_OLLAMA_MODEL."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "mxbai-embed-large")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_model == "mxbai-embed-large"

    def test_ollama_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults ollama_model to nomic-embed-text."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_model == "nomic-embed-text"

    def test_ollama_cpu_only_default_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() defaults ollama_cpu_only to False."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_cpu_only is False

    def test_ollama_cpu_only_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() parses OLLAMA_CPU_ONLY=true."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "true")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_cpu_only is True

    def test_openai_api_key_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads OPENAI_API_KEY (bare, not prefixed)."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_api_key == "sk-test123"

    def test_openai_api_key_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults openai_api_key to None."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.openai_api_key is None

    def test_openai_base_url_prefixed_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_OPENAI_BASE_URL."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OPENAI_BASE_URL",
            "https://api.siliconflow.cn/v1/",
        )
        config = VaultConfig.from_env()
        assert config.embeddings.openai_base_url == "https://api.siliconflow.cn/v1"

    def test_openai_base_url_bare_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads OPENAI_BASE_URL when the prefixed var is absent."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.compat.example/v1")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_base_url == "https://api.compat.example/v1"

    def test_openai_base_url_prefixed_env_var_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() prefers prefixed base URL over OPENAI_BASE_URL."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OPENAI_BASE_URL",
            "https://api.prefixed.example/v1",
        )
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.bare.example/v1")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_base_url == "https://api.prefixed.example/v1"

    def test_openai_base_url_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults openai_base_url to the OpenAI API base URL."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.openai_base_url == "https://api.openai.com/v1"

    def test_openai_embedding_model_prefixed_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL", "BAAI/bge-m3")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_embedding_model == "BAAI/bge-m3"

    def test_openai_embedding_model_bare_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads OPENAI_EMBEDDING_MODEL when prefixed var is absent."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_embedding_model == "text-embedding-3-large"

    def test_openai_embedding_model_prefixed_env_var_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() prefers prefixed model over OPENAI_EMBEDDING_MODEL."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL",
            "prefixed-embedding-model",
        )
        monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "bare-embedding-model")
        config = VaultConfig.from_env()
        assert config.embeddings.openai_embedding_model == "prefixed-embedding-model"

    def test_openai_embedding_model_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() defaults openai_embedding_model to text-embedding-3-small."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.openai_embedding_model == "text-embedding-3-small"

    def test_fastembed_model_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_FASTEMBED_MODEL."""
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", "BAAI/bge-base-en-v1.5"
        )
        config = VaultConfig.from_env()
        assert config.embeddings.fastembed_model == "BAAI/bge-base-en-v1.5"

    def test_fastembed_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VaultConfig.from_env() defaults fastembed_model to BAAI/bge-small-en-v1.5."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.fastembed_model == "BAAI/bge-small-en-v1.5"

    def test_fastembed_cache_dir_prefixed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() reads MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", "/tmp/fe-cache")
        config = VaultConfig.from_env()
        assert config.embeddings.fastembed_cache_dir == "/tmp/fe-cache"

    def test_fastembed_cache_dir_default_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VaultConfig.from_env() defaults fastembed_cache_dir to None."""
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", raising=False)
        config = VaultConfig.from_env()
        assert config.embeddings.fastembed_cache_dir is None


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
        config = VaultConfig.from_env()
        assert config.read_only is True  # default

    def test_git_lfs_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_LFS", "")
        config = VaultConfig.from_env()
        assert config.git.lfs is True  # default

    def test_oidc_verify_access_token_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "")
        config = VaultConfig.from_env()
        assert config.server.oidc_verify_access_token is False  # default

    def test_ollama_cpu_only_empty_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "")
        config = VaultConfig.from_env()
        assert config.embeddings.ollama_cpu_only is False  # default


class TestServerConfigComposition:
    """The composed ServerConfig field on VaultConfig."""

    def test_server_field_default_is_empty_serverconfig(self) -> None:
        from fastmcp_pvl_core import ServerConfig

        config = VaultConfig(source_dir=Path("/tmp/v"))
        assert isinstance(config.server, ServerConfig)
        # Defaults match ServerConfig dataclass defaults
        assert config.server.transport == "stdio"
        assert config.server.bearer_token is None

    def test_from_env_populates_server_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSPORT", "http")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "secret-token")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://api.example.com")

        config = VaultConfig.from_env()

        assert config.server.transport == "http"
        assert config.server.bearer_token == "secret-token"
        assert config.server.base_url == "https://api.example.com"

    def test_from_env_reads_oidc_verify_access_token_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """OIDC_VERIFY_ACCESS_TOKEN=true composes to config.server as True."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "true")

        config = VaultConfig.from_env()

        assert config.server.oidc_verify_access_token is True

    def test_from_env_populates_oidc_fields_from_prefixed_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Every MARKDOWN_VAULT_MCP_OIDC_*/AUTH_MODE var reaches config.server.

        Guards the env-prefix wiring for the OIDC/AUTH_MODE portion of the auth
        surface the deleted TestLoadConfigAuthFields covered (bearer/base_url/
        verify-token are covered by the sibling tests above), now asserted
        against the composed ServerConfig.
        """
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_AUTH_MODE", "oidc-proxy")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
            "https://auth.example.com/.well-known/openid-configuration",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID", "client-123")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET", "secret-456")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_AUDIENCE", "my-api")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "openid,profile")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY", "signing-key")

        config = VaultConfig.from_env()

        assert config.server.auth_mode == "oidc-proxy"
        assert (
            config.server.oidc_config_url
            == "https://auth.example.com/.well-known/openid-configuration"
        )
        assert config.server.oidc_client_id == "client-123"
        assert config.server.oidc_client_secret == "secret-456"
        assert config.server.oidc_audience == "my-api"
        assert config.server.oidc_required_scopes == ("openid", "profile")
        assert config.server.oidc_jwt_signing_key == "signing-key"


def test_search_ranking_config_rejects_malformed_int(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bad env input names the offending variable in the error message."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "foo")

    with pytest.raises(ConfigurationError, match="MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE"):
        VaultConfig.from_env()


def test_search_ranking_config_rejects_malformed_float(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bad float env input names the offending variable in the error message."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA", "abc")

    with pytest.raises(
        ConfigurationError, match="MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA"
    ):
        VaultConfig.from_env()


class TestMaxNoteReadBytesEnv:
    """MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES env loader."""

    def test_default_is_262144(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", raising=False)
        config = VaultConfig.from_env()
        assert config.content.max_note_read_bytes == 262144

    def test_override_via_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "1048576")
        config = VaultConfig.from_env()
        assert config.content.max_note_read_bytes == 1048576

    def test_zero_disables_limit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "0")
        config = VaultConfig.from_env()
        assert config.content.max_note_read_bytes == 0

    def test_invalid_value_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A non-numeric MAX_NOTE_READ_BYTES raises (no warn-and-default; #638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "not-a-number")
        with pytest.raises(ConfigurationError, match="MAX_NOTE_READ_BYTES"):
            VaultConfig.from_env()

    def test_negative_value_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A negative MAX_NOTE_READ_BYTES raises (#638)."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "-1")
        with pytest.raises(ConfigurationError, match="max_note_read_bytes"):
            VaultConfig.from_env()


class TestMaxAttachmentSizeMbDefault:
    """Default for MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB tightened in #442."""

    def test_default_is_one_mb(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", raising=False)
        config = VaultConfig.from_env()
        assert config.content.max_attachment_size_mb == 1.0


def test_transfer_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """TransferConfig defaults apply when no env vars are set."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
    cfg = VaultConfig.from_env()
    assert cfg.transfer.ttl_default_s == 3600
    assert cfg.transfer.ttl_max_s == 86400
    assert cfg.transfer.max_upload_bytes == 104857600


def test_transfer_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """TransferConfig reads its three env vars as integers."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/tmp/vault")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S", "120")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S", "600")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES", "2048")
    cfg = VaultConfig.from_env()
    assert cfg.transfer.ttl_default_s == 120
    assert cfg.transfer.ttl_max_s == 600
    assert cfg.transfer.max_upload_bytes == 2048


def test_transfer_config_rejects_default_above_max():
    """TransferConfig refuses a default TTL above the ceiling."""
    with pytest.raises(ConfigurationError, match="ttl_max_s"):
        TransferConfig(ttl_default_s=7200, ttl_max_s=3600)


def test_transfer_config_rejects_nonpositive_upload_cap():
    """TransferConfig refuses a non-positive upload size cap."""
    with pytest.raises(ConfigurationError, match="max_upload_bytes"):
        TransferConfig(max_upload_bytes=0)


class TestConfigHelpers:
    def test_env_int_valid(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import env_int

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "7")
        assert env_int("MARKDOWN_VAULT_MCP", "X", 3) == 7

    def test_env_int_unset_returns_default(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import env_int

        monkeypatch.delenv("MARKDOWN_VAULT_MCP_X", raising=False)
        assert env_int("MARKDOWN_VAULT_MCP", "X", 3) == 3

    def test_env_int_invalid_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import env_int

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "nope")
        with pytest.raises(ConfigurationError):
            env_int("MARKDOWN_VAULT_MCP", "X", 3)

    def test_env_float_invalid_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import env_float

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "nope")
        with pytest.raises(ConfigurationError):
            env_float("MARKDOWN_VAULT_MCP", "X", 1.5)

    def test_opt_int_unset_is_none(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import opt_int

        monkeypatch.delenv("MARKDOWN_VAULT_MCP_X", raising=False)
        assert opt_int("MARKDOWN_VAULT_MCP", "X") is None

    def test_opt_int_empty_string_is_none(self, monkeypatch):
        """An empty/whitespace value is treated as unset (returns None), not invalid."""
        from markdown_vault_mcp.config_sections._helpers import opt_int

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "   ")
        assert opt_int("MARKDOWN_VAULT_MCP", "X") is None

    def test_opt_int_invalid_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import opt_int

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "nope")
        with pytest.raises(ConfigurationError):
            opt_int("MARKDOWN_VAULT_MCP", "X")


class TestIndexingConfigFromEnv:
    def test_sequence_fields_frozen_as_tuples(self):
        """List inputs are stored as tuples so the frozen config is deeply immutable (#639)."""
        from markdown_vault_mcp.config_sections import IndexingConfig

        cfg = IndexingConfig(
            indexed_frontmatter_fields=["a", "b"],
            required_frontmatter=["title"],
            exclude_patterns=[".obsidian/**"],
        )
        assert cfg.indexed_frontmatter_fields == ("a", "b")
        assert isinstance(cfg.indexed_frontmatter_fields, tuple)
        assert isinstance(cfg.required_frontmatter, tuple)
        assert isinstance(cfg.exclude_patterns, tuple)
        # The stored contents cannot be mutated (the actual #639 contract).
        with pytest.raises(AttributeError):
            cfg.exclude_patterns.append("x")  # type: ignore[union-attr]
        # A bare str (itself a Sequence[str]) is rejected, not split into chars.
        with pytest.raises(ConfigurationError, match="must be a sequence of strings"):
            IndexingConfig(exclude_patterns="x.md")
        # None stays None (not coerced to an empty tuple).
        assert IndexingConfig().exclude_patterns is None

    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import IndexingConfig

        for k in (
            "INDEX_PATH",
            "STATE_PATH",
            "EMBEDDINGS_PATH",
            "INDEXED_FIELDS",
            "REQUIRED_FIELDS",
            "EXCLUDE",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        cfg = IndexingConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg == IndexingConfig()

    def test_index_path_set(self, monkeypatch):
        from pathlib import Path

        from markdown_vault_mcp.config_sections import IndexingConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", "/x/index.db")
        assert IndexingConfig.from_env("MARKDOWN_VAULT_MCP").index_path == Path(
            "/x/index.db"
        )

    def test_list_fields_parsed(self, monkeypatch):
        from markdown_vault_mcp.config_sections import IndexingConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "a, b, c")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", ".obsidian/**")
        cfg = IndexingConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.indexed_frontmatter_fields == ("a", "b", "c")
        assert cfg.exclude_patterns == (".obsidian/**",)

    def test_empty_list_yields_none(self, monkeypatch):
        from markdown_vault_mcp.config_sections import IndexingConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "")
        cfg = IndexingConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.indexed_frontmatter_fields is None

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import IndexingConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            IndexingConfig().index_path = None  # type: ignore[misc]


class TestEmbeddingsConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        for k in (
            "EMBEDDING_PROVIDER",
            "OLLAMA_MODEL",
            "OLLAMA_CPU_ONLY",
            "OPENAI_EMBEDDING_MODEL",
            "FASTEMBED_MODEL",
            "FASTEMBED_CACHE_DIR",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
        cfg = EmbeddingsConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg == EmbeddingsConfig()

    def test_ollama_host_bare_read(self, monkeypatch):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        monkeypatch.setenv("OLLAMA_HOST", "http://gpu-server:11434/")
        cfg = EmbeddingsConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.ollama_host == "http://gpu-server:11434"

    def test_openai_base_url_prefixed_wins(self, monkeypatch):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_OPENAI_BASE_URL", "https://api.prefixed.example/v1"
        )
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.bare.example/v1")
        cfg = EmbeddingsConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_base_url == "https://api.prefixed.example/v1"

    def test_openai_base_url_bare_fallback(self, monkeypatch):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.bare.example/v1")
        cfg = EmbeddingsConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_base_url == "https://api.bare.example/v1"

    def test_openai_api_key_bare_read(self, monkeypatch):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
        cfg = EmbeddingsConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_api_key == "sk-test123"

    def test_post_init_still_normalizes_on_direct_construction(self):
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        assert EmbeddingsConfig(ollama_host="").ollama_host == "http://localhost:11434"
        assert (
            EmbeddingsConfig(ollama_host="http://gpu:11434/").ollama_host
            == "http://gpu:11434"
        )

    def test_post_init_normalizes_openai_base_url_on_direct_construction(self):
        """openai_base_url is normalized on direct construction too, not just from_env (#638)."""
        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        assert (
            EmbeddingsConfig(
                openai_base_url="https://proxy.example/v1/"
            ).openai_base_url
            == "https://proxy.example/v1"
        )
        assert (
            EmbeddingsConfig(openai_base_url="").openai_base_url
            == "https://api.openai.com/v1"
        )

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import EmbeddingsConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            EmbeddingsConfig().provider = "ollama"  # type: ignore[misc]


class TestSearchConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SearchConfig

        for k in (
            "CHUNKS_PER_FILE",
            "SNIPPET_WORDS",
            "LENGTH_DOWNWEIGHT_ALPHA",
            "MAX_CHUNK_WORDS",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        cfg = SearchConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg == SearchConfig()

    def test_overrides(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SearchConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "3")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SNIPPET_WORDS", "100")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA", "0.5")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS", "800")
        cfg = SearchConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.chunks_per_file == 3
        assert cfg.snippet_words == 100
        assert cfg.length_downweight_alpha == 0.5
        assert cfg.max_chunk_words == 800

    def test_chunks_per_file_zero_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SearchConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "0")
        with pytest.raises(ConfigurationError, match="chunks_per_file"):
            SearchConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_chunks_per_file_invalid_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SearchConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "nope")
        with pytest.raises(
            ConfigurationError, match="MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE"
        ):
            SearchConfig.from_env("MARKDOWN_VAULT_MCP")

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("SNIPPET_WORDS", "-1"),
            ("SNIPPET_WORDS", "nope"),
            ("LENGTH_DOWNWEIGHT_ALPHA", "-0.5"),
            ("MAX_CHUNK_WORDS", "0"),
            ("MAX_CHUNK_WORDS", "nope"),
        ],
    )
    def test_invalid_or_out_of_range_raises(self, monkeypatch, var, value):
        from markdown_vault_mcp.config_sections import SearchConfig

        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{var}", value)
        with pytest.raises(ConfigurationError):
            SearchConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_alpha_invalid_raises(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SearchConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA", "abc")
        with pytest.raises(
            ConfigurationError, match="MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA"
        ):
            SearchConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import SearchConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            SearchConfig().chunks_per_file = 5  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"chunks_per_file": 0},
            {"snippet_words": -1},
            {"length_downweight_alpha": -0.5},
            {"max_chunk_words": 0},
            {"max_chunk_chars_override": 0},
        ],
    )
    def test_direct_construction_validates(self, kwargs):
        """__post_init__ rejects out-of-range values on every construction path (#638)."""
        with pytest.raises(ConfigurationError):
            SearchConfig(**kwargs)

    def test_direct_construction_valid_passes(self):
        """In-range values (and a None char-cap override) construct fine."""
        cfg = SearchConfig(
            chunks_per_file=1,
            snippet_words=0,
            length_downweight_alpha=0.0,
            max_chunk_words=1,
            max_chunk_chars_override=None,
        )
        assert cfg.max_chunk_chars_override is None


class TestSyncConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SyncConfig

        for k in ("FILE_WATCHER", "FILE_WATCHER_DEBOUNCE_S", "GITHUB_WEBHOOK_SECRET"):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        cfg = SyncConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg == SyncConfig()

    def test_file_watcher_false(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SyncConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER", "false")
        assert SyncConfig.from_env("MARKDOWN_VAULT_MCP").file_watcher_enabled is False

    def test_debounce_invalid_raises(self, monkeypatch):
        """A non-numeric FILE_WATCHER_DEBOUNCE_S raises (no warn-and-default; #638)."""
        from markdown_vault_mcp.config_sections import SyncConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "notanumber")
        with pytest.raises(ConfigurationError):
            SyncConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_debounce_zero_raises(self, monkeypatch):
        """A zero FILE_WATCHER_DEBOUNCE_S raises (must be > 0; #638)."""
        from markdown_vault_mcp.config_sections import SyncConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "0")
        with pytest.raises(ConfigurationError, match="file_watcher_debounce_s"):
            SyncConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_debounce_negative_raises(self, monkeypatch):
        """A negative FILE_WATCHER_DEBOUNCE_S raises (#638)."""
        from markdown_vault_mcp.config_sections import SyncConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S", "-1.0")
        with pytest.raises(ConfigurationError, match="file_watcher_debounce_s"):
            SyncConfig.from_env("MARKDOWN_VAULT_MCP")

    @pytest.mark.parametrize("debounce", [0, -1.0])
    def test_direct_construction_validates(self, debounce):
        """__post_init__ rejects a non-positive debounce on direct construction (#638)."""
        from markdown_vault_mcp.config_sections import SyncConfig

        with pytest.raises(ConfigurationError, match="file_watcher_debounce_s"):
            SyncConfig(file_watcher_debounce_s=debounce)

    def test_github_webhook_secret(self, monkeypatch):
        from markdown_vault_mcp.config_sections import SyncConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET", "mysecret")
        assert (
            SyncConfig.from_env("MARKDOWN_VAULT_MCP").github_webhook_secret
            == "mysecret"
        )

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import SyncConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            SyncConfig().file_watcher_enabled = False  # type: ignore[misc]


class TestContentConfigFromEnv:
    def test_attachment_extensions_frozen_as_tuple(self):
        """A list of extensions is stored as a tuple for deep immutability (#639)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        cfg = ContentConfig(attachment_extensions=["png", "pdf"])
        assert cfg.attachment_extensions == ("png", "pdf")
        assert isinstance(cfg.attachment_extensions, tuple)
        # The stored contents cannot be mutated (the actual #639 contract).
        with pytest.raises(AttributeError):
            cfg.attachment_extensions.append("x")  # type: ignore[union-attr]
        # A bare str (itself a Sequence[str]) is rejected, not split into chars.
        with pytest.raises(ConfigurationError, match="must be a sequence of strings"):
            ContentConfig(attachment_extensions="pdf")
        assert ContentConfig().attachment_extensions is None

    def test_defaults(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        for k in (
            "ATTACHMENT_EXTENSIONS",
            "MAX_ATTACHMENT_SIZE_MB",
            "MAX_NOTE_READ_BYTES",
            "TEMPLATES_FOLDER",
            "PROMPTS_FOLDER",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg == ContentConfig()

    def test_attachment_extensions_wildcard(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "*")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.attachment_extensions == ("*",)

    def test_attachment_extensions_list(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "pdf,png,docx")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.attachment_extensions == ("pdf", "png", "docx")

    def test_attachment_extensions_empty_is_none(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS", "")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.attachment_extensions is None

    def test_max_attachment_invalid_raises(self, monkeypatch, tmp_path):
        """A non-numeric MAX_ATTACHMENT_SIZE_MB raises (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "not-a-number")
        with pytest.raises(ConfigurationError):
            ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)

    def test_max_attachment_negative_raises(self, monkeypatch, tmp_path):
        """A negative MAX_ATTACHMENT_SIZE_MB raises (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "-5")
        with pytest.raises(ConfigurationError, match="max_attachment_size_mb"):
            ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)

    def test_max_attachment_zero_allowed(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB", "0")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.max_attachment_size_mb == 0.0

    def test_max_note_read_bytes_invalid_raises(self, monkeypatch, tmp_path):
        """A non-numeric MAX_NOTE_READ_BYTES raises (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "nope")
        with pytest.raises(ConfigurationError):
            ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)

    def test_max_note_read_bytes_negative_raises(self, monkeypatch, tmp_path):
        """A negative MAX_NOTE_READ_BYTES raises (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "-1")
        with pytest.raises(ConfigurationError, match="max_note_read_bytes"):
            ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)

    def test_max_note_read_bytes_zero_allowed(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "0")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.max_note_read_bytes == 0

    @pytest.mark.parametrize(
        "kwargs",
        [{"max_attachment_size_mb": -1.0}, {"max_note_read_bytes": -1}],
    )
    def test_direct_construction_validates(self, kwargs):
        """__post_init__ rejects negative size limits on direct construction (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        with pytest.raises(ConfigurationError):
            ContentConfig(**kwargs)

    @pytest.mark.parametrize(
        "kwargs", [{"max_attachment_size_mb": 0}, {"max_note_read_bytes": 0}]
    )
    def test_direct_construction_zero_allowed(self, kwargs):
        """0 (the unlimited sentinel) is accepted on direct construction (#638)."""
        from markdown_vault_mcp.config_sections import ContentConfig

        assert ContentConfig(**kwargs) is not None

    def test_templates_folder_backslash_trailing_slash(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "Templates\\Notes\\")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.templates_folder == "Templates/Notes"

    def test_templates_folder_slash_only_falls_back(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER", "/")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.templates_folder == "_templates"

    def test_prompts_folder_relative_joined_to_source_dir(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", "prompts")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.prompts_folder == str(tmp_path / "prompts")

    def test_prompts_folder_absolute_kept(self, monkeypatch, tmp_path):
        from markdown_vault_mcp.config_sections import ContentConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", "/abs/prompts")
        cfg = ContentConfig.from_env("MARKDOWN_VAULT_MCP", tmp_path)
        assert cfg.prompts_folder == "/abs/prompts"

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import ContentConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            ContentConfig().templates_folder = "other"  # type: ignore[misc]


class TestTransferConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import TransferConfig

        for k in (
            "TRANSFER_TTL_DEFAULT_S",
            "TRANSFER_TTL_MAX_S",
            "TRANSFER_MAX_UPLOAD_BYTES",
        ):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        cfg = TransferConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg == TransferConfig()

    def test_env_overrides(self, monkeypatch):
        from markdown_vault_mcp.config_sections import TransferConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S", "120")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S", "600")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES", "2048")
        cfg = TransferConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.ttl_default_s == 120
        assert cfg.ttl_max_s == 600
        assert cfg.max_upload_bytes == 2048

    def test_invalid_raises(self, monkeypatch):
        """A non-numeric TRANSFER_TTL_DEFAULT_S raises (no warn-and-default; #638)."""
        from markdown_vault_mcp.config_sections import TransferConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S", "nope")
        with pytest.raises(ConfigurationError):
            TransferConfig.from_env("MARKDOWN_VAULT_MCP")

    def test_post_init_raises_on_default_above_max(self, monkeypatch):
        from markdown_vault_mcp.config_sections import TransferConfig

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S", "7200")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S", "3600")
        with pytest.raises(ConfigurationError, match="ttl_max_s"):
            TransferConfig.from_env("MARKDOWN_VAULT_MCP")

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"ttl_default_s": 0}, "ttl_default_s"),
            ({"ttl_default_s": 7200, "ttl_max_s": 3600}, "ttl_max_s"),
            ({"max_upload_bytes": 0}, "max_upload_bytes"),
        ],
    )
    def test_direct_construction_validates(self, kwargs, match):
        """__post_init__ rejects out-of-range/ordering violations on direct construction (#638)."""
        from markdown_vault_mcp.config_sections import TransferConfig

        with pytest.raises(ConfigurationError, match=match):
            TransferConfig(**kwargs)

    def test_frozen(self):
        import dataclasses

        from markdown_vault_mcp.config_sections import TransferConfig

        with pytest.raises(dataclasses.FrozenInstanceError):
            TransferConfig().ttl_default_s = 999  # type: ignore[misc]
