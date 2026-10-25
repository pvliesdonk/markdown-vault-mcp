"""The notes the v5.1.0 sidecar fixture was built from (#1505).

Kept as strings, not ``.md`` files: tests that scan ``tests/fixtures/`` as a
vault would otherwise index them.
"""

NOTES: dict[str, str] = {
    "alpha.md": (
        "# Alpha\n\nApples and pears grow in the orchard.\n\n"
        "## Harvest\n\nThe harvest is in autumn.\n"
    ),
    "beta.md": "# Beta\n\nBoats sail across the harbour.\n",
    "sub/gamma.md": "---\ntags: [ocean]\n---\n# Gamma\n\nWaves and tides.\n",
}
