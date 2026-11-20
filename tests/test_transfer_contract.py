"""The sink-raisable HTTP status signals (#2319).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_sink.py``, for the
two subclasses this project carries.
"""

from __future__ import annotations

import copy
import pickle
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._transfer_contract import (
    TransferResourceGoneError,
    TransferSinkError,
    TransferUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Every named subclass paired with the status it must map to.
_SUGAR = [
    (TransferResourceGoneError, 410),
    (TransferUnavailableError, 503),
]


class TestTransferSinkErrorBase:
    def test_stores_status_code(self) -> None:
        assert TransferSinkError(418).status_code == 418

    @pytest.mark.parametrize("bad", [399, 200, 600, 0, -1])
    def test_rejects_non_4xx_5xx(self, bad: int) -> None:
        with pytest.raises(ValueError, match="4xx/5xx"):
            TransferSinkError(bad)

    @pytest.mark.parametrize("ok", [400, 499, 500, 599])
    def test_accepts_range_bounds(self, ok: int) -> None:
        assert TransferSinkError(ok).status_code == ok

    def test_message_is_preserved(self) -> None:
        assert str(TransferSinkError(503, "backend down")) == "backend down"


class TestSugarSubclasses:
    @pytest.mark.parametrize(("cls", "status"), _SUGAR)
    def test_maps_to_its_status(
        self, cls: Callable[..., TransferSinkError], status: int
    ) -> None:
        assert cls().status_code == status

    @pytest.mark.parametrize("cls", [cls for cls, _ in _SUGAR])
    def test_is_a_transfer_sink_error(
        self, cls: Callable[..., TransferSinkError]
    ) -> None:
        # A handler catching the base catches every sugar subclass.
        assert isinstance(cls(), TransferSinkError)

    @pytest.mark.parametrize("cls", [cls for cls, _ in _SUGAR])
    def test_preserves_message(self, cls: Callable[..., TransferSinkError]) -> None:
        assert str(cls("nope")) == "nope"


class TestRoundTrip:
    """Exceptions must survive copy/pickle — a signal may cross a process or
    task-queue boundary, and the base stores status_code outside self.args."""

    def test_base_copy_preserves_status_and_message(self) -> None:
        c = copy.copy(TransferSinkError(503, "backend down"))
        assert c.status_code == 503
        assert str(c) == "backend down"

    def test_base_pickle_preserves_status_and_message(self) -> None:
        r = pickle.loads(pickle.dumps(TransferSinkError(503, "backend down")))  # noqa: S301, RUF100 - Round-trips our own exception; S301 is gate-only.
        assert r.status_code == 503
        assert str(r) == "backend down"

    @pytest.mark.parametrize(("cls", "status"), _SUGAR)
    def test_subclass_pickle_preserves_status_and_message(
        self, cls: Callable[..., TransferSinkError], status: int
    ) -> None:
        r = pickle.loads(pickle.dumps(cls("nope")))  # noqa: S301, RUF100 - Round-trips our own exception; S301 is gate-only.
        assert r.status_code == status
        assert str(r) == "nope"
