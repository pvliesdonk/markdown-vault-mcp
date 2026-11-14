"""Synthetic Decisions wire fixtures, without credentials or external calls."""

from pathlib import Path
from typing import Any

PARTS: list[dict[str, Any]] = [
    {"type": "input_text", "text": "Path: notes/example.md"},
    {"type": "input_json", "data": {"type": "Reference"}},
    {"type": "input_text", "text": "An exact paragraph.\n"},
]
QUESTIONS: list[dict[str, Any]] = [
    {
        "type": "predicate",
        "name": "meaningful",
        "instructions": "Is curation meaningful?",
    },
    {
        "type": "choice",
        "name": "type",
        "instructions": "Classify the page.",
        "choices": [
            {"value": "Reference", "description": "Living facts."},
            {"value": "Runbook", "description": "Ordered steps."},
        ],
    },
]
ANSWERS: list[dict[str, Any]] = [
    {"type": "predicate", "name": "meaningful", "probability": 0.9},
    {
        "type": "choice",
        "name": "type",
        "choice": "Reference",
        "confidence": 0.8,
        "probabilities": [
            {"value": "Reference", "probability": 0.8},
            {"value": "Runbook", "probability": 0.2},
        ],
    },
]


def vocabulary_text(*, durations: bool = True) -> str:
    """Return an operator vocabulary containing only synthetic records."""
    return (
        """---
types:
  - {value: Reference, description: Living facts.}
  - {value: Runbook, description: Ordered steps.}
statuses:
  - {value: draft, description: Not confirmed.}
  - {value: stable, description: Confirmed.}
  - {value: deprecated, description: Superseded.}
"""
        + (
            """stale_after_durations:
  - {value: 30d, description: Review monthly.}
  - {value: 90d, description: Review quarterly.}
"""
            if durations
            else ""
        )
        + "---\nVocabulary.\n"
    )


def accept_answers(
    questions: list[dict[str, Any]], **overrides: Any
) -> list[dict[str, Any]]:
    """Make closed fixture answers, with explicit overrides per named question."""
    result = []
    for question in questions:
        name = question["name"]
        if question["type"] == "predicate":
            answer = {"name": name, "type": "predicate", "probability": 0.99}
        else:
            values = [item["value"] for item in question["choices"]]
            choice = (
                overrides[name] if isinstance(overrides.get(name), str) else values[0]
            )
            answer = {
                "name": name,
                "type": "choice",
                "choice": choice,
                "confidence": 0.99,
                "probabilities": [
                    {"value": value, "probability": 0.99 if value == choice else 0.0}
                    for value in values
                ],
            }
        if isinstance(overrides.get(name), dict):
            answer.update(overrides[name])
        result.append(answer)
    return result


def write_notes(root: Path, notes: dict[str, str]) -> None:
    """Write synthetic notes, creating their parent folders.

    Args:
        root: Isolated fixture vault.
        notes: Relative paths and complete fixture text.
    """
    for name, text in notes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
