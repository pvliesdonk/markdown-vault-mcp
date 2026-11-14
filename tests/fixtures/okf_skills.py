"""Synthetic skill bytes shared by OKF read and retrieval tests."""

SKILL = """---
name: demo
description: Demonstrate zebra maintenance.
metadata:
  status: deprecated
  stale_after: '2020-01-01'
  generated:
    by: human:alex
    at: '2026-11-13T00:00:00Z'
  verified:
    - by: human:reviewer
  sources:
    - resource: https://example.invalid/spec
---
# Demo

Zebra maintenance instructions.

## Steps

Run the zebra checks.
"""
