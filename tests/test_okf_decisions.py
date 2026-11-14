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
    else:
        _mutate_distribution_or_name(first, second, mutation)
    return answers


def _mutate_distribution_or_name(
    first: dict[str, Any], second: dict[str, Any], mutation: str
) -> None:
    if mutation == "distribution_dict":
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
    [(401, True), (403, True), (429, True), (500, False), (302, False)],
)
def test_decisions_error_is_safe(status: int, terminal: bool) -> None:
    """Status failures never reveal a key/body or retry/follow redirects."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
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


@pytest.mark.parametrize("kind", ["network", "json"])
def test_transport_and_json_failure_is_safe(kind: str) -> None:
    """Raw transport exception and malformed response text stay private."""

    def respond(request: httpx.Request) -> httpx.Response:
        if kind == "network":
            raise httpx.ConnectError("synthetic-only", request=request)
        return httpx.Response(200, text="private-body")

    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "synthetic-only",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DecisionError) as error:
            client.decide(PARTS, QUESTIONS)
        assert not error.value.terminal
        assert "synthetic-only" not in str(error.value)
        assert "private-body" not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize(
    "kind", ["empty", "questions", "choices", "depth", "values", "bytes"]
)
def test_input_limits_precede_http(kind: str) -> None:
    """Oversize or invalid input is a local request error, never paid work."""
    calls: list[httpx.Request] = []
    parts, questions = copy.deepcopy(PARTS), copy.deepcopy(QUESTIONS)
    if kind == "empty":
        questions = []
    elif kind == "questions":
        questions = [QUESTIONS[0]] * 401
    elif kind == "choices":
        questions[1]["choices"] = questions[1]["choices"][:1]
    else:
        data: Any = {}
        if kind == "depth":
            for _ in range(33):
                data = {"nested": data}
        elif kind == "values":
            data = {"values": [0] * 10001}
        else:
            data = {"text": "é" * 131072}
        parts[1]["data"] = data
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
        with pytest.raises(ValueError):
            client.decide(parts, questions)
        assert calls == []
    finally:
        client.close()


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
    ],
)
def test_vocabulary_containment(tmp_path: Path, path: str) -> None:
    """Reserved/skill/non-note and escaping vocabulary paths are refused."""
    outside = tmp_path.parent / "outside-vocabulary.md"
    outside.write_text(vocabulary_text())
    (tmp_path / "escape.md").symlink_to(outside)
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
    patch = decision.accepted_patch(
        accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
    )
    assert patch["description"] == "First exact paragraph.\nSecond line."
    assert patch["sources"] == [
        {"resource": "fact.md"},
        {"resource": "https://example.invalid/a"},
        {"resource": "https://example.invalid/ref"},
        {"resource": "https://example.invalid/b"},
    ]
    assert patch["stale_after"] == "2026-12-14T10:00:00Z"
    assert "generated" not in patch and "last_edited" not in patch


@pytest.mark.parametrize(
    "block",
    ["x" * 281, "```\nsample\n```", "- list", "> quote", "| table |", "    code"],
)
def test_unsuitable_first_block_is_not_skipped(tmp_path: Path, block: str) -> None:
    """A later paragraph cannot replace an unsuitable first block."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md",
        {},
        "# Title\n\n" + block + "\n\nGood later paragraph.",
        load_vocabulary(tmp_path, "types.md"),
    )
    names = [q["name"] for q in decision.questions]
    assert "description" not in names and "stale_after" not in names


