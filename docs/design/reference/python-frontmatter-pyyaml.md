---
type: Reference
title: Python frontmatter serialization and mapping order
description: Pinned serializer keyword forwarding, recursive mapping order and formatting boundaries.
kind: reference
subject_version: "python-frontmatter 1.3.0; PyYAML 6.0.3"
valid_for: "These pinned versions; re-research when either dependency changes"
generated:
  by: process:researching-references
  at: 2026-11-14T11:39:40+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-14T11:39:40+00:00
stale_after: 2027-05-14T11:39:40+00:00
status: stable
sources:
  - id: frontmatter
    title: python-frontmatter 1.3.0 source distribution
    resource: https://files.pythonhosted.org/packages/9d/e8/79cbe69864d44f3b48e70ebee0a872a7d5a4e7150c9f8577ed7a5beefff0/python_frontmatter-1.3.0.tar.gz
    accessed: 2026-11-14
  - id: pyyaml
    title: PyYAML 6.0.3 source distribution
    resource: https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz
    accessed: 2026-11-14
---

# Python frontmatter serialization and mapping order

The document writer and the two OKF write transforms depend on these
serializer behaviors. This page covers YAML mappings and formatting; it does
not cover alternate frontmatter handlers or Git conflict-copy generation.

## Claims

- `frontmatter.dumps` forwards keyword arguments through `BaseHandler.format`
  and `YAMLHandler.export` to `yaml.dump`. The YAML handler supplies safe
  dumping, block style and Unicode defaults, but no sorting override
  (`frontmatter/__init__.py`, `default_handlers.py`). [source: frontmatter]
  [pins: tests/test_managers_document.py::TestWrite::test_write_with_frontmatter]
- PyYAML's `dump_all` defaults to `sort_keys=True`. Its mapping representer
  starts with `mapping.items()` and sorts only when this flag is true;
  nested mappings and mappings inside sequences use the same representer
  setting (`lib/yaml/__init__.py`, `representer.py`). Both `SafeDumper` and
  `CSafeDumper` pass that flag to the representer (`dumper.py`, `cyaml.py`).
  [source: pyyaml]
  [pins: tests/test_managers_document.py::TestWrite::test_write_with_frontmatter]
- The YAML constructor inserts mapping entries in node order
  (`lib/yaml/constructor.py`, `BaseConstructor.construct_mapping`), so a
  parsed ordinary mapping exposes the serialized field order. [source: pyyaml]
  [pins: tests/test_managers_document.py::TestWrite::test_write_with_frontmatter]
- Formatting is reconstructed: the YAML handler dumps metadata afresh and
  `BaseHandler.format` strips the assembled document. Mapping-order
  preservation does not promise comments, quoting, whitespace or exact-byte
  fidelity. [source: frontmatter]

## Project decision and refutation

The [frontmatter contract](../design.md#frontmatter-handling) disables sorting
at the three existing content-write serializer owners. The OKF transforms
retain surviving field positions while changing their owned trust fields.
[pins: tests/test_okf_write.py::TestApplyOkfWriteStamp::test_preserves_sources_and_other_fields, tests/test_okf_write.py::TestAppendOkfVerification::test_preserves_body_and_other_fields]

The source distributions' SHA-256 values match `uv.lock`: python-frontmatter
`acc73e477a568dc2a25c9e130c6c68ae8daa8c204c8f7e813db47d6a7280dcf2`;
PyYAML `d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f`.
The refute pass rechecked the forwarding chain, both safe dumper paths, the
recursive representation calls and loader insertion. It found no handler
override that would prevent `sort_keys=False` reaching PyYAML. These claims
are scoped to the pinned sources, not an assertion about every YAML emitter.
