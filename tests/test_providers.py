"""Tests for embedding providers in markdown_vault_mcp.providers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from markdown_vault_mcp.config import VaultConfig
from markdown_vault_mcp.config_sections import EmbeddingsConfig
from markdown_vault_mcp.providers import (
    FastEmbedProvider,
    OllamaProvider,
    OpenAIProvider,
    get_embedding_provider,
)


def _make_httpx_mock(
    status_code: int = 200,
    json_body: dict | None = None,
    text: str = "",
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_client, mock_response) with context-manager wiring."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    mock_response.json.return_value = json_body or {}

    mock_client = MagicMock()
    mock_client.__enter__ = lambda _: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response

    return mock_client, mock_response


def _config(**embedding_overrides: object) -> VaultConfig:
    """Build a minimal VaultConfig with optional embedding overrides."""
    return VaultConfig(
        source_dir=Path("/tmp/vault"),
        embeddings=EmbeddingsConfig(**embedding_overrides),  # type: ignore[arg-type]
    )


class TestOllamaProvider:
    def test_embed_posts_to_correct_url(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434", model="nomic-embed-text"
            )
            result = provider.embed(["hello"])

        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["model"] == "nomic-embed-text"
        assert result == [[0.1, 0.2, 0.3]]
        assert provider.provider_name == "ollama"
        assert provider.model_name == "nomic-embed-text"

    def test_embed_payload_includes_model_and_input(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.5, 0.6]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(host="http://localhost:11434", model="test-model")
            provider.embed(["alpha", "beta"])

        _, call_kwargs = mock_client.post.call_args
        payload = call_kwargs["json"]
        assert payload["model"] == "test-model"
        assert payload["input"] == ["alpha", "beta"]

    def test_embed_cpu_only_includes_num_gpu_zero(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[1.0, 2.0]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434",
                model="nomic-embed-text",
                cpu_only=True,
            )
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"].get("options") == {"num_gpu": 0}

    def test_embed_cpu_only_false_no_options_key(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[1.0, 2.0]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434",
                model="nomic-embed-text",
                cpu_only=False,
            )
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        assert "options" not in call_kwargs["json"]

    def test_embed_raises_on_non_200_status(self) -> None:
        mock_client, _ = _make_httpx_mock(status_code=503, text="Service Unavailable")
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434", model="nomic-embed-text"
            )
            with pytest.raises(RuntimeError, match="503"):
                provider.embed(["hello"])

    def test_dimension_triggers_embed_on_first_access(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"embeddings": [[0.1, 0.2, 0.3, 0.4]]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434", model="nomic-embed-text"
            )
            assert provider.dimension == 4

    def test_dimension_cached_after_first_embed(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434", model="nomic-embed-text"
            )
            provider.embed(["prime"])
            _ = provider.dimension
            _ = provider.dimension

        assert mock_client.post.call_count == 1

    def test_custom_host_changes_base_url(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.9]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://remote-host:12345", model="nomic-embed-text"
            )
            provider.embed(["x"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://remote-host:12345/api/embed"

    def test_host_trailing_slash_stripped(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://myhost:9999/", model="nomic-embed-text"
            )
            provider.embed(["y"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://myhost:9999/api/embed"

    def test_custom_model(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.3, 0.4]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(
                host="http://localhost:11434", model="my-custom-model"
            )
            provider.embed(["test"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"]["model"] == "my-custom-model"
        assert provider.model_name == "my-custom-model"

    def test_missing_httpx_raises_import_error(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(ImportError, match="httpx"),
        ):
            OllamaProvider(host="http://localhost:11434", model="nomic-embed-text")


class TestOpenAIProvider:
    def test_init_raises_without_api_key(self) -> None:
        with (
            patch("httpx.Client"),
            pytest.raises(RuntimeError, match="non-empty api_key"),
        ):
            OpenAIProvider(api_key="")

    def test_embed_sends_bearer_token(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test-key-123")
            provider.embed(["hello"])

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-key-123"
        assert provider.provider_name == "openai"
        assert provider.model_name == "text-embedding-3-small"

    def test_embed_sorts_by_index(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={
                "data": [
                    {"index": 1, "embedding": [9.0, 8.0]},
                    {"index": 0, "embedding": [1.0, 2.0]},
                ]
            }
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            result = provider.embed(["first", "second"])
        assert result == [[1.0, 2.0], [9.0, 8.0]]

    def test_embed_raises_on_non_200_status(self) -> None:
        mock_client, _ = _make_httpx_mock(status_code=401, text="Unauthorized")
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            with pytest.raises(RuntimeError, match="401"):
                provider.embed(["secret"])

    def test_dimension_caches_after_first_embed(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            provider.embed(["probe"])
            dim1 = provider.dimension
            dim2 = provider.dimension

        assert dim1 == 3
        assert dim2 == 3
        assert mock_client.post.call_count == 1

    def test_dimension_triggers_embed_when_uncached(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.5, 0.6, 0.7, 0.8]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            dim = provider.dimension

        assert dim == 4
        mock_client.post.assert_called_once()

    def test_embed_posts_to_openai_endpoint(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            provider.embed(["hello"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "https://api.openai.com/v1/embeddings"

    def test_custom_base_url_and_model(self) -> None:
        mock_client, _ = _make_httpx_mock(
            json_body={"data": [{"index": 0, "embedding": [0.1]}]}
        )
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(
                api_key="sk-test",
                base_url="https://api.siliconflow.cn/v1/",
                model="BAAI/bge-m3",
            )
            provider.embed(["hello"])

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "https://api.siliconflow.cn/v1/embeddings"
        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"]["model"] == "BAAI/bge-m3"
        assert provider.model_name == "BAAI/bge-m3"


class TestFastEmbedProvider:
    def test_embed_uses_fastembed_model_and_cache(self) -> None:
        vec = MagicMock()
        vec.tolist.return_value = [0.1, 0.2, 0.3]
        model_instance = MagicMock()
        model_instance.embed.return_value = [vec]
        module = MagicMock()
        module.TextEmbedding.return_value = model_instance

        with patch.dict("sys.modules", {"fastembed": module}):
            provider = FastEmbedProvider(
                model_name="nomic-ai/nomic-embed-text-v1.5",
                cache_dir="/tmp/fastembed-cache",
            )
            result = provider.embed(["hello"])

        module.TextEmbedding.assert_called_once_with(
            model_name="nomic-ai/nomic-embed-text-v1.5",
            cache_dir="/tmp/fastembed-cache",
        )
        model_instance.embed.assert_called_once_with(["hello"], batch_size=32)
        assert result == [[0.1, 0.2, 0.3]]
        assert provider.dimension == 3
        assert provider.provider_name == "fastembed"
        assert provider.model_name == "nomic-ai/nomic-embed-text-v1.5"

    def test_missing_fastembed_raises(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fastembed":
                raise ImportError("No module named 'fastembed'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(ImportError, match="fastembed"),
        ):
            FastEmbedProvider()

    def test_dimension_raises_on_empty_embeddings(self) -> None:
        model_instance = MagicMock()
        model_instance.embed.return_value = []
        module = MagicMock()
        module.TextEmbedding.return_value = model_instance

        with patch.dict("sys.modules", {"fastembed": module}):
            provider = FastEmbedProvider()
            with pytest.raises(RuntimeError, match="cannot determine dimension"):
                _ = provider.dimension


class TestGetEmbeddingProvider:
    def _ollama_mock_client(self, reachable: bool = True) -> MagicMock:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda _: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_response = MagicMock()
        mock_response.status_code = 200 if reachable else 503
        mock_client.get.return_value = mock_response
        return mock_client

    def test_explicit_openai_returns_openai_provider(self) -> None:
        cfg = _config(
            provider="openai",
            openai_api_key="sk-test",
            openai_base_url="https://api.siliconflow.cn/v1",
            openai_embedding_model="BAAI/bge-m3",
        )
        with patch("httpx.Client"):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, OpenAIProvider)
        assert provider.model_name == "BAAI/bge-m3"

    def test_explicit_ollama_returns_ollama_provider(self) -> None:
        cfg = _config(provider="ollama")
        with patch("httpx.Client"):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, OllamaProvider)

    def test_explicit_fastembed_returns_fastembed_provider(self) -> None:
        cfg = _config(provider="fastembed")
        module = MagicMock()
        module.TextEmbedding.return_value = MagicMock(embed=lambda *_: [])
        with patch.dict("sys.modules", {"fastembed": module}):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, FastEmbedProvider)

    def test_explicit_unknown_raises_value_error(self) -> None:
        cfg = _config(provider="unknown_value")
        with pytest.raises(
            ValueError, match="Valid values: 'openai', 'ollama', 'fastembed'"
        ):
            get_embedding_provider(cfg)

    def test_autodetect_openai_key_present(self) -> None:
        cfg = _config(openai_api_key="sk-autodetect")
        with patch("httpx.Client"):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, OpenAIProvider)

    def test_autodetect_ollama_reachable(self) -> None:
        cfg = _config()
        probe_client = self._ollama_mock_client(reachable=True)
        ollama_client = MagicMock()
        ollama_client.__enter__ = lambda _: ollama_client
        ollama_client.__exit__ = MagicMock(return_value=False)
        with patch("httpx.Client", side_effect=[probe_client, ollama_client]):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, OllamaProvider)

    def test_autodetect_fastembed_fallback(self) -> None:
        cfg = _config()

        probe_client = MagicMock()
        probe_client.__enter__ = lambda _: probe_client
        probe_client.__exit__ = MagicMock(return_value=False)
        probe_client.get.side_effect = ConnectionError("refused")

        module = MagicMock()
        module.TextEmbedding.return_value = MagicMock(embed=lambda *_: [])

        with (
            patch("httpx.Client", return_value=probe_client),
            patch.dict("sys.modules", {"fastembed": module}),
        ):
            provider = get_embedding_provider(cfg)
        assert isinstance(provider, FastEmbedProvider)

    def test_autodetect_no_providers_raises_runtime_error(self) -> None:
        cfg = _config()

        probe_client = MagicMock()
        probe_client.__enter__ = lambda _: probe_client
        probe_client.__exit__ = MagicMock(return_value=False)
        probe_client.get.side_effect = ConnectionError("refused")

        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fastembed":
                raise ImportError("No module named 'fastembed'")
            return real_import(name, *args, **kwargs)

        with (
            patch("httpx.Client", return_value=probe_client),
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="No embedding provider"),
        ):
            get_embedding_provider(cfg)


def test_embedding_provider_requires_context_length():
    from markdown_vault_mcp.providers import EmbeddingProvider

    assert "context_length" in EmbeddingProvider.__abstractmethods__


def test_ollama_context_length_parses_api_show(monkeypatch):
    from markdown_vault_mcp.providers import OllamaProvider

    class _Resp:
        status_code = 200

        def json(self):
            return {"model_info": {"bert.context_length": 8192}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json, timeout):  # noqa: ARG002
            return _Resp()

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length == 8192
    monkeypatch.setattr(
        p._httpx,
        "Client",
        lambda *a, **k: (_ for _ in ()).throw(  # noqa: ARG005
            AssertionError("re-queried")
        ),
    )
    assert p.context_length == 8192  # cached, no re-query


@pytest.mark.parametrize("err_kind", ["connect", "timeout"])
def test_ollama_context_length_none_on_network_error(monkeypatch, err_kind):
    """An unreachable/slow Ollama (httpx errors, NOT OSError) returns None."""
    import httpx

    from markdown_vault_mcp.providers import OllamaProvider

    exc = (
        httpx.ConnectError("down")
        if err_kind == "connect"
        else httpx.TimeoutException("slow")
    )

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):  # noqa: ARG002
            raise exc

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length is None


def test_ollama_context_length_none_on_malformed_body(monkeypatch):
    """A 200 with a non-JSON body (JSONDecodeError = ValueError) returns None."""
    from markdown_vault_mcp.providers import OllamaProvider

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):  # noqa: ARG002
            return _Resp()

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length is None


def test_ollama_context_length_none_on_non_200(monkeypatch):
    """A non-200 /api/show (e.g. model not pulled) returns None."""
    from markdown_vault_mcp.providers import OllamaProvider

    class _Resp:
        status_code = 404

        def json(self):
            return {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):  # noqa: ARG002
            return _Resp()

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length is None


def test_ollama_context_length_none_when_field_absent(monkeypatch):
    """A 200 whose model_info carries no *.context_length key returns None."""
    from markdown_vault_mcp.providers import OllamaProvider

    class _Resp:
        status_code = 200

        def json(self):
            return {"model_info": {"general.architecture": "bert"}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):  # noqa: ARG002
            return _Resp()

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length is None


@pytest.mark.parametrize(
    "body",
    [
        ["not", "a", "dict"],  # top-level body is a list
        "a string body",  # top-level body is a string
        {"model_info": ["not", "a", "dict"]},  # model_info is a non-dict
        {"model_info": "a string"},  # model_info is a string
    ],
)
def test_ollama_context_length_none_on_non_dict_body(monkeypatch, body):
    """A non-object body OR a non-object model_info returns None, not an
    uncaught AttributeError from .get()/.items()."""
    from markdown_vault_mcp.providers import OllamaProvider

    class _Resp:
        status_code = 200

        def json(self):
            return body

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):  # noqa: ARG002
            return _Resp()

    p = OllamaProvider(host="http://x:11434", model="bge-m3:latest")
    monkeypatch.setattr(p._httpx, "Client", lambda *a, **k: _Client())  # noqa: ARG005
    assert p.context_length is None


def test_fastembed_context_length_table():
    from markdown_vault_mcp.providers import FastEmbedProvider

    p = FastEmbedProvider.__new__(FastEmbedProvider)
    p._model_name = "nomic-ai/nomic-embed-text-v1.5"
    assert p.context_length == 8192
    p._model_name = "BAAI/bge-small-en-v1.5"
    assert p.context_length == 512
    p._model_name = "some/unknown-model"
    assert p.context_length is None


def test_openai_context_length_table():
    from markdown_vault_mcp.providers import OpenAIProvider

    p = OpenAIProvider.__new__(OpenAIProvider)
    p._model = "text-embedding-3-small"
    assert p.context_length == 8191
    p._model = "unknown-embedding-model"
    assert p.context_length is None
