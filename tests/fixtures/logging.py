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


MANAGED_LOGGERS = (
    "fastmcp",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "mcp.server.lowlevel.server",
    "httpx",
    "httpcore",
    "docket.worker",
)
"""Every logger ``configure_logging_from_env`` mutates (ported from pvl-core's
``tests/conftest.py`` at v10.2.0, #2276).

One list, in one place: the logging-setup and serve tests both snapshot this
topology, and a copy in each would drift the moment the policy gained a logger.
"""


@contextmanager
def preserve_logging_topology() -> Iterator[None]:
    """Snapshot root and every managed logger, and put them back afterwards.

    Handlers, level, propagation, filters and the ``disabled`` flag, plus
    FastMCP's ``log_enabled``: everything ``configure_logging_from_env`` or a
    uvicorn server's own logging setup can change.
    """
    import fastmcp

    root = logging.getLogger()
    saved_root = (root.handlers[:], root.level)
    saved = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            logging.getLogger(name).filters[:],
            logging.getLogger(name).disabled,
        )
        for name in MANAGED_LOGGERS
    }
    saved_log_enabled = fastmcp.settings.log_enabled
    try:
        yield
    finally:
        root.handlers[:] = saved_root[0]
        root.setLevel(saved_root[1])
        for name, (handlers, level, propagate, filters, disabled) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.filters[:] = filters
            logger.disabled = disabled
        fastmcp.settings.log_enabled = saved_log_enabled


@pytest.fixture
def restore_logging_topology() -> Iterator[None]:
    """Snapshot the whole logging topology and put it back afterwards.

    Root handlers included: tests that call ``configure_logging_from_env``
    install and remove handlers at root, and without this the first one to run
    leaves the rest of the suite, and pytest's own ``caplog``, on a tree it did
    not expect. Not autouse: only the modules that reconfigure logging pay for
    it.
    """
    with preserve_logging_topology():
        yield
