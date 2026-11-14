"""Decisions contracts: exact wire, refusals, bounded input and safe failures."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from markdown_vault_mcp._okf_decisions import DecisionError, DecisionsClient
from markdown_vault_mcp.managers.okf_decide import (
    DecisionPolicy,
    build_questions,
    load_vocabulary,
)
from tests.fixtures.okf_decisions import (
    ANSWERS,
    PARTS,
    QUESTIONS,
    accept_answers,
    vocabulary_text,
)


def test_decisions_exact_wire_contract() -> None:
    """A different endpoint, question schema or message shape would break this."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url) == "https://fixture.invalid/v1/decisions"
        assert request.headers["authorization"] == "Bearer synthetic-only"
        assert request.extensions["timeout"]["read"] == 30.0
        assert json.loads(request.content) == {
            "model": "gpt-6-luna",
            "input": [{"role": "user", "content": PARTS}],
            "questions": QUESTIONS,
        }
        return httpx.Response(
            200, json={"answers": ANSWERS, "model": "gpt-6-luna", "usage": {}}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1/",
        "synthetic-only",
        transport=httpx.MockTransport(respond),
    )
    try:
        assert client.decide(PARTS, QUESTIONS) == ANSWERS
        assert len(seen) == 1
    finally:
        client.close()


def _invalid_answers(mutation: str) -> list[dict[str, Any]]:
    answers = copy.deepcopy(ANSWERS)
    first, second = answers
    if mutation == "missing":
        answers.pop()
    elif mutation == "extra":
        answers.append(first)
    elif mutation == "shifted":
        second["name"] = "status"
    elif mutation == "null_answer":
        answers[0] = None  # type: ignore[assignment]
    elif mutation == "wrong_type":
        second["type"] = "predicate"
    elif mutation == "choice_bool":
        second["choice"] = True
    elif mutation == "forged_choice":
        second["choice"] = "Invented"
    elif mutation in {"bool", "nan", "inf", "negative", "over_one"}:
        first["probability"] = {
            "bool": True,
            "nan": float("nan"),
            "inf": float("inf"),
            "negative": -0.1,
            "over_one": 1.1,
        }[mutation]
    elif mutation.startswith("huge_"):
        field = mutation.removeprefix("huge_")
        target, key = {
            "probability": (first, "probability"),
            "confidence": (second, "confidence"),
            "distribution": (second["probabilities"][0], "probability"),
        }[field]
        target[key] = 10**1000
    else:
        _mutate_distribution_or_name(first, second, mutation)
    return answers


