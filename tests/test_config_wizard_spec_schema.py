"""Validate the shipped wizard-spec.json against the canonical JSON Schema.

Template-owned and generic: loads whatever ``wizard-spec.json`` this project
ships and checks it against ``wizard-spec-schema.json`` plus the cross-reference
rules JSON Schema cannot express (unique ids, dangling references, secret keys
declared by a question). Runs in the main test lane (no browser required), so it
is the gate that catches spec drift — e.g. a guard ``level`` outside {warning,
info, error} or a ``meta`` block that a stale spec never added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest

_WIZARD_DIR = (
    Path(__file__).resolve().parent.parent / "docs" / "javascripts" / "config-wizard"
)
_SPEC_PATH = _WIZARD_DIR / "wizard-spec.json"
_SCHEMA_PATH = _WIZARD_DIR / "wizard-spec-schema.json"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(_SPEC_PATH.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _minimal_valid_spec() -> dict[str, Any]:
    return {
        "version": 1,
        "meta": {
            "projectName": "demo",
            "dockerImage": "ghcr.io/acme/demo:latest",
            "envPrefix": "DEMO",
        },
        "secretKeys": [],
        "questions": [
            {
                "id": "deployment",
                "label": "Where?",
                "type": "select",
                "options": [
                    {"value": "local", "label": "Local"},
                    {"value": "server", "label": "Server"},
                ],
            }
        ],
        "guards": [],
    }


def test_schema_itself_is_valid(schema: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)


def test_shipped_spec_matches_schema(
    spec: dict[str, Any], schema: dict[str, Any]
) -> None:
    jsonschema.validate(spec, schema)


def test_question_ids_are_unique(spec: dict[str, Any]) -> None:
    ids = [q["id"] for q in spec["questions"]]
    assert len(ids) == len(set(ids)), f"duplicate question id(s): {ids}"


def test_showif_references_existing_ids(spec: dict[str, Any]) -> None:
    ids = {q["id"] for q in spec["questions"]}
    for q in spec["questions"]:
        for key in q.get("showIf") or {}:
            assert key in ids, f"showIf references unknown question id: {key}"


def test_guard_when_references_existing_ids(spec: dict[str, Any]) -> None:
    ids = {q["id"] for q in spec["questions"]}
    for guard in spec.get("guards", []):
        for key in guard["when"]:
            assert key in ids, f"guard.when references unknown question id: {key}"


def test_secret_keys_are_declared_vars(spec: dict[str, Any]) -> None:
    declared = {q.get("var") for q in spec["questions"]}
    for key in spec.get("secretKeys", []):
        assert key in declared, f"secretKey not declared by any question var: {key}"


def test_schema_rejects_unknown_guard_level(schema: dict[str, Any]) -> None:
    bad = _minimal_valid_spec()
    bad["guards"] = [
        {"level": "warn", "message": "x", "when": {"deployment": ["server"]}}
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_rejects_missing_meta(schema: dict[str, Any]) -> None:
    bad = _minimal_valid_spec()
    del bad["meta"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_rejects_dockervolume_and_dockerpath_together(
    schema: dict[str, Any],
) -> None:
    bad = _minimal_valid_spec()
    bad["questions"].append(
        {
            "id": "data_dir",
            "label": "Data",
            "type": "text",
            "var": "DEMO_DATA_DIR",
            "dockerVolume": "/data/app",
            "dockerPath": "/data/state/app",
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_accepts_dockervolume_alone(schema: dict[str, Any]) -> None:
    spec = _minimal_valid_spec()
    spec["questions"].append(
        {
            "id": "data_dir",
            "label": "Data",
            "type": "text",
            "var": "DEMO_DATA_DIR",
            "dockerVolume": "/data/app",
        }
    )
    jsonschema.validate(spec, schema)


def test_schema_rejects_dockervolume_without_var(schema: dict[str, Any]) -> None:
    bad = _minimal_valid_spec()
    bad["questions"].append(
        {"id": "data_dir", "label": "Data", "type": "text", "dockerVolume": "/data/app"}
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_rejects_dockerpath_without_var(schema: dict[str, Any]) -> None:
    bad = _minimal_valid_spec()
    bad["questions"].append(
        {
            "id": "index_path",
            "label": "Index",
            "type": "text",
            "dockerPath": "/data/state/index.db",
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_schema_accepts_dockerpath_alone(schema: dict[str, Any]) -> None:
    spec = _minimal_valid_spec()
    spec["questions"].append(
        {
            "id": "index_path",
            "label": "Index",
            "type": "text",
            "var": "DEMO_INDEX_PATH",
            "dockerPath": "/data/state/index.db",
        }
    )
    jsonschema.validate(spec, schema)
