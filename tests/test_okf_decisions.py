"""Decisions contracts: exact wire, refusals, bounded input and safe failures."""

import copy
import json
from typing import Any

import httpx
import pytest

from markdown_vault_mcp._okf_decisions import DecisionError, DecisionsClient
from tests.fixtures.okf_decisions import ANSWERS, PARTS, QUESTIONS


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
