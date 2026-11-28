---
name: okf-spec
description: Use when you create, edit, check or interpret notes in an Open Knowledge Format (OKF) bundle in this vault; holds the full text of OKF 0.3, the version this server reads and writes.
license: SPEC.md and LICENSE.md are Apache-2.0; SKILL.md and PROVENANCE.md are MIT.
---

# Open Knowledge Format 0.3

This server reads and writes one OKF version, 0.3, whatever `okf_version` a
bundle declares. `SPEC.md` in this skill (`skill://okf-spec/SPEC.md`) is the
full, unchanged text of that version; `PROVENANCE.md` says where it comes
from.

Read `SPEC.md` before you write frontmatter, links, `index.md` or `log.md`
in a bundle, or when you need to know what a field or a validation finding
means. Where it states a rule, follow the spec, not a summary of it.

Its sections:

- §3 bundle structure, reserved filenames, what counts as a concept;
- §4 concept documents and their frontmatter;
- §5 provenance (`sources`), trust (`generated`, `verified`, tiers) and
  lifecycle (`status`, `superseded_by`, `stale_after`);
- §6 links and path fields; §7 the actor convention;
- §8 index files and bundle metadata; §9 log files;
- §10 attested computations; §11 conformance; §12 versioning;
- §13 changes from 0.2; Appendix A a worked example.
