#!/usr/bin/env bash
# promotion_guard: same-source promotion guard for the knope release-PR flow
# (release-vision D10, migration M6; fastmcp-server-template#406).
#
# Invoked from BOTH knope workflows (the two-layer design in knope.toml):
# in prepare-release after the prep commit and before the push/PR steps —
# the early gate, so a drifted promotion never becomes a mergeable PR — and
# in tag-release BEFORE the Release (tag) step, where the ordering is
# load-bearing: tags are immutable, so a refusal must leave no tag behind.
# For a stable promotion of an rc series, verifies that the diff between
# the highest reachable rc tag for the target version and HEAD touches only
# the release stamps plus the release-notes pages.
#
# The allowed set (M6): the release-stamp files knope + stamp_manifests
# write, PLUS docs/releases/** — the notes PR for the release being promoted
# legitimately lands on trunk between rc and stable (notes are release
# metadata, exactly like the changelog section already in the set) — and the
# Vale vocabulary subtree, which notes PRs legitimately commit alongside the
# page (.vale/styles/config/vocabularies/Base/accept.txt; same M6 rationale).
# Everything else is unconditional: any other commit between the last rc and
# the promotion forces a new rc.  The plugin-manifest paths are listed
# unconditionally; on projects without the Claude plugin channel they simply
# never appear in a diff.
set -euo pipefail

ALLOWED=(
  "pyproject.toml"
  "uv.lock"
  "CHANGELOG.md"
  "server.json"
  ".claude-plugin/plugin/.claude-plugin/plugin.json"
  ".claude-plugin/plugin/.mcp.json"
)

version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
if [[ -z "$version" ]]; then
  echo "promotion_guard: could not read a version from pyproject.toml" >&2
  exit 1
fi

case "$version" in
  *-*)
    echo "promotion_guard: pre-release ${version} — guard not applicable, pass"
    exit 0
    ;;
esac

# Highest reachable rc of the target version.  --merged HEAD scopes the
# lookup to reachable tags: an rc cut on an unrelated branch must not gate
# this promotion.
last_rc="$(git tag --list "v${version}-rc.*" --merged HEAD | sort -V | tail -1)"
if [[ -z "$last_rc" ]]; then
  echo "promotion_guard: no reachable rc tags for ${version} — plain trunk release, pass"
  exit 0
fi

echo "promotion_guard: verifying stamps-only diff ${last_rc}..HEAD for stable ${version}"
# --no-renames: with rename detection on (git's default), a file MOVED into
# an allowed subtree would surface only under its destination path and slip
# the check below — the promotion would silently delete the source path.
# Without detection the diff reports the delete and the add separately, so
# the vanished source path is judged on its own.
mapfile -t changed < <(git diff --name-only --no-renames "${last_rc}" HEAD)

violations=()
for f in "${changed[@]}"; do
  ok=no
  for a in "${ALLOWED[@]}"; do
    [[ "$f" == "$a" ]] && ok=yes && break
  done
  # Release-notes pages (and their index) are release metadata (M6), and a
  # notes PR may add vocabulary entries alongside the page.
  [[ "$f" == docs/releases/* ]] && ok=yes
  [[ "$f" == .vale/styles/config/vocabularies/* ]] && ok=yes
  [[ "$ok" == no ]] && violations+=("$f")
done

if ((${#violations[@]} > 0)); then
  # Context-neutral wording: this guard fires at prepare time (no PR yet)
  # AND pre-tag (no tag yet) — see knope.toml's two-layer note.
  echo "promotion_guard: REFUSING the promotion to v${version}." >&2
  echo "promotion_guard: diff ${last_rc}..HEAD touches non-stamp files:" >&2
  printf 'promotion_guard:   %s\n' "${violations[@]}" >&2
  echo "promotion_guard: new source requires a new rc (release-vision D10)." >&2
  exit 1
fi

echo "promotion_guard: OK — ${#changed[@]} changed file(s), all in the allowed set"
