#!/usr/bin/env bash
# Merge a release branch back into the currently checked-out branch (normally
# the default branch) after a release was cut from it.
#
# Why this exists: after any release cut from a release/X.Y branch, the new
# tag is unreachable from main until the branch merges back.  PSR's
# already-released check is repo-global while its version computation is
# ancestry-scoped, so an unmerged branch release deadlocks main — PSR
# recomputes the same version from main's own history, finds the tag taken,
# and reports "already released" forever.  The release workflow's merge-back
# job runs this script; it is also safe to run by hand from a main checkout.
#
# Conflict policy: version-coupled files (pyproject.toml, the changelog, and
# every [tool.semantic_release] asset) resolve to the CURRENT branch's side —
# main is authoritative for current version metadata, and the release tag
# preserves the branch's own state.  Any other conflict means real divergence
# a human must look at: the merge is aborted, the tree left clean, and the
# script exits non-zero.
#
# Usage: merge_back.sh <release-ref> <tag>
#   release-ref  the branch to merge back, e.g. origin/release/4.0
#   tag          the tag just released from it (commit-message context only)
#
# Requires python3 >= 3.11 (tomllib) to read the asset list from
# pyproject.toml, so the file list can never drift from what PSR stages.
set -euo pipefail

release_ref="${1:?usage: merge_back.sh <release-ref> <tag>}"
tag="${2:?usage: merge_back.sh <release-ref> <tag>}"

if git merge-base --is-ancestor "$release_ref" HEAD; then
    echo "merge_back: ${release_ref} is already reachable from HEAD; nothing to do"
    exit 0
fi

coupled="$(python3 - <<'PY'
import tomllib

with open("pyproject.toml", "rb") as fh:
    cfg = tomllib.load(fh)["tool"]["semantic_release"]
files = {"pyproject.toml"}
changelog_cfg = cfg.get("changelog", {})
files.add(
    changelog_cfg.get("default_templates", {}).get("changelog_file")
    or changelog_cfg.get("changelog_file")  # pre-v9.11 deprecated location
    or "CHANGELOG.md"
)
files.update(str(asset) for asset in cfg.get("assets", []))
print("\n".join(sorted(files)))
PY
)"

if ! git merge --no-ff --no-commit "$release_ref"; then
    while IFS= read -r f; do
        if git ls-files --unmerged -- "$f" | grep -q .; then
            if git checkout --ours -- "$f" 2>/dev/null; then
                git add -- "$f"
            fi
        fi
    done <<<"$coupled"
    remaining="$(git diff --name-only --diff-filter=U)"
    if [ -n "$remaining" ]; then
        echo "merge_back: conflicts outside the version-coupled files need a human:" >&2
        printf '%s\n' "$remaining" >&2
        git merge --abort
        exit 1
    fi
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
git commit -m "chore(release): merge ${release_ref#origin/} back into ${current_branch} after ${tag}"
