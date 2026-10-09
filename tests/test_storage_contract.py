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