def _mutate_distribution_or_name(
    first: dict[str, Any], second: dict[str, Any], mutation: str
) -> None:
    if mutation in {"distribution_null", "distribution_value_bool"}:
        second["probabilities"][0] = (
            None
            if mutation == "distribution_null"
            else {"value": True, "probability": 0.5}
        )
    elif mutation == "distribution_dict":
        second["probabilities"] = {"Reference": 0.8}
    elif mutation == "distribution_bool":
        second["probabilities"][0]["probability"] = True
    elif mutation == "distribution_forged":
        second["probabilities"][0]["value"] = "Forged"
    elif mutation == "distribution_duplicate":
        second["probabilities"][1]["value"] = "Reference"
    elif mutation == "distribution_missing":
        second["probabilities"].pop()
    elif mutation == "null_name":
        first["name"] = None
    else:
        first["type"] = "unknown"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "shifted",
        "null_answer",
        "wrong_type",
        "choice_bool",
        "forged_choice",
        "bool",
        "nan",
        "inf",
        "negative",
        "over_one",
        "distribution_null",
        "distribution_value_bool",
        "huge_probability",
        "huge_confidence",
        "huge_distribution",
        "distribution_dict",
        "distribution_bool",
        "distribution_forged",
        "distribution_duplicate",
        "distribution_missing",
        "null_name",
        "unknown",
    ],
)
def test_invalid_reply_never_returns_answers(mutation: str) -> None:
    """A malformed field must invalidate the entire response before any write."""
    answers = _invalid_answers(mutation)
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "synthetic-only",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps({"answers": answers}))
        ),
    )
    try:
        with pytest.raises(DecisionError, match="response") as error:
            client.decide(PARTS, QUESTIONS)
        assert not error.value.terminal
    finally:
        client.close()


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("named", [False, True])
def test_null_name_refusal_uses_question_order(position: int, named: bool) -> None:
    """An unnamed refusal must retain accepted neighboring answers."""
    answers = copy.deepcopy(ANSWERS)
    answers[position] = {"type": "refusal", **({"name": None} if named else {})}
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "synthetic-only",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps({"answers": answers}))
        ),
    )
    try:
        assert client.decide(PARTS, QUESTIONS) == answers
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,terminal",
    [
        (401, True),
        (403, True),
        (429, True),
        (500, False),
        (302, False),
        ("network", False),
        ("json", False),
    ],
)
def test_decisions_error_is_safe(status: int | str, terminal: bool) -> None:
    """Status failures never reveal a key/body or retry/follow redirects."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status == "network":
            raise httpx.ConnectError("synthetic-only private-body", request=request)
        if status == "json":
            return httpx.Response(200, text="private-body synthetic-only")
        assert isinstance(status, int)
        return httpx.Response(
            status,
            text="private-body synthetic-only",
            headers={"location": "https://elsewhere.invalid"},
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "synthetic-only",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DecisionError) as error:
            client.decide(PARTS, QUESTIONS)
        assert error.value.terminal is terminal
        assert "synthetic-only" not in str(error.value)
        assert "private-body" not in str(error.value)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "questions",
        "choices",
        "depth",
        "values",
        "bytes",
        "null-name",
        "duplicate-name",
        "instructions",
        "type",
        "set",
        "nan",
    ],
)
def test_input_limits_precede_http(kind: str) -> None:
    """Each invalid request independently refuses before any HTTP request."""
    calls: list[httpx.Request] = []
    parts, questions = copy.deepcopy(PARTS), copy.deepcopy(QUESTIONS)
    if kind == "empty":
        questions = []
    elif kind == "questions":
        questions = [{**QUESTIONS[0], "name": f"question-{i}"} for i in range(401)]
    elif kind == "choices":
        questions[1]["choices"] = questions[1]["choices"][:1]
    elif kind in {"null-name", "duplicate-name", "instructions", "type"}:
        field, value = {
            "null-name": ("name", None),
            "duplicate-name": ("name", "type"),
            "instructions": ("instructions", ""),
            "type": ("type", "unknown"),
        }[kind]
        questions[0][field] = value
    else:
        parts[1]["data"] = _invalid_input_data(kind)
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "synthetic-only",
        transport=httpx.MockTransport(
            lambda request: (
                calls.append(request) or httpx.Response(200, json={"answers": ANSWERS})
            )
        ),
    )
    try:
        with pytest.raises(
            ValueError, match="valid JSON" if kind in {"set", "nan"} else None
        ):
            client.decide(parts, questions)
        assert calls == []
    finally:
        client.close()


def _invalid_input_data(kind: str) -> Any:
    if kind == "depth":
        data: Any = {}
        for _ in range(33):
            data = {"nested": data}
        return data
    return (
        {"values": [0] * 10001}
        if kind == "values"
        else {"text": "é" * 131072}
        if kind == "bytes"
        else {"not-json": {1, 2}}
        if kind == "set"
        else {"nonfinite": float("nan")}
    )


# Policies are exercised with the real transport above and pure closed candidates below.
NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
THRESHOLDS = dict.fromkeys(
    ("type", "status", "description", "sources", "stale_after"), 0.8
)


def test_load_vocabulary_closed_lists(tmp_path: Path) -> None:
    """Operator data supplies every option/description, including bounded durations."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    vocab = load_vocabulary(tmp_path, "types.md")
    assert vocab.types[0] == {"value": "Reference", "description": "Living facts."}
    assert [item["value"] for item in vocab.statuses] == [
        "draft",
        "stable",
        "deprecated",
    ]
    assert [item["value"] for item in vocab.durations] == ["30d", "90d"]


