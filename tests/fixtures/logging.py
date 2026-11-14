"""Root-state and reader capture lifetimes; see docs/design/reference/cli-test-logging.md."""

from __future__ import annotations

import logging
from contextlib import contextmanager, redirect_stderr
from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from rich.logging import RichHandler

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def preserve_root_logging() -> Iterator[None]:
    """Restore incoming root handlers and level without closing their streams."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


@pytest.fixture(autouse=True)
def isolated_root_logging() -> Iterator[None]:
    """Detach test-installed handlers before their pytest capture expires."""
    with preserve_root_logging():
        yield


@contextmanager
def reader_logging() -> Iterator[StringIO]:
    """Keep server logs separate from command capture until reader shutdown.

    Yields:
        The live destination rendered by the real logging bootstrap.
    """
    from markdown_vault_mcp import cli
    from markdown_vault_mcp.config import _ENV_PREFIX

    original = cli.configure_logging_from_env
    root = logging.getLogger()
    with StringIO() as destination, preserve_root_logging():

        def configure(env_prefix: str, *, verbose: bool = False) -> None:
            before = set(root.handlers)
            with redirect_stderr(destination):
                original(env_prefix, verbose=verbose)
                for handler in root.handlers:
                    if handler in before:
                        continue
                    if isinstance(handler, RichHandler):
                        handler.console.file = destination
                    elif isinstance(handler, logging.StreamHandler):
                        handler.setStream(destination)

        with patch.object(cli, "configure_logging_from_env", configure):
            configure(_ENV_PREFIX)
            yield destination
