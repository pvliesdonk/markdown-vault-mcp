"""Tests for the LLM-backed summarize feature.

Covers the provider-neutral config, the Summarizer abstraction + factory, the
SummarizeManager orchestration, the SummarizeFacet/Vault wiring, and the
end-to-end MCP tool gating. Uses a deterministic fake Summarizer and a fake
``openai`` module so no network or real SDK is required.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.config import ProjectConfig, to_vault_kwargs
from markdown_vault_mcp.config_sections import SummarizeConfig
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.server import make_server
from markdown_vault_mcp.summarizer import (
    OpenAISummarizer,
    Summarizer,
    get_summarizer,
)
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_mcp_writer_drain

_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
)


class FakeSummarizer(Summarizer):
    """Deterministic summarizer that records its prompts."""

    def __init__(self, text: str = "FAKE SUMMARY") -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def summarize(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.text

    @property
    def provider_name(self) -> str:
        return "fake"


VaultFactory = Callable[..., Vault]


@pytest.fixture
def make_vault(tmp_path: Path) -> Iterator[VaultFactory]:
    """Factory building a built Vault over a small tmp vault; closes on teardown."""
    built: list[Vault] = []

    def _factory(
        *,
        summarizer: Summarizer | None = None,
        notes: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Vault:
        root = tmp_path / f"vault{len(built)}"
        root.mkdir()
        content = notes or {
            "alpha.md": "# Alpha\n\nAlpha body about cats.",
            "beta.md": "# Beta\n\nBeta body about dogs.",
            "sub/one.md": "# One\n\nSub one body.",
            "sub/two.md": "# Two\n\nSub two body.",
        }
        for rel, text in content.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        vault = Vault(source_dir=root, summarizer=summarizer, **kwargs)
        vault.index.build_index()
        built.append(vault)
        return vault

    yield _factory
    for vault in built:
        vault.close()


# ---------------------------------------------------------------------------
# SummarizeConfig
# ---------------------------------------------------------------------------


class TestSummarizeConfig:
    def test_from_env_reads_bare_key_and_prefixed_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL", "gpt-5")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SUMMARIZE_MAX_NOTES", "7")
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.has_provider() is True
        assert cfg.openai_api_key == "sk-test"
        assert cfg.openai_model == "gpt-5"
        assert cfg.max_notes == 7

    def test_defaults_no_key(self) -> None:
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.has_provider() is False
        assert cfg.openai_model == "gpt-5-mini"
        assert cfg.openai_base_url is None
        assert cfg.provider is None
        # Sized for reasoning models, whose budget covers internal
        # reasoning tokens as well as the visible summary (#919).
        assert cfg.max_tokens == 8192

    def test_blank_key_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_api_key is None
        assert cfg.has_provider() is False

    def test_prefixed_key_wins_over_bare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-bare")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY", "sk-prefixed")
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_api_key == "sk-prefixed"

    def test_prefixed_base_url_alone_enables_keyless(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
            "http://localhost:11434/v1/",
        )
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_api_key is None
        # __post_init__ strips the trailing slash.
        assert cfg.openai_base_url == "http://localhost:11434/v1"
        assert cfg.has_provider() is True

    def test_bare_base_url_alone_does_not_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A bare OPENAI_BASE_URL set purely for embeddings must not
        # surprise-enable the summarize tool.
        monkeypatch.setenv("OPENAI_BASE_URL", "http://proxy.example/v1")
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_base_url is None
        assert cfg.has_provider() is False

    def test_bare_base_url_routes_when_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://proxy.example/v1")
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_base_url == "http://proxy.example/v1"

    def test_prefixed_base_url_wins_over_bare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://bare.example/v1")
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
            "http://prefixed.example/v1",
        )
        cfg = SummarizeConfig.from_env("MARKDOWN_VAULT_MCP")
        assert cfg.openai_base_url == "http://prefixed.example/v1"

    def test_blank_base_url_is_unset(self) -> None:
        cfg = SummarizeConfig(openai_base_url="   ")
        assert cfg.openai_base_url is None
        assert cfg.has_provider() is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_tokens": 0},
            {"max_notes": 0},
            {"max_input_chars": 0},
        ],
    )
    def test_validation_rejects_non_positive(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            SummarizeConfig(**kwargs)


# ---------------------------------------------------------------------------
# get_summarizer factory
# ---------------------------------------------------------------------------


def _config_with(summarize: SummarizeConfig, tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(source_dir=tmp_path, summarize=summarize)


class TestGetSummarizer:
    def test_no_backend_raises_runtime_error(self, tmp_path: Path) -> None:
        cfg = _config_with(SummarizeConfig(), tmp_path)
        with pytest.raises(RuntimeError, match="No summarization backend"):
            get_summarizer(cfg)

    def test_unknown_provider_raises_configuration_error(self, tmp_path: Path) -> None:
        cfg = _config_with(
            SummarizeConfig(provider="bogus", openai_api_key="k"), tmp_path
        )
        with pytest.raises(ConfigurationError, match="Unrecognised summarize"):
            get_summarizer(cfg)

    def test_removed_anthropic_provider_raises_migration_error(
        self, tmp_path: Path
    ) -> None:
        cfg = _config_with(
            SummarizeConfig(provider="anthropic", openai_api_key="k"), tmp_path
        )
        with pytest.raises(ConfigurationError, match="was removed"):
            get_summarizer(cfg)

    def test_explicit_openai_without_backend_raises_eagerly(
        self, tmp_path: Path
    ) -> None:
        # Explicit provider with no key and no base URL must fail at config
        # time, not at request time with a placeholder key.
        cfg = _config_with(SummarizeConfig(provider="openai"), tmp_path)
        with pytest.raises(RuntimeError, match="no backend is configured"):
            get_summarizer(cfg)

    def test_missing_sdk_raises_import_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force `import openai` to fail regardless of install state.
        monkeypatch.setitem(sys.modules, "openai", None)
        cfg = _config_with(SummarizeConfig(openai_api_key="k"), tmp_path)
        with pytest.raises(ImportError, match="markdown-vault-mcp\\[summarize\\]"):
            get_summarizer(cfg)


# ---------------------------------------------------------------------------
# OpenAISummarizer (against a fake `openai` module)
# ---------------------------------------------------------------------------


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str | None = "SUMMARY",
    refusal: str | None = None,
    finish_reason: str | None = "stop",
    no_choices: bool = False,
    raise_error: str | None = None,
    reject_max_completion_tokens: bool = False,
    reject_reasoning_effort: bool = False,
) -> dict[str, Any]:
    """Inject a minimal fake ``openai`` module; return a capture dict.

    The capture dict records the constructor's ``api_key`` / ``base_url`` and
    a ``calls`` list with the kwargs of every ``chat.completions.create``
    invocation (a list, so the token-parameter retry can assert both calls).
    """
    captured: dict[str, Any] = {"calls": []}

    class OpenAIError(Exception):
        pass

    class BadRequestError(OpenAIError):
        pass

    class _Message:
        def __init__(self) -> None:
            self.content = content
            self.refusal = refusal

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()
            self.finish_reason = finish_reason

    class _Response:
        def __init__(self) -> None:
            self.choices = [] if no_choices else [_Choice()]

    class _Completions:
        def create(self, **kwargs: Any) -> _Response:
            captured["calls"].append(kwargs)
            if raise_error == "api":
                raise OpenAIError("upstream boom")
            if raise_error == "bad_request":
                raise BadRequestError("model not found")
            if reject_reasoning_effort and "reasoning_effort" in kwargs:
                raise BadRequestError("Unsupported parameter: 'reasoning_effort'")
            if reject_max_completion_tokens and "max_completion_tokens" in kwargs:
                raise BadRequestError("Unsupported parameter: 'max_completion_tokens'")
            return _Response()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class OpenAI:
        def __init__(
            self, api_key: str | None = None, base_url: str | None = None
        ) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = OpenAI  # type: ignore[attr-defined]
    mod.OpenAIError = OpenAIError  # type: ignore[attr-defined]
    mod.BadRequestError = BadRequestError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", mod)
    return captured


class TestOpenAISummarizer:
    def test_summarize_sends_expected_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch, content="the answer")
        summ = OpenAISummarizer("sk-k", "gpt-5-mini", max_tokens=99)
        out = summ.summarize("SYS", "USR")
        assert out == "the answer"
        assert captured["api_key"] == "sk-k"
        assert captured["base_url"] is None
        (kwargs,) = captured["calls"]
        assert kwargs["model"] == "gpt-5-mini"
        assert kwargs["max_completion_tokens"] == 99
        assert kwargs["reasoning_effort"] == "low"
        assert "temperature" not in kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ]
        assert summ.provider_name == "openai"

    def test_keyless_uses_placeholder_and_threads_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch)
        summ = OpenAISummarizer(
            None, "llama3.2", base_url="http://localhost:11434/v1", max_tokens=10
        )
        summ.summarize("s", "u")
        assert captured["api_key"] == "ollama"
        assert captured["base_url"] == "http://localhost:11434/v1"

    def test_token_param_fallback_retries_with_max_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch, reject_max_completion_tokens=True)
        summ = OpenAISummarizer("k", "old-model", max_tokens=42)
        assert summ.summarize("s", "u") == "SUMMARY"
        first, second = captured["calls"]
        assert first["max_completion_tokens"] == 42
        assert "max_completion_tokens" not in second
        assert second["max_tokens"] == 42
        # The unrelated adaptive parameter survives the token-param retry.
        assert second["reasoning_effort"] == "low"

    def test_reasoning_effort_fallback_drops_parameter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch, reject_reasoning_effort=True)
        summ = OpenAISummarizer("k", "strict-model", max_tokens=42)
        assert summ.summarize("s", "u") == "SUMMARY"
        first, second = captured["calls"]
        assert first["reasoning_effort"] == "low"
        assert "reasoning_effort" not in second
        assert second["max_completion_tokens"] == 42

    def test_both_adaptive_parameters_fall_back_sequentially(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(
            monkeypatch,
            reject_reasoning_effort=True,
            reject_max_completion_tokens=True,
        )
        summ = OpenAISummarizer("k", "very-strict-model", max_tokens=42)
        assert summ.summarize("s", "u") == "SUMMARY"
        assert len(captured["calls"]) == 3
        final = captured["calls"][-1]
        assert "reasoning_effort" not in final
        assert "max_completion_tokens" not in final
        assert final["max_tokens"] == 42

    def test_unrelated_bad_request_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch, raise_error="bad_request")
        summ = OpenAISummarizer("k", "m", max_tokens=10)
        with pytest.raises(
            RuntimeError, match="OpenAI-compatible summarization failed"
        ):
            summ.summarize("s", "u")
        assert len(captured["calls"]) == 1

    def test_api_error_becomes_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_openai(monkeypatch, raise_error="api")
        summ = OpenAISummarizer("k", "m", max_tokens=10)
        with pytest.raises(
            RuntimeError, match="OpenAI-compatible summarization failed"
        ):
            summ.summarize("s", "u")

    def test_refusal_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_openai(monkeypatch, content=None, refusal="nope")
        summ = OpenAISummarizer("k", "m", max_tokens=10)
        with pytest.raises(RuntimeError, match="was refused: nope"):
            summ.summarize("s", "u")

    def test_empty_content_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_openai(monkeypatch, content=None, finish_reason="stop")
        summ = OpenAISummarizer("k", "m", max_tokens=10)
        with pytest.raises(RuntimeError, match="returned no text"):
            summ.summarize("s", "u")

    def test_empty_content_on_length_hints_at_max_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reasoning models can burn the whole budget on internal reasoning;
        # the error must point the operator at the knob to turn (#919).
        _install_fake_openai(monkeypatch, content=None, finish_reason="length")
        summ = OpenAISummarizer("k", "gpt-5-mini", max_tokens=10)
        with pytest.raises(RuntimeError, match="SUMMARIZE_MAX_TOKENS"):
            summ.summarize("s", "u")

    def test_empty_choices_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_openai(monkeypatch, no_choices=True)
        summ = OpenAISummarizer("k", "m", max_tokens=10)
        with pytest.raises(RuntimeError, match="returned no text"):
            summ.summarize("s", "u")

    def test_get_summarizer_auto_detects_on_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_openai(monkeypatch)
        cfg = _config_with(SummarizeConfig(openai_api_key="k"), tmp_path)
        assert isinstance(get_summarizer(cfg), OpenAISummarizer)

    def test_get_summarizer_auto_detects_on_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_openai(monkeypatch)
        cfg = _config_with(
            SummarizeConfig(openai_base_url="http://localhost:11434/v1"), tmp_path
        )
        assert isinstance(get_summarizer(cfg), OpenAISummarizer)
        assert captured["base_url"] == "http://localhost:11434/v1"

    def test_get_summarizer_explicit_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_openai(monkeypatch)
        cfg = _config_with(
            SummarizeConfig(provider="openai", openai_api_key="k"), tmp_path
        )
        assert isinstance(get_summarizer(cfg), OpenAISummarizer)


# ---------------------------------------------------------------------------
# SummarizeManager / SummarizeFacet via Vault
# ---------------------------------------------------------------------------


class TestSummarizeFacet:
    def test_single_note(self, make_vault: VaultFactory) -> None:
        fake = FakeSummarizer("S1")
        vault = make_vault(summarizer=fake)
        result = vault.summarizer.summarize(["alpha.md"])
        assert result.summary == "S1"
        assert [s.path for s in result.sources] == ["alpha.md"]
        assert result.mode == "synthesis"
        assert result.truncated is False
        # The note path and body reached the model.
        _system, user = fake.calls[0]
        assert "alpha.md" in user
        assert "cats" in user

    def test_multi_note_synthesis(self, make_vault: VaultFactory) -> None:
        fake = FakeSummarizer()
        vault = make_vault(summarizer=fake)
        result = vault.summarizer.summarize(["alpha.md", "beta.md"])
        assert [s.path for s in result.sources] == ["alpha.md", "beta.md"]
        system, _user = fake.calls[0]
        assert "reference that note by its path" in system
        # Tool results are terminal; the prompt must forbid assistant-style
        # offers of further help (#921).
        assert "no offers of further help" in system

    def test_subtree_expansion(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer())
        result = vault.summarizer.summarize(["sub"])
        assert {s.path for s in result.sources} == {"sub/one.md", "sub/two.md"}

    def test_dedup(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer())
        result = vault.summarizer.summarize(["alpha.md", "alpha.md"])
        assert [s.path for s in result.sources] == ["alpha.md"]

    def test_per_note_mode(self, make_vault: VaultFactory) -> None:
        fake = FakeSummarizer()
        vault = make_vault(summarizer=fake)
        vault.summarizer.summarize(["alpha.md"], mode="per_note")
        system, _user = fake.calls[0]
        assert "separate concise summary for each note" in system
        assert "no offers of further help" in system

    def test_focus_is_folded_into_system(self, make_vault: VaultFactory) -> None:
        fake = FakeSummarizer()
        vault = make_vault(summarizer=fake)
        vault.summarizer.summarize(["alpha.md"], focus="action items")
        system, _user = fake.calls[0]
        assert "Focus specifically on: action items" in system

    def test_max_notes_truncation(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer(), summarize_max_notes=1)
        result = vault.summarizer.summarize(["sub"])
        assert len(result.sources) == 1
        assert result.truncated is True

    def test_max_input_chars_truncation(self, make_vault: VaultFactory) -> None:
        vault = make_vault(
            summarizer=FakeSummarizer(),
            summarize_max_input_chars=10,
        )
        result = vault.summarizer.summarize(["alpha.md", "beta.md"])
        assert result.truncated is True

    def test_input_cap_exact_boundary_drops_next_note(
        self, make_vault: VaultFactory
    ) -> None:
        # First note body is exactly the cap → not cut, but the second note
        # sees zero remaining and is dropped.
        vault = make_vault(
            summarizer=FakeSummarizer(),
            notes={"a.md": "hello", "b.md": "world"},
            summarize_max_input_chars=5,
        )
        result = vault.summarizer.summarize(["a.md", "b.md"])
        assert [s.path for s in result.sources] == ["a.md"]
        assert result.truncated is True

    def test_subtree_cap_honours_configured_max_notes_above_200(
        self, make_vault: VaultFactory
    ) -> None:
        # get_toc defaults max_notes to 200; a configured cap above that must
        # win, not be silently clamped to 200 by the subtree expansion.
        notes = {f"big/n{i:03d}.md": f"# N{i}\n\nbody {i}" for i in range(205)}
        vault = make_vault(
            summarizer=FakeSummarizer(),
            notes=notes,
            summarize_max_notes=250,
        )
        result = vault.summarizer.summarize(["big"])
        assert len(result.sources) == 205
        assert result.truncated is False

    def test_empty_subtree_raises_no_notes_found(
        self, make_vault: VaultFactory
    ) -> None:
        # A folder that exists but contains no markdown resolves to zero notes.
        vault = make_vault(
            summarizer=FakeSummarizer(),
            notes={"docs/notes.txt": "not markdown", "top.md": "# Top\n\nx"},
        )
        with pytest.raises(ValueError, match="No notes found"):
            vault.summarizer.summarize(["docs"])

    def test_skips_oversized_note_in_subtree(self, make_vault: VaultFactory) -> None:
        vault = make_vault(
            summarizer=FakeSummarizer(),
            notes={
                "sub/small.md": "# Small\n\ntiny",
                "sub/big.md": "# Big\n\n" + ("x " * 500),
            },
            max_note_read_bytes=64,
        )
        result = vault.summarizer.summarize(["sub"])
        assert [s.path for s in result.sources] == ["sub/small.md"]

    def test_invalid_mode_raises(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer())
        with pytest.raises(ValueError, match="mode must be one of"):
            vault.summarizer.summarize(["alpha.md"], mode="nope")

    def test_empty_paths_raises(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer())
        with pytest.raises(ValueError, match="at least one"):
            vault.summarizer.summarize([])

    def test_no_readable_notes_raises(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=FakeSummarizer())
        with pytest.raises(ValueError, match="No readable notes"):
            vault.summarizer.summarize(["missing.md"])

    def test_unconfigured_vault_raises(self, make_vault: VaultFactory) -> None:
        vault = make_vault(summarizer=None)
        with pytest.raises(RuntimeError, match="Summarization is not configured"):
            _ = vault.summarizer


# ---------------------------------------------------------------------------
# config.to_vault_kwargs summarizer gating
# ---------------------------------------------------------------------------


class TestToVaultKwargsSummarizer:
    def test_backend_loaded_passes_summarizer_and_caps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_openai(monkeypatch)
        cfg = _config_with(
            SummarizeConfig(openai_api_key="k", max_notes=9, max_input_chars=1234),
            tmp_path,
        )
        kwargs = to_vault_kwargs(cfg)
        assert isinstance(kwargs["summarizer"], OpenAISummarizer)
        assert kwargs["summarize_max_notes"] == 9
        assert kwargs["summarize_max_input_chars"] == 1234

    def test_explicit_provider_load_failure_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "openai", None)  # force ImportError
        cfg = _config_with(
            SummarizeConfig(provider="openai", openai_api_key="k"), tmp_path
        )
        with pytest.raises(ConfigurationError, match="explicitly configured"):
            to_vault_kwargs(cfg)

    def test_removed_anthropic_provider_raises_migration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_openai(monkeypatch)
        cfg = _config_with(
            SummarizeConfig(provider="anthropic", openai_api_key="k"), tmp_path
        )
        with pytest.raises(ConfigurationError, match="was removed"):
            to_vault_kwargs(cfg)

    def test_autodetect_load_failure_disables_silently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "openai", None)  # force ImportError
        cfg = _config_with(SummarizeConfig(openai_api_key="k"), tmp_path)
        kwargs = to_vault_kwargs(cfg)
        assert "summarizer" not in kwargs

    def test_no_provider_no_summarizer(self, tmp_path: Path) -> None:
        cfg = _config_with(SummarizeConfig(), tmp_path)
        kwargs = to_vault_kwargs(cfg)
        assert "summarizer" not in kwargs


# ---------------------------------------------------------------------------
# End-to-end MCP tool gating
# ---------------------------------------------------------------------------


def _base_env(monkeypatch: pytest.MonkeyPatch, source_dir: Path) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source_dir))
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


async def test_summarize_hidden_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "simple.md").write_text("# Simple\n\nhi", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    server = make_server()
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert "summarize" not in names


async def test_summarize_visible_with_base_url_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keyless local endpoints (Ollama) enable the tool via the prefixed
    # base URL alone.
    (tmp_path / "simple.md").write_text("# Simple\n\nhi", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://localhost:11434/v1"
    )
    monkeypatch.setattr(
        "markdown_vault_mcp.summarizer.get_summarizer",
        lambda _config: FakeSummarizer(),
    )
    server = make_server()
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert "summarize" in names


async def test_summarize_visible_and_callable_with_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "simple.md").write_text("# Simple\n\nabout cats", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake = FakeSummarizer("SERVER SUMMARY")
    monkeypatch.setattr(
        "markdown_vault_mcp.summarizer.get_summarizer",
        lambda _config: fake,
    )

    server = make_server()
    async with Client(server) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert "summarize" in tools
        ann = tools["summarize"].annotations
        assert ann is not None
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False

        await wait_for_mcp_writer_drain(client)
        result = await client.call_tool("summarize", {"paths": ["simple.md"]})
    data = result.data
    assert data["summary"] == "SERVER SUMMARY"
    assert data["sources"] == [{"path": "simple.md", "title": "Simple"}]
    # The note content reached the (fake) model.
    assert "cats" in fake.calls[0][1]