@pytest.mark.parametrize(
    "old,new",
    [
        ("value: stable", "value: other"),
        ("value: Runbook", "value: Reference"),
        ("description: Living facts.", "description: ''"),
        (vocabulary_text(), "---\ntypes: [\n---\n"),
        ("30d", "-1d"),
        ("30d", "1.5d"),
        ("30d", "999999999999999999999999d"),
        ("  - {value: Runbook, description: Ordered steps.}\n", ""),
    ],
)
def test_invalid_vocabulary_fails_locally(tmp_path: Path, old: str, new: str) -> None:
    """Unknown statuses, duplicates, empty descriptions and bad durations cannot submit."""
    (tmp_path / "types.md").write_text(vocabulary_text().replace(old, new))
    with pytest.raises(ValueError):
        load_vocabulary(tmp_path, "types.md")


@pytest.mark.parametrize(
    "path",
    [
        "../types.md",
        "/types.md",
        "index.md",
        "log.md",
        "SKILL.md",
        "types.txt",
        "escape.md",
        "alias-log",
        "alias-skill",
        "reverse-log",
        "reverse-skill",
    ],
)
def test_vocabulary_containment(tmp_path: Path, path: str) -> None:
    """Reserved/skill/non-note and escaping vocabulary paths are refused."""
    outside = tmp_path.parent / "outside-vocabulary.md"
    outside.write_text(vocabulary_text())
    (tmp_path / "escape.md").symlink_to(outside)
    if path.startswith(("alias-", "reverse-")):
        reserved = "log.md" if path.endswith("log") else "SKILL.md"
        actual, alias = (
            ("types.md", reserved)
            if path.startswith("reverse-")
            else (reserved, "types.md")
        )
        (tmp_path / actual).write_text(vocabulary_text())
        (tmp_path / alias).symlink_to(tmp_path / actual)
        path = alias
    with pytest.raises(ValueError):
        load_vocabulary(tmp_path, path)


