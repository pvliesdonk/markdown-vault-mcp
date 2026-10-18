"""The embedding-identity check vector stores share (#1503, #1504).

A store records the identity its rows were embedded under; an open or a
read compares it with the identity asked for. Provider, model, variant and
text format must match; dimensions must match when both are known, and a
wanted dimension of 0 (not known yet) takes the stored one.
"""

from __future__ import annotations

from collections.abc import Mapping

from markdown_vault_mcp.exceptions import StoreCorruptError, StoreIncompatibleError
from markdown_vault_mcp.types import EmbeddingIdentity

_TEXT_KEYS = ("provider", "model", "variant", "text_format")


def identity_mapping(identity: EmbeddingIdentity) -> dict[str, str | int]:
    """Return *identity* as the mapping stores persist.

    Args:
        identity: The identity.

    Returns:
        Its five fields by name.
    """
    return {
        "provider": identity.provider,
        "model": identity.model,
        "variant": identity.variant,
        "text_format": identity.text_format,
        "dimension": identity.dimension,
    }


def identity_from_mapping(
    data: object, *, backend: str, where: str
) -> EmbeddingIdentity:
    """Read an identity back from what a store persisted.

    Args:
        data: The stored value, parsed from JSON.
        backend: The backend's registry name, for the error.
        where: The store's location, for the error.

    Returns:
        The identity.

    Raises:
        StoreCorruptError: If *data* is not a mapping of the five fields with
            text values and a non-negative integer dimension.
    """
    problem = _identity_problem(data)
    if problem is not None or not isinstance(data, Mapping):
        raise StoreCorruptError(
            f"Vector store at {where} has a malformed identity: {problem}",
            backend=backend,
            location=where,
        )
    return EmbeddingIdentity(
        provider=str(data["provider"]),
        model=str(data["model"]),
        variant=str(data["variant"]),
        text_format=str(data["text_format"]),
        dimension=int(data["dimension"]),
    )


def _identity_problem(data: object) -> str | None:
    """Say what is wrong with *data* as a stored identity, or ``None``."""
    if not isinstance(data, Mapping):
        return f"expected an object, got {type(data).__name__}"
    missing = [key for key in (*_TEXT_KEYS, "dimension") if key not in data]
    if missing:
        return f"missing {', '.join(missing)}"
    if any(not isinstance(data[key], str) for key in _TEXT_KEYS):
        return "a text field is not a string"
    dimension = data["dimension"]
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 0:
        return f"dimension must be a non-negative integer, got {dimension!r}"
    return None


def served_identity(
    stored: EmbeddingIdentity, wanted: EmbeddingIdentity, *, backend: str, where: str
) -> EmbeddingIdentity:
    """Return the identity to serve, refusing another embedding space.

    A *wanted* dimension of 0 does not know it yet: the stored one stands.

    Args:
        stored: The identity the store holds.
        wanted: The identity asked for.
        backend: The backend's registry name, for the error.
        where: The store's location, for the error.

    Returns:
        *wanted*, with the stored dimension when *wanted* has none.

    Raises:
        StoreIncompatibleError: If the two name another embedding space.
    """
    held = (stored.provider, stored.model, stored.variant, stored.text_format)
    asked = (wanted.provider, wanted.model, wanted.variant, wanted.text_format)
    if held != asked:
        raise StoreIncompatibleError(
            f"Vector store at {where} holds {held}, not {asked}.",
            backend=backend,
            location=where,
        )
    if stored.dimension and wanted.dimension and stored.dimension != wanted.dimension:
        raise StoreIncompatibleError(
            f"Vector store at {where} holds dimension {stored.dimension}, "
            f"not {wanted.dimension}.",
            backend=backend,
            location=where,
        )
    return EmbeddingIdentity(
        provider=wanted.provider,
        model=wanted.model,
        variant=wanted.variant,
        text_format=wanted.text_format,
        dimension=stored.dimension or wanted.dimension,
    )
