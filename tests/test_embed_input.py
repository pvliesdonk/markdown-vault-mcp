"""What reaches the embedding provider under ``EMBED_INPUT`` (#2311).

The legacy matrix pins, against values recorded from main before #2311, that
an unset ``EMBED_INPUT`` derives the same provider input and format token as
before for every ``EMBED_CONTEXT`` x searchable-fields combination.
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import embeddings as embeddings_section
from markdown_vault_mcp.config_sections._assembly import to_vault_settings
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.query_types import NoteQuery
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

_PREFIX = "MARKDOWN_VAULT_MCP_"
_TITLE = "Example Agreement"
_FIRST = "First paragraph of the terms.\n" * 16 + "\n"
_SECOND = ("Second paragraph of the conditions.\n" * 16).rstrip("\n")


def _note(summary: str = "initialword", title: str = _TITLE) -> str:
    return (
        f"---\ntitle: {title}\nsummary: {summary}\ntype: contract\n---\n"
        f"# Terms\n{_FIRST}## Conditions\n{_SECOND}\n"
    )


class _ProviderSpy(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Record every input the provider is asked to embed."""
        self.texts.extend(texts)
        return super().embed(texts)


_BODY = [_FIRST, _SECOND]
_CONTEXT = [f"{_TITLE}\nTerms\n\n{_FIRST}", f"{_TITLE}\nConditions\n\n{_SECOND}"]


def _with_fields(value: str) -> list[str]:
    return [f"{_TITLE}\nTerms\n{value}\n\n{_FIRST}", _CONTEXT[1]]


_FIELD_ENVS = {
    "empty": {},
    "explicit": {"SEARCHABLE_FIELDS": "summary"},
    "inherited": {"INDEXED_FIELDS": "type"},
    "none": {"INDEXED_FIELDS": "type", "SEARCHABLE_FIELDS": "none"},
}

# Recorded from main (7b874535) before #2311: (fields, EMBED_CONTEXT) ->
# (format token, provider input).
_LEGACY = {
    ("empty", None): ("v1", _BODY),
    ("empty", "false"): ("v1", _BODY),
    ("empty", "true"): ("v2;fields=", _CONTEXT),
    ("explicit", None): ("v2;fields=summary", _with_fields("initialword")),
    ("explicit", "false"): ("v2;fields=summary", _with_fields("initialword")),
    ("explicit", "true"): ("v2;fields=summary", _with_fields("initialword")),
    ("inherited", None): ("v2;fields=type", _with_fields("contract")),
    ("inherited", "false"): ("v2;fields=type", _with_fields("contract")),
    ("inherited", "true"): ("v2;fields=type", _with_fields("contract")),
    ("none", None): ("v1", _BODY),
    ("none", "false"): ("v1", _BODY),
    ("none", "true"): ("v2;fields=", _CONTEXT),
}


def _settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str]
) -> VaultSettings:
    source = tmp_path / "vault"
    source.mkdir(exist_ok=True)
    monkeypatch.setenv(f"{_PREFIX}SOURCE_DIR", str(source))
    for name, value in env.items():
        monkeypatch.setenv(f"{_PREFIX}{name}", value)
    return dataclasses.replace(
        to_vault_settings(ProjectConfig.from_env()),
        index_path=tmp_path / "fts.db",
        state_path=tmp_path / "state.json",
        embeddings_path=tmp_path / "vectors",
    )


def _open(tmp_path: Path, settings: VaultSettings, provider: _ProviderSpy) -> Vault:
    return Vault(
        source_dir=tmp_path / "vault", settings=settings, embedding_provider=provider
    )


def _build(tmp_path: Path, settings: VaultSettings) -> tuple[str, list[str]]:
    (tmp_path / "vault" / "doc.md").write_text(_note(), encoding="utf-8")
    provider = _ProviderSpy()
    with closing(_open(tmp_path, settings, provider)) as vault:
        vault.index.build_index()
        vault.index.build_embeddings()
        token = vault._embed_builder.format_token()
        meta = vault._index_mgr.chunking_meta.searchable_fields
    assert meta == _fields_meta(settings)
    return token, provider.texts


def _fields_meta(settings: VaultSettings) -> str:
    return ",".join(settings.searchable_frontmatter_fields or ())


