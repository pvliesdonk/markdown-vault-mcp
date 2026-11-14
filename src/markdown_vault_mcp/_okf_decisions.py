"""Bounded Decisions transport; see docs/design/reference/openai-decisions.md."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx


class DecisionError(Exception):
    """A safe endpoint failure, optionally terminating further requests.

    Args:
        message: Public diagnostic without response content or credentials.
        terminal: Whether processing subsequent notes must stop.
    """

    def __init__(self, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


def _metric(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _json_bounds(value: Any, depth: int = 0, budget: list[int] | None = None) -> int:
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Decisions JSON input exceeds 10,000 values.")
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if depth >= 32:
            raise ValueError("Decisions JSON input exceeds 32 container levels.")
        children = value.values() if isinstance(value, dict) else value
        return 1 + sum(_json_bounds(child, depth + 1, budget) for child in children)
    return 1


def _validate_request(
    parts: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> None:
    if not 1 <= len(questions) <= 400:
        raise ValueError("Decisions requires 1..400 questions per note.")
    names = [question.get("name") for question in questions]
    if any(not isinstance(name, str) or not name for name in names) or len(
        set(names)
    ) != len(names):
        raise ValueError("Decisions questions require unique nonempty names.")
    for question in questions:
        if question.get("type") not in {"choice", "predicate"} or not question.get(
            "instructions"
        ):
            raise ValueError("Invalid Decisions question.")
        if question["type"] == "choice":
            choices = question.get("choices", [])
            values = [choice.get("value") for choice in choices]
            if (
                not 2 <= len(values) <= 255
                or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("Decisions requires 2..255 unique string choices.")
    for part in parts:
        if part.get("type") == "input_json":
            _validate_json(part.get("data"))


def _validate_json(data: Any) -> None:
    _json_bounds(data)
    try:
        encoded = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Decisions input must be valid JSON.") from None
    if len(encoded) > 256 * 1024:
        raise ValueError("Decisions JSON input exceeds 256 KiB.")


def _valid_answer(answer: Any, question: dict[str, Any]) -> bool:
    if not isinstance(answer, dict):
        return False
    name, kind = answer.get("name"), answer.get("type")
    if kind == "refusal":
        return name is None or name == question["name"]
    if name != question["name"] or kind != question["type"]:
        return False
    if kind == "predicate":
        return _metric(answer.get("probability"))
    return _valid_choice(answer, question)


def _valid_choice(answer: dict[str, Any], question: dict[str, Any]) -> bool:
    values = [choice["value"] for choice in question["choices"]]
    distribution = answer.get("probabilities")
    if (
        type(answer.get("choice")) is not str
        or answer["choice"] not in values
        or not _metric(answer.get("confidence"))
    ):
        return False
    if not isinstance(distribution, list) or len(distribution) != len(values):
        return False
    seen: set[str] = set()
    for item in distribution:
        if not isinstance(item, dict) or type(item.get("value")) is not str:
            return False
        value = item["value"]
        if value not in values or value in seen or not _metric(item.get("probability")):
            return False
        seen.add(value)
    return True


class DecisionsClient:
    """One-request-per-note synchronous Platform transport.

    Args:
        base_url: Independently configured Platform API base URL.
        api_key: Platform key; never included in diagnostics.
        transport: Optional fixture HTTP transport.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/decisions"
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        """Close the owned HTTP connection pool."""
        self._client.close()

    def decide(
        self, input_parts: list[dict[str, Any]], questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Submit one note, returning only wholly validated ordered answers.

        Args:
            input_parts: Path, raw metadata and body content parts.
            questions: Closed, named predicates and choices in stable order.

        Returns:
            Validated answers in question order, including partial refusals.

        Raises:
            ValueError: Input violates the endpoint's request bounds.
            DecisionError: Transport, HTTP or response validation failed.
        """
        _validate_request(input_parts, questions)
        try:
            response = self._client.post(
                self._url,
                json={
                    "model": "gpt-6-luna",
                    "input": [{"role": "user", "content": input_parts}],
                    "questions": questions,
                },
            )
        except httpx.HTTPError:
            raise DecisionError("Decisions network request failed.") from None
        if response.status_code != 200:
            raise DecisionError(
                f"Decisions HTTP {response.status_code}.",
                terminal=response.status_code in {401, 403, 429},
            )
        try:
            payload = response.json()
            answers = payload.get("answers") if isinstance(payload, dict) else None
            if (
                not isinstance(answers, list)
                or len(answers) != len(questions)
                or not all(
                    _valid_answer(a, q) for a, q in zip(answers, questions, strict=True)
                )
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise DecisionError("Invalid Decisions response.") from None
        return answers
