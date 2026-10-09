"""The generic storage contract: value types, open outcomes, protocols, registry (#1766)."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import (
    MarkdownMCPError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreNotConfiguredError,
    StoreOpenError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.types import SourceState


class TestSourceState:
    def test_present_carries_a_token(self) -> None:
        state = SourceState("present", "abc")
        assert (state.status, state.token) == ("present", "abc")

    def test_present_without_a_token_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="present"):
            SourceState("present")

    @pytest.mark.parametrize("status", ["absent", "unavailable"])
    def test_absent_and_unavailable_carry_no_token(self, status: str) -> None:
        assert SourceState(status).token is None  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=status):
            SourceState(status, "abc")  # type: ignore[arg-type]


class TestStoreOpenOutcomes:
    @pytest.mark.parametrize(
        "outcome",
        [
            StoreNotConfiguredError,
            StoreUnavailableError,
            StoreIncompatibleError,
            StoreUnsupportedSchemaError,
            StoreCorruptError,
        ],
    )
    def test_each_outcome_is_a_store_open_error_naming_its_store(
        self, outcome: type[StoreOpenError]
    ) -> None:
        exc = outcome("why", backend="sqlite", location="/tmp/index.db")
        assert isinstance(exc, StoreOpenError)
        assert isinstance(exc, MarkdownMCPError)
        assert (exc.backend, exc.location) == ("sqlite", "/tmp/index.db")
        assert str(exc) == "why"


class TestProtocols:
    def test_a_fake_lifecycle_satisfies_store_lifecycle(self) -> None:
        from markdown_vault_mcp.interfaces import StoreLifecycle

        class Fake:
            def close(self) -> None: ...

            def checkpoint(self) -> None: ...

        assert isinstance(Fake(), StoreLifecycle)
        assert not isinstance(object(), StoreLifecycle)

    def test_a_fake_backend_satisfies_store_backend(self) -> None:
        from markdown_vault_mcp.interfaces import StoreBackend

        class Fake:
            family = "keyword"
            name = "fake"

            def open(  # a protocol fake, never called
                self,
                _location: object,
                _identity: object | None = None,
                *,
                _owns_files: bool = True,
            ) -> object:
                return self

        assert isinstance(Fake(), StoreBackend)

    def test_a_fake_probe_satisfies_source_probe(self) -> None:
        from markdown_vault_mcp.interfaces import SourceProbe

        class Fake:
            def probe(self, path: str) -> SourceState:  # noqa: ARG002
                return SourceState("absent")

        assert isinstance(Fake(), SourceProbe)


class TestRegistry:
    def test_the_sqlite_keyword_backend_is_registered(self) -> None:
        from markdown_vault_mcp.stores.registry import backend_names, get_backend
        from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend

        assert get_backend("keyword", "sqlite") is SqliteKeywordBackend
        assert "sqlite" in backend_names("keyword")

    def test_an_unknown_name_lists_the_known_ones(self) -> None:
        from markdown_vault_mcp.stores import sqlite_keyword  # noqa: F401
        from markdown_vault_mcp.stores.registry import get_backend

        with pytest.raises(LookupError, match=r"keyword.*nope.*sqlite"):
            get_backend("keyword", "nope")

    def test_registering_a_different_factory_under_a_taken_key_is_refused(
        self,
    ) -> None:
        from markdown_vault_mcp.stores.registry import register_backend
        from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend

        register_backend("keyword", "sqlite", SqliteKeywordBackend)  # same: fine
        with pytest.raises(ValueError, match="already registered"):
            register_backend("keyword", "sqlite", object)  # type: ignore[arg-type]

    def test_families_do_not_collide(self) -> None:
        from markdown_vault_mcp.stores.registry import backend_names

        assert "sqlite" not in backend_names("vector")
