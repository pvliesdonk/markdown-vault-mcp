---
type: Reference
title: OpenAI Decisions endpoint
description: The pinned request, answer, bounds and access contract for local OKF curation.
kind: reference
subject_version: "openai-python 3dae6090cc31cc64a437fe0bcee740b6d81ed2cc"
valid_for: "Pinned SDK contract; moving Decisions service as of 2026-11-14"
generated:
  by: process:researching-references
  at: 2026-11-14T12:18:00+00:00
stale_after: 2027-05-14T12:18:00+00:00
status: stable
sources:
  - id: sdk
    title: Pinned Decisions SDK resource and types
    resource: https://github.com/openai/openai-python/tree/3dae6090cc31cc64a437fe0bcee740b6d81ed2cc/src/openai
    accessed: 2026-11-14
  - id: guide
    title: Decisions guide
    resource: https://developers.openai.com/api/docs/guides/decisions
    accessed: 2026-11-14
  - id: errors
    title: API error codes
    resource: https://developers.openai.com/api/docs/guides/error-codes
    accessed: 2026-11-14
---

# OpenAI Decisions endpoint

## Scope

The local OKF client depends on `resources/decisions.py`,
`types/decision_create_params.py`, `types/decision.py`,
`types/decision_input_part_union_param.py` and
`types/shared_params/decision_input_json.py` at the pinned SDK commit.
No installed SDK upgrade or paid request was used to research this contract.

## Claims

- The resource sends POST `/decisions`; only `gpt-6-luna` is supported.
  Input accepts a user message with text parts and JSON parts shaped
  `{type: input_json, data: ...}`. Questions use `instructions`, `name` and
  `type`, with `choices` containing typed `value` and optional `description`.
  [source: sdk] [pins: tests/test_okf_decisions.py::test_decisions_exact_wire_contract]
- The request accepts 1–400 questions and 2–255 unique choices per choice
  question. JSON parts allow 32 container levels, 10,000 values and 256 KiB
  of compact UTF-8 JSON. [source: sdk]
  [pins: tests/test_okf_decisions.py::test_input_limits_precede_http]
- Answers retain question order. Choice answers contain `choice`,
  `confidence` and a list of `{value, probability}` records; predicates
  contain `probability`. A refusal replaces one answer and may have a null
  name; independent questions may still succeed. [source: sdk]
  [pins: tests/test_okf_decisions.py::test_null_name_refusal_uses_question_order]
- Choice values are typed: booleans and strings are distinct. This client
  submits strings and rejects replies outside those submitted values or
  malformed metrics. [source: sdk]
  [pins: tests/test_okf_decisions.py::test_invalid_reply_never_returns_answers]
- Invalid authentication uses 401; exhausted credit and request rate limits
  can both use 429. The client terminates further work for 401, 403 or 429,
  without exposing response bodies or authorization. [source: errors]
  [pins: tests/test_okf_decisions.py::test_decisions_error_is_safe]
- At research time, uncached Decisions input with `gpt-6-luna` costs $0.10
  per million tokens and output tokens are not charged. Cache writes are
  charged, cache hits discounted, and regional and long-context multipliers
  apply. These rates apply to `/v1/decisions`, not every model endpoint.
  [source: guide]

## Where this project departs from the subject

The [local maintenance policy](../design.md#okf-read-semantics-phase-1-960)
uses only named predicate and string-choice questions. It binds unnamed
refusals by order, requires non-refusal names, refuses nonfinite metrics,
and performs no retry or redirect. It accepts a Platform API key only;
subscription token extraction is outside the operator contract.

## Not covered

Subscription eligibility was not tested. Pricing and service availability
are moving claims, not guarantees. No billing, credentials or live endpoint
probe is required by the fixture tests.
