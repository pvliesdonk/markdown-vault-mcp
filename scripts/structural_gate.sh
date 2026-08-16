#!/usr/bin/env bash
# Structural diff gate — the ONE implementation shared by the pre-push hook
# (.pre-commit-config.yaml) and ci.yml's `structure` job, so the command and
# its ruff selection cannot drift between the two.
#
# Compare-branch resolution, in order:
#   1. STRUCTURAL_GATE_BASE — explicit override; CI sets it to the PR's
#      actual base (origin/<base_ref>).
#   2. Derived: the nearest base among origin/main and origin/release/*,
#      picked by most-recent merge-base with HEAD.  A feature branch off main
#      resolves to origin/main; a backport branch off release/X.Y resolves to
#      origin/release/X.Y, so backport PRs are measured against the branch
#      they actually target instead of an ever-growing diff vs main.
#   3. origin/main, when nothing else resolves (fresh clone, no remotes).
set -euo pipefail

base="${STRUCTURAL_GATE_BASE:-}"
if [ -z "$base" ]; then
    best_ts=0
    for ref in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin/main 'refs/remotes/origin/release/*'); do
        mb="$(git merge-base HEAD "$ref" 2>/dev/null)" || continue
        ts="$(git log -1 --format=%ct "$mb")"
        # Strict > prefers the earlier-listed origin/main on a timestamp tie.
        if [ "$ts" -gt "$best_ts" ]; then
            best_ts="$ts"
            base="$ref"
        fi
    done
fi
base="${base:-origin/main}"

# Test seam: print the resolved base and exit, so the derivation is testable
# without diff-quality or a venv (see tests/test_structural_gate.py).
if [ -n "${STRUCTURAL_GATE_PRINT_BASE:-}" ]; then
    printf '%s\n' "$base"
    exit 0
fi

# No Python in the diff → nothing for the gate to measure (mirrors the
# diff-cover guard; the pre-push hook also skips via `types: [python]`).
has_py="$(git diff --name-only "${base}...HEAD" | grep -c '\.py$' || true)"
if [ "$has_py" -eq 0 ]; then
    echo "No Python source changes — skipping structural diff gate"
    exit 0
fi

echo "structural gate: comparing against ${base}"
uv run diff-quality --violations=ruff.check \
    --options="--extend-select=C901,PLR0911,PLR0912,PLR0913,PLR0915,S" \
    --compare-branch="${base}" --fail-under=100
