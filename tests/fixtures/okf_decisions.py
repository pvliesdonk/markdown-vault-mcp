"""Synthetic Decisions wire fixtures, without credentials or external calls."""

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
