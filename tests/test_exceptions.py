"""Tests for markdown_vault_mcp.exceptions — constructor contracts."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.exceptions import (
    IndexUnavailableError,
    MarkdownMCPError,
)


class TestIndexUnavailableError:
    def test_constructs_with_never_built_reason(self) -> None:
        err = IndexUnavailableError("not built", reason="never_built")
        assert err.reason == "never_built"
        assert str(err) == "not built"
        assert isinstance(err, MarkdownMCPError)

    def test_constructs_with_timeout_reason(self) -> None:
        err = IndexUnavailableError("timed out", reason="timeout")
        assert err.reason == "timeout"
        assert str(err) == "timed out"

    def test_reason_is_keyword_only(self) -> None:
        """Positional second arg must fail — reason must be passed by keyword."""
        with pytest.raises(TypeError):
            IndexUnavailableError("msg", "timeout")  # type: ignore[misc]

    def test_reason_is_required(self) -> None:
        """No default — must pass reason explicitly."""
        with pytest.raises(TypeError):
            IndexUnavailableError("msg")  # type: ignore[call-arg]

    def test_message_is_required(self) -> None:
        """Message remains the first positional argument."""
        with pytest.raises(TypeError):
            IndexUnavailableError(reason="never_built")  # type: ignore[call-arg]

    def test_reason_attribute_survives_raise_catch(self) -> None:
        """The attribute is preserved through the raise/catch cycle."""
        try:
            raise IndexUnavailableError("oops", reason="timeout")
        except IndexUnavailableError as e:
            assert e.reason == "timeout"


def test_index_unavailable_reason_literal_values() -> None:
    """The Literal type carries the five expected discriminator values.

    Validates by attempting to construct with each. Direct introspection of
    typing.Literal at runtime is implementation-dependent; the constructor
    round-trip is the contract that matters.
    """
    for value in ("never_built", "build_failed", "timeout", "broken", "busy"):
        err = IndexUnavailableError("x", reason=value)  # type: ignore[arg-type]
        assert err.reason == value


class TestConfigurationErrorCanonical:
    """ConfigurationError is the project's one config error (#638, #2283)."""

    def test_env_helpers_raise_this_class(self, monkeypatch) -> None:
        """env_int's strict raise is the same class callers catch."""
        import pytest

        from markdown_vault_mcp._env import env_int
        from markdown_vault_mcp.exceptions import ConfigurationError

        monkeypatch.setenv("MVM_EXC_TEST_N", "nope")
        with pytest.raises(ConfigurationError):
            env_int("MVM_EXC_TEST", "N", 1, strict=True)

    def test_not_a_markdown_mcp_error_subclass(self) -> None:
        """Deliberately outside the MarkdownMCPError tree (env_int raises the bare CE)."""
        from markdown_vault_mcp.exceptions import ConfigurationError

        assert not issubclass(ConfigurationError, MarkdownMCPError)
        assert issubclass(ConfigurationError, Exception)
