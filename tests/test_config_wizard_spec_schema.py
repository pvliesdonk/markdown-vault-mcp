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


def _assert_gates_self_contained(
    gates: dict[str, Any], by_id: dict[str, Any], owner: str
) -> None:
    """Assert a ``showIf``/``when`` map carries its referenced questions' gates.

    Both ``isVisible`` and the guard evaluator (generators.js / wizard.js) check
    raw ``answers[k]`` with no cascade. So if ``gates`` references question B and
    B is itself gated on C, ``gates`` must also gate on C — with a value set no
    wider than B's — or it stays active when B is hidden but B's stale answer
    lingers.
    """
    for parent_id in gates:
        parent_gates = by_id.get(parent_id, {}).get("showIf") or {}
        for grand_id, grand_allowed in parent_gates.items():
            assert grand_id in gates, (
                f"{owner} gates on {parent_id!r} but not on {parent_id!r}'s own "
                f"gate {grand_id!r}; it stays active when {parent_id!r} is hidden"
            )
            assert set(gates[grand_id]) <= set(grand_allowed), (
                f"{owner} allows {grand_id!r} values {gates[grand_id]} wider than "
                f"{parent_id!r}'s {grand_allowed}; it can be active when "
                f"{parent_id!r} is not"
            )


def test_showif_and_guard_gates_cascade(spec: dict[str, Any]) -> None:
    """Question ``showIf`` and guard ``when`` must stay self-contained.

    Neither ``isVisible`` (which gates ``buildEnvMap``'s var emission) nor the
    guard evaluator cascades — each checks raw ``answers[k]`` only. A question
    gated only on ``auth`` keeps emitting its ``var`` after the user flips
    ``deployment`` back to a value that hides the ``auth`` control, because the
    stale ``auth`` answer persists; a guard gated only on ``auth`` fires its
    warning the same way. So whenever a ``showIf``/``when`` gates on B and B
    itself gates on C, it must also gate on C (values no wider than B's). This
    asserts that transitive closure for every question and every guard.
    """
    by_id = {q["id"]: q for q in spec["questions"]}
    for q in spec["questions"]:
        _assert_gates_self_contained(
            q.get("showIf") or {}, by_id, f"question {q['id']!r}"
        )
    for i, guard in enumerate(spec.get("guards", [])):
        _assert_gates_self_contained(guard.get("when") or {}, by_id, f"guard[{i}]")


def _chain_spec(questions: list[dict[str, Any]]) -> dict[str, Any]:
    base = _minimal_valid_spec()
    base["questions"] = questions
    return base


def _sel(qid: str, show_if: dict[str, Any] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {
        "id": qid,
        "label": qid.upper(),
        "type": "select",
        "options": [{"value": "on", "label": "On"}, {"value": "off", "label": "Off"}],
    }
    if show_if is not None:
        q["showIf"] = show_if
    return q


def test_cascade_check_enforces_full_transitive_closure() -> None:
    """The one-level-per-question check composes to full transitive closure.

    A 3-level chain d <- c <- b <- a: `a` correctly carries b and c but omits
    d's gate. The check must still reject it, because `a` gates on c and c gates
    on d, so c's gate (d) is re-checked against `a`. This pins the docstring's
    "transitive closure" claim — the single-level check is not merely a
    one-hop guard.
    """
    chain = [
        _sel("d"),
        _sel("c", {"d": ["on"]}),
        _sel("b", {"c": ["on"], "d": ["on"]}),
    ]
    # Fully closed: a carries every ancestor gate -> passes.
    good = _chain_spec([*chain, _sel("a", {"b": ["on"], "c": ["on"], "d": ["on"]})])
    test_showif_and_guard_gates_cascade(good)
    # a omits the transitive gate d -> must be rejected.
    bad = _chain_spec([*chain, _sel("a", {"b": ["on"], "c": ["on"]})])
    with pytest.raises(AssertionError):
        test_showif_and_guard_gates_cascade(bad)


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