def test_all_field_questions_use_operator_and_body_values(tmp_path: Path) -> None:
    """No generated sentence/resource/date or YAML body may enter candidates."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    (tmp_path / "fact.md").write_text("A fact.")
    body = "# Title\n\nFirst exact paragraph.\nSecond line.\n\n[fact](fact.md) [web](https://example.invalid/a)\n[ref][r]\n\n[r]: https://example.invalid/ref\nBare https://example.invalid/b.\n```\nhttps://example.invalid/hidden\n```\n[bad](../escape.md) [js](javascript:bad) [missing](missing.md)\n"
    decision = build_questions(
        "note.md",
        {"type": "Reference", "when": NOW.date()},
        body,
        load_vocabulary(tmp_path, "types.md"),
    )
    questions = {q["name"]: q for q in decision.questions}
    assert {
        "meaningful",
        "type",
        "type_wrong",
        "status",
        "description",
        "stale_after",
    } <= questions.keys()
    assert questions["type"]["choices"][0]["description"] == "Living facts."
    assert decision.input_parts[1]["data"]["when"] == "2026-11-14"
    assert decision.input_parts[2] == {"type": "input_text", "text": body}
    patch = decision.evaluate(
        accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
    )[0]
    assert patch["sources"] == [
        {"resource": "fact.md"},
        {"resource": "https://example.invalid/a"},
        {"resource": "https://example.invalid/ref"},
        {"resource": "https://example.invalid/b"},
    ]
    assert patch["stale_after"] == "2026-12-14T10:00:00Z"
    assert "generated" not in patch and "last_edited" not in patch
    without_durations = tmp_path / "no-durations.md"
    without_durations.write_text(vocabulary_text(durations=False))
    assert (
        "stale_after"
        not in build_questions(
            "note.md", {}, body, load_vocabulary(tmp_path, "no-durations.md")
        ).bindings
    )


@pytest.mark.parametrize(
    "current,metrics,expected",
    [
        ("Runbook", (0.8, 0.951, 0.8), {"type": "Reference"}),
        ("Runbook", (0.799, 0.951, 0.8), {}),
        ("Runbook", (0.8, 0.95, 0.8), {}),
        ("Runbook", (0.8, 0.951, 0.799), {}),
        (None, (0.8, 0.951, 0.8), {"type": "Reference"}),
        (None, (0.799, 0.951, 0.8), {}),
    ],
)
def test_thresholds_and_noop_policy(
    tmp_path: Path,
    current: str | None,
    metrics: tuple[float, float, float],
    expected: dict[str, Any],
) -> None:
    """Acceptance is inclusive, replacement strict, and rejection produces no patch."""
    confidence, wrong, meaningful = metrics
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md",
        {"type": current} if current else {},
        "",
        load_vocabulary(tmp_path, "types.md"),
    )
    answers = accept_answers(
        decision.questions,
        type={"confidence": confidence},
        type_wrong={"probability": wrong},
        meaningful={"probability": meaningful},
        status="stable",
    )
    assert (
        decision.evaluate(answers, DecisionPolicy(THRESHOLDS), now=NOW)[0] == expected
    )


@pytest.mark.parametrize("malformed", ["malformed", [{"resource": " "}]])
def test_evidence_values_cannot_be_invented(tmp_path: Path, malformed: Any) -> None:
    """Existing raw values and partial refusals survive; equal classifications are noops."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    metadata = {
        "type": "Reference",
        "status": "draft",
        "description": "Keep.",
        "stale_after": "keep-date",
        "sources": malformed,
    }
    decision = build_questions(
        "note.md",
        metadata,
        "Candidate.\n\nhttps://example.invalid/a",
        load_vocabulary(tmp_path, "types.md"),
    )
    assert (
        decision.evaluate(
            accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
        )[0]
        == {}
    )
    metadata = {"sources": [{"resource": "https://example.invalid/a", "id": "keep"}]}
    decision = build_questions(
        "note.md",
        metadata,
        "Candidate.\n\nhttps://example.invalid/a https://example.invalid/b",
        load_vocabulary(tmp_path, "types.md"),
    )
    answers = accept_answers(decision.questions, type={"type": "refusal", "name": None})
    patch = decision.evaluate(answers, DecisionPolicy(THRESHOLDS), now=NOW)[0]
    assert "type" not in patch
    assert patch["sources"] == [
        {"resource": "https://example.invalid/a", "id": "keep"},
        {"resource": "https://example.invalid/b"},
    ]
    answers[0] = {"type": "refusal", "name": None}
    assert decision.evaluate(answers, DecisionPolicy(THRESHOLDS), now=NOW)[0] == {}


@pytest.mark.parametrize(
    "change", [{"type": float("nan")}, {"type": True}, {"extra": 0.8}, {"status": 0.96}]
)
def test_policy_rejects_invalid_thresholds(change: dict[str, Any]) -> None:
    """Malformed policy cannot be treated as uncertainty or weaken replacement."""
    with pytest.raises(ValueError):
        DecisionPolicy({**THRESHOLDS, **change})


