"""Lifecycle conformance every store runs (#1766).

Subclass :class:`StoreLifecycleContract` in a backend's test module and
provide five fixtures: ``open_store`` and ``open_store_read_only``, each a
zero-argument callable returning a fresh open store; ``touch_store``, a
callable that performs one read on a store; ``write_to_store``, a callable
that performs one write; and ``closed_error``, the exception type a closed
store raises on use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.interfaces import StoreLifecycle

if TYPE_CHECKING:
    from collections.abc import Callable


class StoreLifecycleContract:
    """The behaviour behind ``StoreLifecycle``; ``isinstance`` alone proves nothing."""

    def test_satisfies_the_protocol(self, open_store: Callable[[], Any]) -> None:
        store = open_store()
        try:
            assert isinstance(store, StoreLifecycle)
        finally:
            store.close()

    def test_close_is_idempotent(self, open_store: Callable[[], Any]) -> None:
        store = open_store()
        store.close()
        store.close()

    def test_use_after_close_raises(
        self,
        open_store: Callable[[], Any],
        touch_store: Callable[[Any], None],
        closed_error: type[Exception],
    ) -> None:
        store = open_store()
        store.close()
        with pytest.raises(closed_error):
            touch_store(store)

    def test_read_only_open_refuses_writes(
        self,
        open_store_read_only: Callable[[], Any],
        write_to_store: Callable[[Any], None],
    ) -> None:
        store = open_store_read_only()
        try:
            with pytest.raises(Exception, match=r"(?i)read.?only"):
                write_to_store(store)
        finally:
            store.close()

    def test_checkpoint_is_a_no_op_when_read_only(
        self, open_store_read_only: Callable[[], Any]
    ) -> None:
        store = open_store_read_only()
        try:
            store.checkpoint()
        finally:
            store.close()

    def test_checkpoint_runs_when_writable(self, open_store: Callable[[], Any]) -> None:
        store = open_store()
        try:
            store.checkpoint()
        finally:
            store.close()
