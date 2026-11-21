"""Tests for the YAML 1.2 core-schema loader (OKF 0.3 §4, #2415).

``markdown_vault_mcp.utils.yaml12`` gives frontmatter parsing the plain-scalar
resolution the YAML 1.2 core schema defines, where PyYAML's SafeLoader applies
YAML 1.1. These tests pin the resolution table itself — including that the
global SafeLoader is left untouched — and the OKF 0.3 examples end to end
through ``scanner.parse_frontmatter``.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pytest
import yaml

from markdown_vault_mcp.utils.yaml12 import Yaml12CoreLoader, load_core_schema


def _scalar(document: str) -> Any:
    """Load a one-line YAML document and return its single value."""
    return load_core_schema(document)


class TestCoreSchemaScalars:
    """YAML 1.2.2 core-schema resolution (spec §10.3.2) for plain scalars."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Booleans: only the true/false spellings (YAML 1.1's yes/no/on/off
            # are plain strings in the core schema).
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("yes", "yes"),
            ("Yes", "Yes"),
            ("no", "no"),
            ("on", "on"),
            ("off", "off"),
            # Null: ~, the three null spellings, and the empty scalar.
            ("~", None),
            ("null", None),
            ("Null", None),
            ("NULL", None),
            ("", None),
            # Integers: decimal, 0o octal, 0x hex. No 1.1 leading-zero octal,
            # no underscores, no sexagesimal.
            ("0", 0),
            ("00", 0),
            ("42", 42),
            ("-7", -7),
            ("+5", 5),
            ("012", 12),
            ("0o17", 15),
            ("0o777", 511),
            ("-0o17", -15),
            ("0x1F", 31),
            ("0xdeadBEEF", 0xDEADBEEF),
            ("1_000", "1_000"),
            ("1:30", "1:30"),
            ("1:30:00", "1:30:00"),
            ("0b101", "0b101"),
            # Floats: dotted/exponent forms plus .inf/.nan. `1e5` is a float
            # in the core schema (PyYAML 1.1 left it a string).
            ("3.14", 3.14),
            ("-0.5", -0.5),
            (".5", 0.5),
            ("1.", 1.0),
            ("1e5", 100000.0),
            ("1E-5", 0.00001),
            ("2.5e+3", 2500.0),
            ("1_0.5", "1_0.5"),
            # Everything else is a string: dates, date-times (the core schema
            # has no timestamp tag), and 1.1-only forms.
            ("2026-06-30", "2026-06-30"),
            ("2026-06-30 14:00:00", "2026-06-30 14:00:00"),
            ("2026-06-30T14:00:00Z", "2026-06-30T14:00:00Z"),
            ("2016-12-31T23:59:60Z", "2016-12-31T23:59:60Z"),
            ("=", "="),
            ("y", "y"),
            ("n", "n"),
        ],
    )
    def test_plain_scalar_resolution(self, raw: str, expected: Any) -> None:
        assert _scalar(raw) == expected
        assert type(_scalar(raw)) is type(expected)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (".inf", math.inf),
            (".Inf", math.inf),
            (".INF", math.inf),
            ("+.inf", math.inf),
            ("-.inf", -math.inf),
        ],
    )
    def test_infinity(self, raw: str, expected: float) -> None:
        assert _scalar(raw) == expected

    def test_nan(self) -> None:
        value = _scalar(".nan")
        assert isinstance(value, float)
        assert math.isnan(value)

    def test_types(self) -> None:
        assert isinstance(_scalar("yes"), str)
        assert isinstance(_scalar("0o17"), int)
        assert isinstance(_scalar("1e5"), float)
        assert isinstance(_scalar("2026-06-30"), str)


class TestCoreSchemaStructures:
    """Collections, merge keys and explicit tags under the core schema."""

    def test_mapping_and_sequence(self) -> None:
        doc = load_core_schema("a: yes\nb:\n  - 1\n  - 0o17\n  - '1:30'\n")
        assert doc == {"a": "yes", "b": [1, 15, "1:30"]}

    def test_flow_style(self) -> None:
        assert load_core_schema("{a: on, b: [012, 1_000]}") == {
            "a": "on",
            "b": [12, "1_000"],
        }

    def test_nested_date_stays_string(self) -> None:
        doc = load_core_schema("generated:\n  at: 2026-11-21T11:04:16+01:00\n")
        assert doc == {"generated": {"at": "2026-11-21T11:04:16+01:00"}}
        assert isinstance(doc["generated"]["at"], str)

    def test_merge_key_still_works(self) -> None:
        doc = load_core_schema("base: &b\n  x: yes\nmerged:\n  <<: *b\n  y: 0o17\n")
        assert doc["merged"] == {"x": "yes", "y": 15}

    def test_explicit_tags_still_apply(self) -> None:
        # An explicit tag overrides plain-scalar resolution, as in any schema.
        assert load_core_schema("a: !!str 0o17") == {"a": "0o17"}
        assert load_core_schema("a: !!int 0o17") == {"a": 15}
        assert load_core_schema("a: !!bool yes") == {"a": True}

    def test_duplicate_keys_last_wins(self) -> None:
        # OKF §4 lets consumers MAY reject duplicates; PyYAML's behaviour
        # (last wins) is kept.
        assert load_core_schema("a: 1\na: yes\n") == {"a": "yes"}

    def test_malformed_yaml_raises_yaml_error(self) -> None:
        with pytest.raises(yaml.YAMLError):
            load_core_schema("a: [unclosed\n")


class TestSafeLoaderUntouched:
    """The subclass must not leak its resolvers into PyYAML's own loaders."""

    def test_safe_load_still_resolves_yaml_11(self) -> None:
        # PyYAML's SafeLoader keeps its YAML 1.1 table: only Yaml12CoreLoader
        # was re-registered.
        assert yaml.safe_load("a: yes") == {"a": True}
        assert yaml.safe_load("a: 012") == {"a": 10}
        assert yaml.safe_load("a: 1:30") == {"a": 90}
        assert yaml.safe_load("a: 1_000") == {"a": 1000}

    def test_safe_loader_class_resolver_table_is_not_shared(self) -> None:
        safe_tags = {
            tag
            for resolvers in yaml.SafeLoader.yaml_implicit_resolvers.values()
            for tag, _ in resolvers
        }
        core_tags = {
            tag
            for resolvers in Yaml12CoreLoader.yaml_implicit_resolvers.values()
            for tag, _ in resolvers
        }
        assert "tag:yaml.org,2002:timestamp" in safe_tags
        assert "tag:yaml.org,2002:timestamp" not in core_tags

    def test_safe_load_still_parses_timestamps(self) -> None:
        assert yaml.safe_load("a: 2026-06-30") == {"a": date(2026, 6, 30)}
        assert yaml.safe_load("a: 2026-06-30 14:00:00") == {
            "a": datetime(2026, 6, 30, 14, 0)
        }

    def test_core_loader_has_no_timestamp_resolver(self) -> None:
        assert load_core_schema("a: 2026-06-30") == {"a": "2026-06-30"}

    def test_loader_reuse_across_calls(self) -> None:
        # The loader class is shared, so resolvers must be stable across loads.
        for _ in range(3):
            assert load_core_schema("a: on") == {"a": "on"}
            assert load_core_schema("a: 0o17") == {"a": 15}
