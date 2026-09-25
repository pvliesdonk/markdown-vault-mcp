"""get_history / get_diff report a caller's mistake apart from a server fault (#1608)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from markdown_vault_mcp._tools.git import _refusal_or_fault
from markdown_vault_mcp.exceptions import InvalidRequestError

if TYPE_CHECKING:
    import pytest


def test_invalid_request_reaches_the_model_at_info() -> None:
    err = _refusal_or_fault("get_diff", InvalidRequestError("'dead' is not a commit"))
    assert err.log_level == logging.INFO
    assert "not a commit" in str(err)


def test_fault_is_logged_and_hidden_from_the_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        raise ValueError("fatal: not a git repository: /srv/secret/vault")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR, logger="markdown_vault_mcp._tools.git"):
            err = _refusal_or_fault("get_diff", exc)
    assert "/srv/secret" not in str(err)
    assert "request was fine" in str(err)
    assert any(r.msg.startswith("tool_failed") and r.exc_info for r in caplog.records)
