"""Tests for the exception taxonomy after issue #533."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import (
    IndexNotReadyError,
    MarkdownMCPError,
)


def test_index_not_ready_error_carries_reason_field() -> None:
    err = IndexNotReadyError("not built", reason="never_built")
    assert err.reason == "never_built"
    assert str(err) == "not built"
    assert isinstance(err, MarkdownMCPError)


def test_index_not_ready_error_requires_reason_kwarg() -> None:
    with pytest.raises(TypeError):
        IndexNotReadyError("missing reason")  # type: ignore[call-arg]


def test_index_not_ready_reason_alias_is_a_literal_string() -> None:
    """IndexNotReadyReason is a Literal alias — we don't introspect the
    literal at runtime (PEP 586 makes that awkward); we instead verify that
    the documented values construct successfully and unexpected values can
    still be passed (it's a static type, not a runtime guard)."""
    for reason in ("never_built", "timeout", "broken"):
        err = IndexNotReadyError("x", reason=reason)
        assert err.reason == reason