@pytest.mark.parametrize(("fields", "context"), list(_LEGACY))
def test_unset_embed_input_keeps_the_legacy_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fields: str,
    context: str | None,
) -> None:
    env = dict(_FIELD_ENVS[fields])
    if context is not None:
        env["EMBED_CONTEXT"] = context
    assert (
        _build(tmp_path, _settings(monkeypatch, tmp_path, env))
        == _LEGACY[(fields, context)]
    )


@pytest.mark.parametrize(
    ("embed_input", "expected"),
    [
        ("body", ("v1", _BODY)),
        ("context", ("v2;fields=", _CONTEXT)),
        ("context+fields", ("v2;fields=summary", _with_fields("initialword"))),
    ],
)
def test_explicit_embed_input_selects_the_provider_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    embed_input: str,
    expected: tuple[str, list[str]],
) -> None:
    settings = _settings(
        monkeypatch,
        tmp_path,
        {"SEARCHABLE_FIELDS": "summary", "EMBED_INPUT": embed_input},
    )
    assert _build(tmp_path, settings) == expected


def test_context_without_fields_shares_the_legacy_context_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Identical input, identical token: no spurious re-embed on migrating."""
    settings = _settings(monkeypatch, tmp_path, {"EMBED_INPUT": "context+fields"})
    assert _build(tmp_path, settings) == _LEGACY[("empty", "true")]


@pytest.mark.parametrize("embed_input", ["body", "context"])
def test_frontmatter_only_edit_never_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, embed_input: str
) -> None:
    settings = _settings(
        monkeypatch,
        tmp_path,
        {
            "SEARCHABLE_FIELDS": "summary",
            "INDEXED_FIELDS": "type",
            "EMBED_INPUT": embed_input,
        },
    )
    note = tmp_path / "vault" / "doc.md"
    note.write_text(_note(), encoding="utf-8")
    provider = _ProviderSpy()
    with closing(_open(tmp_path, settings, provider)) as vault:
        vault.index.build_index()
        assert vault.index.build_embeddings() == 2
        provider.texts.clear()

        note.write_text(_note(summary="updatedword"), encoding="utf-8")
        vault.index.reindex()
        vault.index.build_embeddings()
        assert provider.texts == []
        # The keyword index still carries every searchable field.
        page = vault.reader.query_documents(
            NoteQuery(query="updatedword", scope="metadata")
        )
        assert [item["path"] for item in page.items] == ["doc.md"]

    note.write_text(_note(summary="offlineword"), encoding="utf-8")
    with closing(_open(tmp_path, settings, provider)) as vault:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert provider.texts == []


def test_title_edit_re_embeds_under_context_but_not_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under ``context`` the title reaches the provider, so it counts."""
    for embed_input, calls in (("body", 0), ("context", 2)):
        root = tmp_path / embed_input
        root.mkdir()
        settings = _settings(monkeypatch, root, {"EMBED_INPUT": embed_input})
        note = root / "vault" / "doc.md"
        note.write_text(_note(), encoding="utf-8")
        provider = _ProviderSpy()
        with closing(_open(root, settings, provider)) as vault:
            vault.index.build_index()
            vault.index.build_embeddings()
            provider.texts.clear()
            note.write_text(_note(title="Renamed Agreement"), encoding="utf-8")
            vault.index.reindex()
            vault.index.build_embeddings()
        assert len(provider.texts) == calls


@pytest.mark.parametrize("target", ["context", "body"])
def test_dropping_fields_from_the_input_re_embeds_every_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    """Vectors built with field values are never reused for an input without them."""
    env = {"SEARCHABLE_FIELDS": "summary", "EMBED_INPUT": "context+fields"}
    _build(tmp_path, _settings(monkeypatch, tmp_path, env))

    settings = _settings(monkeypatch, tmp_path, {**env, "EMBED_INPUT": target})
    provider = _ProviderSpy()
    with closing(_open(tmp_path, settings, provider)) as vault:
        vault.index.build_index()
        vault.index.build_embeddings()
    assert provider.texts == (_CONTEXT if target == "context" else _BODY)


