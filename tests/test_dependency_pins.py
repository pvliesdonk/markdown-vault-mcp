"""``[tool.uv]`` pins carry their exit condition (template-owned).

A pin under ``[tool.uv] override-dependencies`` / ``constraint-dependencies``
is a workaround that must go away once its reason does.  ``scripts/check_pins.py``
enforces ``# until: <issue URL>`` on every entry and, in CI, fails once that
issue is closed; this test runs its offline half against the committed
``pyproject.toml`` so an untracked pin never lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_pins import findings, parse_pins  # noqa: E402

PYPROJECT = REPO_ROOT / "pyproject.toml"


def test_every_tool_uv_pin_has_an_exit_condition() -> None:
    problems = findings(PYPROJECT.read_text(encoding="utf-8"), state_of=None)
    assert not problems, "\n".join(problems)


def test_untracked_pin_is_named() -> None:
    text = (
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["numpy>=1.20"]\n\n'
        '[tool.uv]\nconstraint-dependencies = ["numpy<2.5.0"]\n'
    )
    problems = findings(text, state_of=None)
    assert any("no exit condition" in p and "numpy<2.5.0" in p for p in problems)
    assert any("also carries a version bound under" in p for p in problems)


def test_tracked_transitive_pin_is_clean() -> None:
    text = (
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["numpy>=1.20"]\n\n'
        "[tool.uv]\noverride-dependencies = [\n"
        '    "pygments<2.21",  # until: https://github.com/pygments/pygments/issues/1\n'
        "]\n"
    )
    assert findings(text, state_of=None) == []


_ONLINE = (
    '[project]\nname = "x"\nversion = "0"\n\n[tool.uv]\n'
    'override-dependencies = ["pygments<2.21"]  # until: https://github.com/o/r/issues/7\n'
)


def test_resolved_issue_fails_online() -> None:
    problems = findings(_ONLINE, state_of=lambda _url: "fixed")
    assert problems == [
        (
            "pyproject.toml:6 [tool.uv] override-dependencies 'pygments<2.21': outlived its "
            "reason — https://github.com/o/r/issues/7 is resolved; lift the pin"
        )
    ]
    assert findings(_ONLINE, state_of=lambda _url: "open") == []


def test_abandoned_issue_asks_for_a_live_one() -> None:
    (problem,) = findings(_ONLINE, state_of=lambda _url: "abandoned")
    assert "closed without a fix" in problem


def test_lookup_failure_warns_but_does_not_fail() -> None:
    warnings: list[str] = []

    def boom(_url: str) -> str:
        raise OSError("403 rate limit")

    assert findings(_ONLINE, state_of=boom, warn=warnings.append) == []
    assert warnings and "assuming still open" in warnings[0]


def test_parser_edge_cases() -> None:
    text = (
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["numpy", "foo>=1"]\n\n'
        "[tool.uv]\n"
        "constraint-dependencies = [\n"
        '    "a<1",  # until: https://github.com/o/r/issues/1 [see] "foo"\n'
        "    # until: https://github.com/o/r/issues/2\n"
        "    'b<1',\n"
        '    "numpy<2.5",  # until: https://github.com/o/r/issues/3\n'
        "]\n"
    )
    problems = findings(text, state_of=None)
    # `]` and a quoted word inside a comment do not end the array or invent a pin;
    # a single-quoted entry is parsed; a comment line above counts for the next entry;
    # a bare direct dependency ("numpy") is not a competing bound.
    assert problems == [], "\n".join(problems)


def test_until_on_the_opening_line_covers_the_array() -> None:
    text = (
        '[project]\nname = "x"\nversion = "0"\n\n[tool.uv]\n'
        "override-dependencies = [  # until: https://github.com/o/r/issues/4\n"
        '    "a<1",\n'
        '    "b<2",  # until: https://github.com/o/r/issues/5\n'
        "]\n"
    )
    assert findings(text, state_of=None) == []
    seen = dict.fromkeys(("issues/4", "issues/5"), 0)
    for pin in parse_pins(text):
        for key in seen:
            if pin.until and pin.until.endswith(key):
                seen[key] += 1
    assert seen == {"issues/4": 1, "issues/5": 1}


def test_until_on_the_closing_line_is_diagnosed() -> None:
    text = (
        '[project]\nname = "x"\nversion = "0"\n\n[tool.uv]\n'
        'override-dependencies = [\n    "a<1",\n]  # until: https://github.com/o/r/issues/6\n'
    )
    problems = findings(text, state_of=None)
    assert any("closing `]` line covers nothing" in p for p in problems), problems
    assert any("'a<1': no exit condition" in p for p in problems), problems