def test_duration_overflow_refuses_only_expiry(tmp_path: Path) -> None:
    """A representable duration cannot overflow the write-attempt datetime."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    decision = build_questions("note.md", {}, "", load_vocabulary(tmp_path, "types.md"))
    patch = decision.evaluate(
        accept_answers(decision.questions),
        DecisionPolicy(THRESHOLDS),
        now=datetime(9999, 12, 20, tzinfo=UTC),
    )[0]
    assert "stale_after" not in patch and patch["type"] == "Reference"


@pytest.mark.parametrize(
    "body,description",
    [
        ("*An emphasized paragraph.*", "*An emphasized paragraph.*"),
        *(
            (
                "Verbatim" + separator + "paragraph.",
                "Verbatim" + separator + "paragraph.",
            )
            for separator in ("\u2028", "\u2029", "\x85")
        ),
        ("A title\n=======\n\nFirst prose.", "First prose."),
        (
            "Title first line\nTitle second line\n=================\n\nActual prose.",
            "Actual prose.",
        ),
        ("<!-- private comment -->\n\nActual prose.", None),
        ("<div>HTML block</div>\n\nActual prose.", None),
        (
            "# Title\n\nFirst exact paragraph.\nSecond line.\n\nLater.",
            "First exact paragraph.\nSecond line.",
        ),
        ("    \n\t\n# Title\n\nFirst prose.", "First prose."),
        *(
            ("# Title\n\n" + block + "\n\nGood later paragraph.", None)
            for block in [
                "x" * 281,
                "```\nsample\n```",
                "- list",
                "> quote",
                "| table |",
                "    code",
            ]
        ),
        ("- First item\n---\n\nLater prose.", None),
        ("A | B\n--- | ---\none | two\n\nLater prose.", None),
        *(
            ("First prose.\n" + block + "\nLater prose.", "First prose.")
            for block in ["# Next", "   ### Next ###", "- Next", "> Next", "```python"]
        ),
        *(
            (
                "First line.\n" + continuation + "\n\nLater.",
                "First line.\n" + continuation,
            )
            for continuation in [
                "    indented continuation",
                "\ttab continuation",
                "2. ordinary continuation",
            ]
        ),
        ("First line.\n    " + "x" * 270, None),
        ("First.\n-", None),
        ("é" * 280, "é" * 280),
        ("Text <span>inline</span>.", "Text <span>inline</span>."),
        ("<https://example.invalid>", "<https://example.invalid>"),
        ("<custom-tag>\n\nLater.", None),
        ("First.\n<custom-tag>", "First.\n<custom-tag>"),
        ("First.\n<!lowercase>", "First.\n<!lowercase>"),
        *(
            ("First.\n" + marker, "First.\n" + marker)
            for marker in ("+", "*", "1.", "1)")
        ),
        *(
            ("First.\n" + block + "\nLater.", "First.")
            for block in (
                "<!-- comment -->",
                "<?process?>",
                "<!DOCTYPE html>",
                "<![CDATA[",
                "<script>",
                "<DIV>",
                "***",
                "~~~lang",
                "1. item",
                "A | B\n--- | ---",
            )
        ),
    ],
)
def test_first_ordinary_paragraph_respects_markdown_blocks(
    tmp_path: Path, body: str, description: str | None
) -> None:
    """Copy the whole first prose paragraph or refuse it without skipping ahead."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md", {}, body, load_vocabulary(tmp_path, "types.md")
    )
    assert decision.bindings.get("description") == description


