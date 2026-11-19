---
type: Reference
title: Docker Hub tags for PostgreSQL range images
kind: reference
description: Docker Hub tag pagination and observed image tag shapes for the PostgreSQL range check.
subject_version: "Docker Hub v2 tags API observed 2026-11-16"
valid_for: "Docker Hub v2 repository tags endpoint"
generated:
  by: process:researching-references
  at: 2026-11-16T12:00:00+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-16T12:00:00+00:00
stale_after: 2027-05-16T12:00:00+00:00
status: stable
sources:
  - id: pgvector-tags
    title: Docker Hub pgvector repository tags
    resource: https://hub.docker.com/v2/repositories/pgvector/pgvector/tags?page_size=100
    accessed: 2026-11-16
  - id: vchord-tags
    title: Docker Hub vchord-suite repository tags
    resource: https://hub.docker.com/v2/repositories/tensorchord/vchord-suite/tags?page_size=100
    accessed: 2026-11-16
---

# Docker Hub tags for PostgreSQL range images

## Scope

The consumer is `scripts/check_postgres_images.py`; the manual support decision
is in [the PostgreSQL design](../design.md).

## Claims

- The v2 repository tags endpoint accepts `page_size=100` and returns a
  `results` array of objects with `name` and a `next` URL for another page,
  or null at the end. [observed: anonymous GET of both sources and following next]
  [source: pgvector-tags] [source: vchord-tags]
  [pins: tests/test_postgres_image_currency.py::test_hub_follows_next_page, tests/test_postgres_image_currency.py::test_malformed_hub_response_fails]
- Every `next` URL is an absolute `https://hub.docker.com/` URL, so the check
  refuses to follow one that names another scheme or host.
  [observed: full pagination of both sources on 2026-11-17]
  [source: pgvector-tags] [source: vchord-tags]
  [pins: tests/test_postgres_image_currency.py::test_hub_refuses_next_page_off_docker_hub]
- The repositories include pgvector tags shaped `X.Y.Z-pgN` and vchord-suite
  tags shaped `pgN-YYYYMMDD`, alongside variant tags; this check deliberately
  accepts only those strict shapes. [observed: anonymous GET of both sources]
  [source: pgvector-tags] [source: vchord-tags]
  [pins: tests/test_postgres_image_currency.py::test_floors_equal_tags_and_variants_are_ignored, tests/test_postgres_image_currency.py::test_new_vchord_date_is_reported]
