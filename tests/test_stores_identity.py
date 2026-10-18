"""The embedding-identity check the SQLite and PostgreSQL stores share (#1504)."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import StoreCorruptError, StoreIncompatibleError
from markdown_vault_mcp.stores._identity import (
    identity_from_mapping,
    identity_mapping,
    served_identity,
)
from markdown_vault_mcp.types import EmbeddingIdentity

_ID = EmbeddingIdentity("ollama", "nomic", "", "v1", 768)


def _where() -> dict[str, str]:
    return {"backend": "test", "where": "here"}


def test_identity_round_trips_through_a_mapping() -> None:
    assert identity_from_mapping(identity_mapping(_ID), **_where()) == _ID


@pytest.mark.parametrize(
    "data",
    [
        "not a mapping",
        {"provider": "ollama"},
        {**identity_mapping(_ID), "dimension": "768"},
        {**identity_mapping(_ID), "dimension": -1},
        {**identity_mapping(_ID), "dimension": True},
    ],
)
def test_a_malformed_identity_is_corrupt(data: object) -> None:
    with pytest.raises(StoreCorruptError, match="malformed identity") as failure:
        identity_from_mapping(data, **_where())
    assert (failure.value.backend, failure.value.location) == ("test", "here")


def test_the_same_identity_is_served() -> None:
    assert served_identity(_ID, _ID, **_where()) == _ID


def test_a_wanted_dimension_of_zero_takes_the_stored_one() -> None:
    wanted = EmbeddingIdentity("ollama", "nomic", "", "v1", 0)
    assert served_identity(_ID, wanted, **_where()).dimension == 768


def test_a_stored_dimension_of_zero_takes_the_wanted_one() -> None:
    stored = EmbeddingIdentity("ollama", "nomic", "", "v1", 0)
    assert served_identity(stored, _ID, **_where()).dimension == 768


@pytest.mark.parametrize(
    "other",
    [
        EmbeddingIdentity("openai", "nomic", "", "v1", 768),
        EmbeddingIdentity("ollama", "other", "", "v1", 768),
        EmbeddingIdentity("ollama", "nomic", "q8", "v1", 768),
        EmbeddingIdentity("ollama", "nomic", "", "v2", 768),
        EmbeddingIdentity("ollama", "nomic", "", "v1", 1024),
    ],
)
def test_another_identity_is_incompatible(other: EmbeddingIdentity) -> None:
    with pytest.raises(StoreIncompatibleError, match="Vector store at here holds"):
        served_identity(_ID, other, **_where())