@pytest.mark.parametrize(
    "answer,disposition",
    [({"type": "refusal"}, "refusal"), ({"probability": 0.1}, "low-confidence")],
)
def test_source_outcome_retains_literal_candidate(
    tmp_path: Path, answer: dict[str, Any], disposition: str
) -> None:
    """Refusals and low scores retain the resource and actual source decision."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md",
        {},
        "https://example.invalid/evidence",
        load_vocabulary(tmp_path, "types.md"),
    )
    patch, fields = decision.evaluate(
        accept_answers(decision.questions, **{"sources:0": answer}),
        DecisionPolicy(THRESHOLDS),
        now=NOW,
    )
    assert "sources" not in patch
    source = fields["sources"]["decisions"]["sources:0"]
    assert source["proposed"] == "https://example.invalid/evidence"
    assert source["disposition"] == disposition and not source["accepted"]


@pytest.mark.parametrize(
    "path,body,expected",
    [
        ("note.md", "[escape](escaped.md) https://[invalid", []),
        (
            "note.md",
            "[web](https://example.invalid/a`code`b)",
            ["https://example.invalid/a"],
        ),
        ("note.md", "[web](<https://example.invalid/a`code`b>)", []),
        (
            "note.md",
            '<IMG alt="greater > less"\nsrc="HTTPS://example.invalid/image">',
            [],
        ),
        ("note.md", "![Image\n`code`\n](https://example.invalid/image)", []),
        (
            "note.md",
            "[Evidence\n`code`\n](https://example.invalid/proof)",
            ["https://example.invalid/proof"],
        ),
        ("note.md", "![Image\n`code`\n](fact.md)", []),
        ("note.md", "[Evidence\n`code`\n](fact.md)", ["fact.md"]),
        ("note.md", "[Evidence](fa`code`ct.md)", []),
        ("note.md", "[[fa`code`ct]]", []),
        (
            "note.md",
            "HTTPS://example.invalid/proof.",
            ["HTTPS://example.invalid/proof"],
        ),
        (
            "note.md",
            "HtTpS://example.invalid/Mixed%2fPath?q=A%26B.",
            ["HtTpS://example.invalid/Mixed%2fPath?q=A%26B"],
        ),
        ("note.md", "https://example.invalid/a`code`b", ["https://example.invalid/a"]),
        ("note.md", '<img src="https://example.invalid/diagram.png">', []),
        (
            "note.md",
            "See 'https://example.invalid/evidence'.",
            ["https://example.invalid/evidence"],
        ),
        (
            "note.md",
            "https://example.invalid/O'Reilly",
            ["https://example.invalid/O'Reilly"],
        ),
        *(
            ("note.md", usage + "\n\n[r]: https://example.invalid/image", [])
            for usage in ["![image][r]", "![r][]", "![r]", ""]
        ),
        *(
            ("note.md", image, [])
            for image in [
                "![image](https://example.invalid/image)",
                '![image](<https://example.invalid/image> "Title")',
            ]
        ),
        *(
            ("note.md", link, [])
            for link in [
                "[escape](../fact.md)",
                "[escape](%2e%2e/fact.md)",
                "[[../fact]]",
                "[escape][r]\n\n[r]: ../fact.md",
            ]
        ),
        *(
            (
                "note.md",
                "See https://example.invalid/Function_(mathematics)" + suffix,
                ["https://example.invalid/Function_(mathematics)"],
            )
            for suffix in [".", ").", "].", "}."]
        ),
        ("note.md", "[fact](fact.md)", ["fact.md"]),
        *(
            ("nested/note.md", link, ["fact.md"])
            for link in ["[fact](/fact.md)", "[fact](../fact.md)", "[[../fact]]"]
        ),
        (
            "note.md",
            "![diagram][r]\n\n[Evidence][r]\n\n[r]: https://example.invalid/evidence",
            ["https://example.invalid/evidence"],
        ),
        *(
            ("note.md", form.format(url=url), [url])
            for url in [
                "https://example.invalid/items/a%23b?q=x%26y",
                "https://example.invalid/?q=a%26b%3Dc",
            ]
            for form in ["[web]({url})", "[web][r]\n\n[r]: {url}"]
        ),
    ],
)
def test_source_candidates_reject_escaped_paths_and_bad_urls(
    tmp_path: Path, path: str, body: str, expected: list[str]
) -> None:
    """Native candidates exclude images/escapes and preserve literal resource identity."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    (tmp_path / "fact.md").write_text("Contained fact.")
    (tmp_path / "nested").mkdir()
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("Fixture.")
    (tmp_path / "escaped.md").symlink_to(outside)
    try:
        decision = build_questions(
            path, {}, body, load_vocabulary(tmp_path, "types.md")
        )
        assert decision.bindings["sources"] == {
            f"sources:{i}": value for i, value in enumerate(expected)
        }
    finally:
        outside.unlink()


@pytest.mark.parametrize("shape", ["deep", "wide", "set"])
def test_review_metadata_conversion_is_bounded(tmp_path: Path, shape: str) -> None:
    """Metadata conversion enforces the request bounds before expanding aliases."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    value: Any = "leaf"
    if shape == "deep":
        for _ in range(33):
            value = [value]
    elif shape == "wide":
        value = ["leaf"] * 10001
    else:
        value = set(range(10001))
    with pytest.raises(ValueError):
        build_questions(
            "note.md", {"custom": value}, "Body.", load_vocabulary(tmp_path, "types.md")
        )
