"""The ``(family, name)`` registry of store backends (#1766).

A configuration value names a backend; the family keeps keyword and vector
backends that share a name (``sqlite`` for both, from #1497) apart. The
registry holds factories, not instances: a backend carries configuration,
so the caller constructs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.interfaces import StoreBackend
    from markdown_vault_mcp.types import StoreFamily

_BACKENDS: dict[tuple[str, str], Callable[..., StoreBackend[Any]]] = {}


def register_backend(
    family: StoreFamily, name: str, factory: Callable[..., StoreBackend[Any]]
) -> None:
    """Register *factory* under ``(family, name)``.

    Registering the same factory twice is fine (a module imported twice);
    a different one under a taken key is a programming error.

    Args:
        family: The store family.
        name: The configuration value that selects the backend.
        factory: What the caller constructs the backend with.

    Raises:
        ValueError: If the key holds a different factory already.
    """
    key = (family, name)
    existing = _BACKENDS.get(key)
    if existing is not None and existing is not factory:
        raise ValueError(f"Backend {family}/{name} is already registered.")
    _BACKENDS[key] = factory


def get_backend(family: StoreFamily, name: str) -> Callable[..., StoreBackend[Any]]:
    """Return the factory registered under ``(family, name)``.

    Raises:
        LookupError: If nothing is registered there; the message names the
            registered backends of that family.
    """
    try:
        return _BACKENDS[(family, name)]
    except KeyError:
        known = ", ".join(backend_names(family)) or "none"
        raise LookupError(
            f"No {family} backend named {name!r}; registered: {known}."
        ) from None


def backend_names(family: StoreFamily) -> tuple[str, ...]:
    """Return the registered names of *family*, sorted."""
    return tuple(sorted(name for fam, name in _BACKENDS if fam == family))
