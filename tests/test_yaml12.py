"""Frontmatter is read by the YAML 1.2 core schema (OKF 0.3 §4, #2415).

The cases come from YAML 1.2.2 §10.3.2, its Example 10.9, and the YAML 1.1
spellings PyYAML used to resolve differently
(``docs/design/reference/yaml-1-2-core-schema.md``).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import frontmatter
import pytest
import yaml

from markdown_vault_mcp._yaml12 import YAML12_HANDLER, Core12Loader, load_yaml
from markdown_vault_mcp.okf import derive_annotation
from markdown_vault_mcp.scanner import parse_frontmatter


def _meta(block: str) -> dict[str, Any]:
    return dict(parse_frontmatter(f"---\n{block}\n---\nbody\n").metadata)


def test_example_10_9_core_tag_resolution() -> None:
    """YAML 1.2.2 Example 10.9, resolved as the spec's JSON shows."""
    loaded = load_yaml(
        "A null: null\n"
        "Also a null: # Empty\n"
        'Not a null: ""\n'
        "Booleans: [ true, True, false, FALSE ]\n"
        "Integers: [ 0, 0o7, 0x3A, -19 ]\n"
        "Floats: [ 0., -0.0, .5, +12e03, -2E+05 ]\n"
        "Also floats: [ .inf, -.Inf, +.INF, .NAN ]\n"
    )
    assert loaded["A null"] is None
    assert loaded["Also a null"] is None
    assert loaded["Not a null"] == ""
    assert loaded["Booleans"] == [True, True, False, False]
    assert loaded["Integers"] == [0, 7, 58, -19]
    assert loaded["Floats"] == [0.0, -0.0, 0.5, 12000.0, -200000.0]
    inf, neg_inf, pos_inf, nan = loaded["Also floats"]
    assert (inf, neg_inf, pos_inf) == (math.inf, -math.inf, math.inf)
    assert math.isnan(nan)


@pytest.mark.parametrize(
    ("plain", "expected"),
    [
        ("yes", "yes"),  # YAML 1.1 bool
        ("on", "on"),
        ("No", "No"),
        ("1_000", "1_000"),  # YAML 1.1 int with separators
        ("012", 12),  # YAML 1.1 octal; 1.2 base 10
        ("1:30", "1:30"),  # YAML 1.1 sexagesimal
        ("2026-06-30", "2026-06-30"),  # the core schema has no timestamp
        ("2026-06-30T14:00:00Z", "2026-06-30T14:00:00Z"),
        ("2016-12-31T23:59:60Z", "2016-12-31T23:59:60Z"),  # PyYAML 1.1 fails here
        ("2026-06-30 14:00:00", "2026-06-30 14:00:00"),
        ("0o17", 15),
        ("1e3", 1000.0),
    ],
)
def test_plain_scalars_resolve_by_the_core_schema(plain: str, expected: Any) -> None:
    assert _meta(f"a: {plain}")["a"] == expected


def test_an_explicit_tag_still_applies() -> None:
    assert _meta("a: !!timestamp 2026-06-30")["a"] == dt.date(2026, 6, 30)


def test_a_leap_second_note_is_readable_and_its_stale_after_parses() -> None:
    meta = _meta("type: Note\nstale_after: 2016-12-31T23:59:60Z")
    now = dt.datetime(2026, 11, 21, tzinfo=dt.UTC)
    annotation = derive_annotation(meta, now=now)
    assert annotation["freshness"]["state"] == "expired"


@pytest.mark.parametrize(
    "text",
    ["0o17", "1e3", "+12e03", "yes", "on", "1_000", "12", "true", "~", "1:30", "0x3A"],
)
def test_a_written_string_reads_back_as_the_same_string(text: str) -> None:
    """Quoting must hold for both YAML 1.1 and YAML 1.2 readers."""
    written = frontmatter.dumps(
        frontmatter.Post("body", a=text), handler=YAML12_HANDLER
    )
    reread = written.split("---")[1]
    assert load_yaml(reread)["a"] == text
    assert yaml.safe_load(reread)["a"] == text


def test_a_parsed_post_writes_back_with_the_same_handler() -> None:
    post = parse_frontmatter("---\na: '0o17'\nb: 2026-06-30\n---\nbody\n")
    assert post.handler is YAML12_HANDLER
    reread = parse_frontmatter(frontmatter.dumps(post))
    assert dict(reread.metadata) == {"a": "0o17", "b": "2026-06-30"}


def test_the_loader_constructs_no_python_objects() -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("a: !!python/object:os.system x", Loader=Core12Loader)  # noqa: S506, RUF100 - asserts the refusal; S506 is gate-only.


def test_a_yaml_1_2_directive_is_accepted() -> None:
    """§6.8.1: a 1.2 processor "must accept" ``%YAML 1.2`` and ``%YAML 1.1``."""
    assert load_yaml("%YAML 1.2\n---\na: yes\n") == {"a": "yes"}
    assert load_yaml("%YAML 1.1\n---\na: 1\n") == {"a": 1}


@pytest.mark.parametrize(
    ("block", "tag"),
    [
        ("a: !!bool yes", "bool"),
        ("a: !!bool on", "bool"),
        ("a: !!int 1_000", "int"),
        ("a: !!int 0b11", "int"),
        ("a: !!int +0x3A", "int"),
    ],
)
def test_an_explicit_tag_on_a_non_core_spelling_is_a_yaml_error(
    block: str, tag: str
) -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match=f"core-schema {tag}"):
        load_yaml(block)


def test_an_explicit_core_tag_on_a_core_spelling_still_constructs() -> None:
    assert load_yaml("a: !!bool FALSE\nb: !!int 0o17") == {"a": False, "b": 15}


def test_a_tagged_timestamp_round_trips_as_the_string_it_reads_back_as() -> None:
    post = parse_frontmatter("---\na: !!timestamp 2026-06-30\n---\nbody\n")
    assert post.metadata["a"] == dt.date(2026, 6, 30)
    reread = parse_frontmatter(frontmatter.dumps(post))
    assert reread.metadata["a"] == "2026-06-30"
    assert yaml.safe_load(frontmatter.dumps(post).split("---")[1])["a"] == "2026-06-30"
