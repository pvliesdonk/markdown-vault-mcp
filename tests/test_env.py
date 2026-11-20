"""Tests for the env var helpers, ported from fastmcp_pvl_core 10.2.0 (#2275)."""

from __future__ import annotations

import logging

import pytest

from markdown_vault_mcp._env import env, env_float, env_int, parse_bool, parse_list
from markdown_vault_mcp.exceptions import ConfigurationError


class TestEnv:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_FOO", raising=False)
        assert env("MYAPP", "FOO", default="bar") == "bar"

    def test_returns_value_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "hello")
        assert env("MYAPP", "FOO") == "hello"

    def test_empty_string_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "")
        assert env("MYAPP", "FOO", default="fallback") == "fallback"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "  value  ")
        assert env("MYAPP", "FOO") == "value"

    def test_prefix_can_have_trailing_underscore(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "x")
        assert env("MYAPP_", "FOO") == "x"
        assert env("MYAPP", "FOO") == "x"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


class TestEnvInt:
    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_N", raising=False)
        assert env_int("MYAPP", "N", 8000) == 8000

    def test_unset_without_default_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_N", raising=False)
        assert env_int("MYAPP", "N") is None

    def test_unset_does_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.delenv("MYAPP_N", raising=False)
        with caplog.at_level(logging.WARNING):
            env_int("MYAPP", "N", 5)
        assert _warnings(caplog) == []

    def test_blank_returns_default_silently(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_N", "   ")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 7) == 7
        assert _warnings(caplog) == []

    def test_valid_value(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "42")
        assert env_int("MYAPP", "N", 0) == 42

    def test_strips_surrounding_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "  42  ")
        assert env_int("MYAPP", "N") == 42

    def test_float_text_is_invalid(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_N", "42.5")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 3) == 3
        assert "MYAPP_N" in caplog.text

    def test_malformed_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_N", "abc")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 9) == 9
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert "MYAPP_N" in warnings[0].getMessage()
        assert "abc" in warnings[0].getMessage()

    def test_malformed_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "abc")
        with pytest.raises(ConfigurationError) as exc:
            env_int("MYAPP", "N", 9, strict=True)
        assert "MYAPP_N" in str(exc.value)
        assert "abc" in str(exc.value)

    def test_strict_parse_error_chains_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The ConfigurationError chains the underlying int() ValueError
        # (raise ... from cause), so the low-level reason stays attached.
        monkeypatch.setenv("MYAPP_N", "abc")
        with pytest.raises(ConfigurationError) as exc:
            env_int("MYAPP", "N", strict=True)
        assert isinstance(exc.value.__cause__, ValueError)

    def test_soft_reject_without_default_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        # Soft mode + no default: an invalid value yields None (still warns).
        # The int|None overload makes None a first-class "no usable value"
        # outcome — the numeric analog of env() -> None. A caller wanting an
        # invalid value to fail hard uses strict=True instead.
        monkeypatch.setenv("MYAPP_N", "abc")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N") is None
        assert _warnings(caplog)

    def test_below_minimum_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_N", "0")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 8000, minimum=1) == 8000
        assert _warnings(caplog)

    def test_below_minimum_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "0")
        with pytest.raises(ConfigurationError) as exc:
            env_int("MYAPP", "N", 8000, minimum=1, strict=True)
        assert ">= 1" in str(exc.value)

    def test_above_maximum_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "70000")
        with pytest.raises(ConfigurationError) as exc:
            env_int("MYAPP", "N", 8000, maximum=65535, strict=True)
        assert "<= 65535" in str(exc.value)

    def test_above_maximum_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_N", "70000")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 8000, maximum=65535) == 8000
        assert _warnings(caplog)

    def test_soft_default_is_not_bounds_checked(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        # Contract: the default is the trusted developer fallback, returned
        # as-is on the soft reject path. minimum/maximum validate the operator's
        # env value, NOT the default — so a default that itself violates the
        # bounds is still returned (with the warning for the rejected value).
        monkeypatch.setenv("MYAPP_N", "70000")
        with caplog.at_level(logging.WARNING):
            assert env_int("MYAPP", "N", 0, minimum=1, maximum=65535) == 0
        assert _warnings(caplog)

    def test_boundaries_are_inclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "1")
        assert env_int("MYAPP", "N", minimum=1, maximum=65535) == 1
        monkeypatch.setenv("MYAPP_N", "65535")
        assert env_int("MYAPP", "N", minimum=1, maximum=65535) == 65535

    def test_no_bounds_accepts_any_integer(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "-1000000")
        assert env_int("MYAPP", "N") == -1000000

    def test_accepts_pep515_underscores(self, monkeypatch: pytest.MonkeyPatch):
        # Documented accept-set: env_int delegates to int(), which accepts
        # PEP 515 underscore separators. Pinned so the contract is explicit.
        monkeypatch.setenv("MYAPP_N", "1_000")
        assert env_int("MYAPP", "N") == 1000

    def test_trailing_underscore_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "5")
        assert env_int("MYAPP_", "N") == env_int("MYAPP", "N") == 5

    def test_error_key_has_no_double_underscore(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_N", "abc")
        with pytest.raises(ConfigurationError) as exc:
            env_int("MYAPP_", "N", strict=True)
        assert "MYAPP_N" in str(exc.value)
        assert "MYAPP__N" not in str(exc.value)


class TestEnvFloat:
    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_X", raising=False)
        assert env_float("MYAPP", "X", 2.5) == 2.5

    def test_unset_without_default_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_X", raising=False)
        assert env_float("MYAPP", "X") is None

    def test_blank_returns_default_silently(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_X", "   ")
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X", 1.5) == 1.5
        assert _warnings(caplog) == []

    def test_decimal_value(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "3.14")
        assert env_float("MYAPP", "X") == 3.14

    def test_integer_text_becomes_float(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "5")
        result = env_float("MYAPP", "X")
        assert result == 5.0
        assert isinstance(result, float)

    def test_malformed_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_X", "abc")
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X", 1.0) == 1.0
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert "MYAPP_X" in warnings[0].getMessage()

    def test_malformed_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "abc")
        with pytest.raises(ConfigurationError) as exc:
            env_float("MYAPP", "X", strict=True)
        assert "MYAPP_X" in str(exc.value)

    def test_soft_reject_without_default_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        # See TestEnvInt: soft + no default + invalid -> None (warns), not a raise.
        monkeypatch.setenv("MYAPP_X", "abc")
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X") is None
        assert _warnings(caplog)

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
    def test_non_finite_soft_warns_and_defaults(
        self,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        monkeypatch.setenv("MYAPP_X", value)
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X", 1.0) == 1.0
        assert _warnings(caplog)

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity"])
    def test_non_finite_strict_raises(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MYAPP_X", value)
        with pytest.raises(ConfigurationError) as exc:
            env_float("MYAPP", "X", strict=True)
        assert "finite" in str(exc.value)

    def test_non_finite_rejected_before_bounds(self, monkeypatch: pytest.MonkeyPatch):
        # Ordering invariant: the finite check runs *before* _check_bounds.
        # nan compares False to every bound (nan < 0.0 and nan > 5.0 are both
        # False), so were the order reversed it would slip through the bounds
        # and be returned as a config value. Pinned so a reorder fails here:
        # with bounds set, a non-finite value is still rejected as "finite".
        monkeypatch.setenv("MYAPP_X", "nan")
        with pytest.raises(ConfigurationError) as exc:
            env_float("MYAPP", "X", 1.0, minimum=0.0, maximum=5.0, strict=True)
        assert "finite" in str(exc.value)

    def test_below_minimum_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_X", "-0.5")
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X", 1.0, minimum=0.0) == 1.0
        assert _warnings(caplog)

    def test_below_minimum_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "-0.5")
        with pytest.raises(ConfigurationError) as exc:
            env_float("MYAPP", "X", 1.0, minimum=0.0, strict=True)
        assert ">= 0" in str(exc.value)

    def test_above_maximum_soft_warns_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MYAPP_X", "9.9")
        with caplog.at_level(logging.WARNING):
            assert env_float("MYAPP", "X", 1.0, maximum=5.0) == 1.0
        assert _warnings(caplog)

    def test_above_maximum_strict_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "9.9")
        with pytest.raises(ConfigurationError) as exc:
            env_float("MYAPP", "X", 1.0, maximum=5.0, strict=True)
        assert "<= 5" in str(exc.value)

    def test_boundaries_are_inclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_X", "0.0")
        assert env_float("MYAPP", "X", minimum=0.0, maximum=1.0) == 0.0
        monkeypatch.setenv("MYAPP_X", "1.0")
        assert env_float("MYAPP", "X", minimum=0.0, maximum=1.0) == 1.0

    def test_accepts_pep515_underscores(self, monkeypatch: pytest.MonkeyPatch):
        # Documented accept-set: env_float delegates to float(), which accepts
        # PEP 515 underscore separators. Pinned so the contract is explicit.
        monkeypatch.setenv("MYAPP_X", "1_000.5")
        assert env_float("MYAPP", "X") == 1000.5

    def test_accepts_scientific_notation(self, monkeypatch: pytest.MonkeyPatch):
        # Documented accept-set: float() accepts scientific notation, unlike
        # int() (env_int rejects "1e3"). Pinned to lock the int/float asymmetry.
        monkeypatch.setenv("MYAPP_X", "1e3")
        assert env_float("MYAPP", "X") == 1000.0


class TestParseBool:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
    def test_truthy(self, value: str):
        assert parse_bool(value) is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", ""])
    def test_falsy(self, value: str):
        assert parse_bool(value) is False


class TestParseList:
    def test_empty(self):
        assert parse_list("") == []

    def test_comma_separated(self):
        assert parse_list("a,b,c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert parse_list(" a , b , c ") == ["a", "b", "c"]

    def test_drops_empty_items(self):
        assert parse_list("a,,b,") == ["a", "b"]


class TestRequired:
    @pytest.mark.parametrize("reader", [env, env_int, env_float])
    def test_unset_raises_naming_the_key(
        self, monkeypatch: pytest.MonkeyPatch, reader
    ) -> None:
        monkeypatch.delenv("MYAPP_FOO", raising=False)
        with pytest.raises(
            ConfigurationError, match=r"^MYAPP_FOO is required but not set$"
        ):
            reader("MYAPP", "FOO", required=True)

    @pytest.mark.parametrize("raw", ["", "   "])
    @pytest.mark.parametrize("reader", [env, env_int, env_float])
    def test_blank_counts_as_unset(
        self, monkeypatch: pytest.MonkeyPatch, reader, raw: str
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", raw)
        with pytest.raises(
            ConfigurationError, match="MYAPP_FOO is required but not set"
        ):
            reader("MYAPP", "FOO", required=True)

    def test_env_returns_the_value_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", "  hello ")
        assert env("MYAPP", "FOO", required=True) == "hello"

    def test_env_int_returns_the_value_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", "42")
        assert env_int("MYAPP", "FOO", required=True) == 42

    def test_env_float_returns_the_value_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", "2.5")
        assert env_float("MYAPP", "FOO", required=True) == 2.5

    @pytest.mark.parametrize("reader", [env_int, env_float])
    def test_required_implies_strict_for_a_malformed_value(
        self, monkeypatch: pytest.MonkeyPatch, reader
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", "not-a-number")
        with pytest.raises(ConfigurationError, match="MYAPP_FOO must be"):
            reader("MYAPP", "FOO", required=True)

    def test_required_implies_strict_for_an_out_of_range_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_FOO", "0")
        with pytest.raises(ConfigurationError, match="MYAPP_FOO must be >= 1"):
            env_int("MYAPP", "FOO", required=True, minimum=1)

    @pytest.mark.parametrize(
        ("reader", "default"), [(env, "x"), (env_int, 1), (env_float, 1.0)]
    )
    def test_required_with_a_default_is_a_type_error(self, reader, default) -> None:
        with pytest.raises(
            TypeError, match="required=True cannot be combined with a default"
        ):
            reader("MYAPP", "FOO", default, required=True)

    def test_explicit_required_false_matches_omitting_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MYAPP_FOO", raising=False)
        assert env("MYAPP", "FOO", required=False) is None
        assert env("MYAPP", "FOO", "d", required=False) == "d"
        assert env_int("MYAPP", "FOO", 3, required=False) == 3


def test_reading_the_environment_does_not_load_the_server_stack() -> None:
    """The CLI client's env read imports neither FastMCP nor pvl-core (#2016)."""
    import subprocess
    import sys

    probe = (
        "import sys, markdown_vault_mcp._cli_client\n"
        "print(sorted({'fastmcp', 'fastmcp_pvl_core'} & set(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", result.stdout
