---
type: Reference
title: LEANN query-time embedding recomputation
description: "The storage and compute trade-off behind the deferred LEANN proposal."
kind: reference
subject_version: "arXiv:2506.08276v2 (2025-11-25)"
valid_for: "The technique in paper v2; not a guarantee about a current LEANN release"
generated:
  by: process:researching-references
  at: 2026-11-15T14:22:23+00:00
stale_after: 2027-05-15T14:22:23+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-15T14:22:23+00:00
status: stable
sources:
  - id: paper
    title: "LEANN: A Low-Storage Vector Index, v2"
    resource: https://arxiv.org/html/2506.08276v2
    accessed: 2026-11-15
  - id: upstream
    title: "StarTrail-org/LEANN README"
    resource: https://github.com/StarTrail-org/LEANN
    accessed: 2026-11-15
---

# LEANN query-time embedding recomputation

## Scope

Records the external technique behind [#1368](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1368).
No runtime module depends on LEANN, so there are no behavior-test pins.
The project's [revisit criteria](../design.md#deferred-query-time-recomputation-1368)
are a design decision, not a claim made by the paper.

## Claims

- LEANN trades stored full embeddings for query-time recomputation with the
  encoder used to build its proximity graph; the paper builds on HNSW
  (§1). [source: paper]
- It retains compressed vector codes and a pruned graph, uses approximate
  distances to select candidates, and recomputes only selected embeddings
  (§3–4.1). It is not an exact full-corpus scan with vectors omitted.
  [source: paper]
- Dynamic batching groups recomputations across graph-search steps to improve
  GPU utilization (§4.2); the evaluation uses an RTX 4090 workstation and
  an M1 Ultra Mac (§7.1). Those measurements do not establish latency for
  this server's hardware or embedding-provider round trips. [source: paper]
- The upstream repository describes selective recomputation and high-degree
  preserving graph pruning and links to the paper. Its storage-saving claims
  are upstream results, not measurements of this server. [source: upstream]

## Where this project departs from the subject

LEANN is not implemented here. See the [embedding-store design](../design.md#deferred-query-time-recomputation-1368)
for the existing alternatives and the conditions for reconsidering it.

## Not covered

Deployment-specific recomputation latency, provider cost, recall with this
server's filters, and update/recovery integration are unmeasured here.
This reference neither selects an ANN library nor evaluates an implementation.