@pytest.mark.parametrize(
    "confidence,wrong,meaningful,expected",
    [
        (0.8, 0.951, 0.8, True),
        (0.799, 0.951, 0.8, False),
        (0.8, 0.95, 0.8, False),
        (0.8, 0.951, 0.799, False),
    ],
)
def test_thresholds_and_noop_policy(
    tmp_path: Path, confidence: float, wrong: float, meaningful: float, expected: bool
) -> None:
    """Equality is inclusive for acceptance and strict for existing-value wrongness."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md", {"type": "Runbook"}, "", load_vocabulary(tmp_path, "types.md")
    )
    answers = accept_answers(
        decision.questions,
        type={"confidence": confidence},
        type_wrong={"probability": wrong},
        meaningful={"probability": meaningful},
        status="stable",
    )
    assert (
        decision.accepted_patch(answers, DecisionPolicy(THRESHOLDS), now=NOW)
        == {"type": "Reference"}
    ) is expected


def test_evidence_values_cannot_be_invented(tmp_path: Path) -> None:
    """Existing raw values and partial refusals survive; equal classifications are noops."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    metadata = {
        "type": "Reference",
        "status": "draft",
        "description": "Keep.",
        "stale_after": "keep-date",
        "sources": "malformed",
    }
    decision = build_questions(
        "note.md",
        metadata,
        "Candidate.\n\nhttps://example.invalid/a",
        load_vocabulary(tmp_path, "types.md"),
    )
    assert (
        decision.accepted_patch(
            accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
        )
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
    patch = decision.accepted_patch(answers, DecisionPolicy(THRESHOLDS), now=NOW)
    assert "type" not in patch
    assert patch["sources"] == [
        {"resource": "https://example.invalid/a", "id": "keep"},
        {"resource": "https://example.invalid/b"},
    ]
    answers[0] = {"type": "refusal", "name": None}
    assert decision.accepted_patch(answers, DecisionPolicy(THRESHOLDS), now=NOW) == {}


@pytest.mark.parametrize(
    "change", [{"type": float("nan")}, {"type": True}, {"extra": 0.8}, {"status": 0.96}]
)
def test_policy_rejects_invalid_thresholds(change: dict[str, Any]) -> None:
    """Malformed policy cannot be treated as uncertainty or weaken replacement."""
    with pytest.raises(ValueError):
        DecisionPolicy({**THRESHOLDS, **change})


@pytest.mark.parametrize("confidence,expected", [(0.8, True), (0.799, False)])
def test_missing_type_threshold_is_inclusive(
    tmp_path: Path, confidence: float, expected: bool
) -> None:
    """Missing values accept exactly the configured confidence boundary."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions("note.md", {}, "", load_vocabulary(tmp_path, "types.md"))
    patch = decision.accepted_patch(
        accept_answers(
            decision.questions, type={"confidence": confidence}, status="stable"
        ),
        DecisionPolicy(THRESHOLDS),
        now=NOW,
    )
    assert (patch == {"type": "Reference"}) is expected


def test_duration_overflow_refuses_only_expiry(tmp_path: Path) -> None:
    """A representable duration cannot overflow the write-attempt datetime."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    decision = build_questions("note.md", {}, "", load_vocabulary(tmp_path, "types.md"))
    patch = decision.accepted_patch(
        accept_answers(decision.questions),
        DecisionPolicy(THRESHOLDS),
        now=datetime(9999, 12, 20, tzinfo=UTC),
    )
    assert "stale_after" not in patch and patch["type"] == "Reference"


@pytest.mark.parametrize(
    "body,description",
    [
        ("*An emphasized paragraph.*", "*An emphasized paragraph.*"),
        ("A title\n=======\n\nFirst prose.", "First prose."),
    ],
)
def test_first_ordinary_paragraph_respects_markdown_blocks(
    tmp_path: Path, body: str, description: str
) -> None:
    """Emphasis is prose and leading setext headings are headings."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md", {}, body, load_vocabulary(tmp_path, "types.md")
    )
    patch = decision.accepted_patch(
        accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
    )
    assert patch["description"] == description


def test_malformed_source_record_is_preserved_without_append(tmp_path: Path) -> None:
    """Whitespace-only resource records must not be silently treated as valid."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    metadata = {"sources": [{"resource": " "}]}
    decision = build_questions(
        "note.md",
        metadata,
        "Paragraph.\n\nhttps://example.invalid/evidence",
        load_vocabulary(tmp_path, "types.md"),
    )
    assert "sources" not in decision.accepted_patch(
        accept_answers(decision.questions), DecisionPolicy(THRESHOLDS), now=NOW
    )


@pytest.mark.parametrize(
    "body",
    [
        "- First item\n---\n\nLater prose.",
        "A | B\n--- | ---\none | two\n\nLater prose.",
    ],
)
def test_description_never_skips_or_copies_unsuitable_first_block(
    tmp_path: Path, body: str
) -> None:
    """A list before a thematic break and a pipe-optional table aren't prose."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    decision = build_questions(
        "note.md", {}, body, load_vocabulary(tmp_path, "types.md")
    )
    assert "description" not in decision.bindings
