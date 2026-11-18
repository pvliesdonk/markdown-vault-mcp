"""Resolve independent OKF authority without changing the current write gates."""

from dataclasses import dataclass


class DefaultVerify(str):
    """The legacy public default, distinguished from an explicit selection."""


DEFAULT_VERIFY = DefaultVerify("elicit")


@dataclass(frozen=True)
class OkfCapabilities:
    """Configured OKF capabilities before vault and read-only restrictions."""

    author: bool
    verify: str
    index: bool
    log: str
    folders: tuple[str, ...]