@pytest.mark.parametrize("embed_input", ["body", "context"])
def test_changing_searchable_fields_outside_the_input_does_not_re_embed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, embed_input: str
) -> None:
    """The keyword index cold-rebuilds; the unchanged inputs keep their vectors."""
    env = {"SEARCHABLE_FIELDS": "summary", "EMBED_INPUT": embed_input}
    _build(tmp_path, _settings(monkeypatch, tmp_path, env))

    settings = _settings(monkeypatch, tmp_path, {**env, "SEARCHABLE_FIELDS": "type"})
    provider = _ProviderSpy()
    with closing(_open(tmp_path, settings, provider)) as vault:
        vault.index.build_index()
        vault.index.build_embeddings()
        page = vault.reader.query_documents(
            NoteQuery(query="contract", scope="metadata")
        )
        assert [item["path"] for item in page.items] == ["doc.md"]
    assert provider.texts == []


# ---------------------------------------------------------------------------
# Configuration: values, conflicts, deprecation
# ---------------------------------------------------------------------------


def _config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str
) -> ProjectConfig:
    monkeypatch.setenv(f"{_PREFIX}SOURCE_DIR", str(tmp_path))
    for name, value in env.items():
        monkeypatch.setenv(f"{_PREFIX}{name}", value)
    return ProjectConfig.from_env()


def test_embed_input_is_normalised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(monkeypatch, tmp_path, EMBED_INPUT=" Context+Fields ")
    assert config.embeddings.embed_input == "context+fields"
    assert to_vault_settings(config).embed_input == "context+fields"


def test_unknown_embed_input_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError, match=f"{_PREFIX}EMBED_INPUT must be"):
        _config(monkeypatch, tmp_path, EMBED_INPUT="fields")
    with pytest.raises(ConfigurationError, match="EMBED_INPUT must be"):
        ProjectConfig(embed_input="title")


def test_embed_context_true_with_body_fails_naming_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        _config(monkeypatch, tmp_path, EMBED_CONTEXT="true", EMBED_INPUT="body")
    message = str(excinfo.value)
    assert f"{_PREFIX}EMBED_CONTEXT=true" in message
    assert f"{_PREFIX}EMBED_INPUT=body" in message
    with pytest.raises(ConfigurationError, match="conflicts"):
        ProjectConfig(embed_context=True, embed_input="body")


@pytest.mark.parametrize(
    ("context", "embed_input"),
    [
        ("true", "context"),
        ("true", "context+fields"),
        ("false", "body"),
        ("false", "context"),
        ("false", "context+fields"),
    ],
)
def test_agreeing_pairs_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context: str,
    embed_input: str,
) -> None:
    config = _config(
        monkeypatch, tmp_path, EMBED_CONTEXT=context, EMBED_INPUT=embed_input
    )
    assert to_vault_settings(config).embed_input == embed_input


def test_library_conflict_raises_value_error(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    with pytest.raises(ValueError, match="conflicts"):
        Vault(
            source_dir=source,
            settings=VaultSettings(embed_context=True, embed_input="body"),
        )


@pytest.fixture
def _fresh_deprecations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_section, "_DEPRECATIONS_LOGGED", set())


def _deprecations(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.msg.startswith("embed_context_env_")]


@pytest.mark.usefixtures("_fresh_deprecations")
@pytest.mark.parametrize("value", ["true", "false"])
def test_setting_embed_context_logs_one_deprecation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    with caplog.at_level(logging.WARNING):
        config = _config(monkeypatch, tmp_path, EMBED_CONTEXT=value)
        for _ in range(3):
            _ = config.embeddings
        dataclasses.replace(config, read_only=True)
        to_vault_settings(config)
        # A CLI command may read the environment twice.
        ProjectConfig.from_env()
    records = _deprecations(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].args == (
        value == "true",
        "context+fields" if value == "true" else "unset",
    )


@pytest.mark.usefixtures("_fresh_deprecations")
def test_deprecation_survives_a_stricter_log_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp"):
        _config(monkeypatch, tmp_path, EMBED_CONTEXT="true")
    assert [r.levelno for r in _deprecations(caplog)] == [logging.ERROR]


@pytest.mark.usefixtures("_fresh_deprecations")
def test_unset_embed_context_logs_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        config = _config(monkeypatch, tmp_path, EMBED_CONTEXT="  ")
    assert config.embed_context is None
    assert not _deprecations(caplog)
