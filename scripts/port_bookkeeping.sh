#!/usr/bin/env bash
# port_bookkeeping: after a STABLE release cut from a branch other than the
# default one, bring what the default branch still wants from it — as an
# ORDINARY pull request (fastmcp-server-template#588, #419).
#
# Invoked by release.yml's port-bookkeeping job from a full checkout of the
# default branch, with the release's identity in the environment (TAG,
# VERSION, MERGE_SHA, BASE, DEFAULT, REPO, GH_TOKEN).
#
# Two modes, decided by ONE comparison — is the released version the
# highest stable in the whole repository?  Repo-global, not reachable:
# with only reachable tags, a backport shipped while a newer stable's port
# PR is still open would read as the newest and be merged.  "In the
# repository" includes a higher stable whose release PR merged but whose
# tag has not appeared yet (its release job still running, or failed
# before tagging): two releases from different bases can overlap, and the
# older one tagging first must not read as the newest.
#
# * ancestry — the release IS the newest stable: the release commit is
#   merged (--no-ff, a real merge, never `-s ours`) so the default branch's
#   next prepare anchors on it.  knope computes the next version and the
#   changelog range from the first STABLE tag it meets walking the log in
#   reverse date order (rc tags never anchor; a descendant beats its
#   ancestor), so a copied section or ported stamps alone leave the branch
#   computing from the release before the cut — the #588 failure.  A real
#   merge also carries a fix that only ever landed on the branch; a
#   `-s ours` merge would record it as merged while dropping it, and every
#   later merge would trust that record.
# * files — an OLDER backport (a higher stable is already reachable): the
#   release's changelog section, notes page and index entry are copied in,
#   exactly as before.  Its ancestry must NOT be merged: dated after the
#   newer stable, the backport's tag would become knope's anchor and drag
#   the default branch's next version and range backwards.  Its stamps stay
#   on the branch too — the default branch's manifests already name a
#   newer stable.
#
# Conflicts in ancestry mode are resolved deterministically only for the
# files a release commit always touches (the promotion guard's allowed set
# plus the release's own notes page, the notes index and the staging
# file): take the default branch's side, then regenerate —
# the changelog section and the index entry are re-inserted, a conflicted
# notes page is replaced by the released copy (the files-mode contract),
# and the stamps are re-applied.  A page that merged cleanly is left as
# git merged it, so the default branch's own edits to it survive.  Any
# other conflict is a code conflict this job must not resolve: the merge is
# aborted and the port falls back to files mode, with the PR body naming
# the conflicting files and the branch-only commits, and Release Prepare's
# collision guard keeps refusing until the ancestry lands by hand.  A merge
# that fails for any other reason fails this job loudly.
#
# Needs python3 (3.11+, for scripts/stamp_manifests.py) on PATH — the
# ubuntu-latest runner's is enough; no setup-python step is required.
#
# Stables only: knope's stable section is cumulative over the whole rc
# cycle, so porting per rc would only manufacture pairwise conflicts and
# duplicate entries for content the stable's port carries anyway.
set -euo pipefail

: "${TAG:?}" "${VERSION:?}" "${MERGE_SHA:?}" "${BASE:?}" "${DEFAULT:?}" "${REPO:?}"

# The promotion guard's allowed set (scripts/promotion_guard.sh) — a test
# asserts the two lists match.  The plugin-manifest paths are listed
# unconditionally; on projects without the Claude plugin channel they
# simply never conflict.  The guard admits all of docs/releases/** and the
# Vale vocabulary subtree; this script deliberately admits only the three
# notes files it regenerates — nothing here could regenerate another
# minor's page or a dropped vocabulary entry, so a conflict there takes the
# files-only fallback, whose body names the file.
ALLOWED=(
  "pyproject.toml"
  "uv.lock"
  "CHANGELOG.md"
  "server.json"
  ".claude-plugin/plugin/.claude-plugin/plugin.json"
  ".claude-plugin/plugin/.mcp.json"
)

minor="${VERSION%.*}"
page="docs/releases/${minor}.md"
index="docs/releases/index.md"
staging="docs/releases/next.md"

regenerable() {
  local f="$1" a
  for a in "${ALLOWED[@]}"; do
    [[ "$f" == "$a" ]] && return 0
  done
  [[ "$f" == "$page" || "$f" == "$index" || "$f" == "$staging" ]] && return 0
  return 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git config user.name "github-actions"
git config user.email "actions@users.noreply.github.com"
git fetch -q origin "$MERGE_SHA"
git fetch -q --tags origin
if [[ "$(git rev-parse -q --verify "refs/tags/${TAG}^{commit}" || true)" != "$MERGE_SHA" ]]; then
  # The mode comparison below reads the tag list; a release whose tag is
  # missing or points elsewhere would silently take the files path with a
  # body claiming a newer stable exists.
  echo "::error::${TAG} is not a fetched tag pointing at ${MERGE_SHA} — the release job tags before this job runs; re-run it once the tag exists"
  exit 1
fi

# A release already reachable from the default branch (a re-run after its
# port merged as a merge commit) is fully there — files included — and
# nothing here may touch the branch's later edits.
if git merge-base --is-ancestor "$MERGE_SHA" HEAD; then
  echo "${TAG} is already reachable from ${DEFAULT} — nothing to port"
  exit 0
fi

# Highest STABLE tag in the repository (all tags were just fetched; the
# release job tagged this release before this job ran).  Stables only, so
# `sort -V` is safe here — it must never rank an rc against a stable
# (release.yml's rolling-channel note), and rc tags do not anchor knope.
highest="$(git tag --list 'v[0-9]*' \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)"

# A higher stable in flight: a merged `knope/prepare/*` release PR whose
# stamped stable version is above this one and carries no tag yet.  Its
# tag will point at its own merge commit, dated BEFORE this release's, so
# once both ancestries land the later-dated older release would be knope's
# anchor.  Read from the merge commits' committed pyproject.toml — never a
# PR title — and degrade to the tag comparison on an API hiccup (the same
# posture as release-prepare.yml's reservation check).
inflight=""
merged="$(gh pr list --state merged --limit 30 --json headRefName,mergeCommit \
  --jq '.[] | select(.headRefName | startswith("knope/prepare/")) | .mergeCommit.oid' \
  2>/dev/null || true)"
for sha in $merged; do
  git cat-file -e "${sha}^{commit}" 2>/dev/null || git fetch -q origin "$sha" 2>/dev/null || continue
  v="$(git show "${sha}:pyproject.toml" 2>/dev/null \
    | sed -n '/^version = "/{ s/^version = "\(.*\)"/\1/p; q; }' || true)"
  [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
  [[ "$v" == "$VERSION" ]] && continue
  git rev-parse -q --verify "refs/tags/v${v}^{commit}" > /dev/null && continue
  if [[ "$(printf '%s\n' "v${v}" "v${VERSION}" | sort -V | tail -1)" == "v${v}" ]]; then
    inflight="$v"
    break
  fi
done

mode=files
if [[ -n "$inflight" ]]; then
  echo "port_bookkeeping: a higher stable ${inflight} is in flight (its release PR merged, no tag yet)"
elif [[ "$highest" == "v${VERSION}" ]]; then
  mode=ancestry
fi
echo "port_bookkeeping: highest stable in the repository: ${highest:-none}; ${TAG} ports in ${mode} mode"

BRANCH="knope/port/${TAG}"
git switch --force-create "$BRANCH"
# --cherry-pick --right-only: a fix landed on the default branch first and
# cherry-picked to the release branch is the same patch on both sides and
# is not "branch-only".
branch_only="$(git log --oneline --no-merges --cherry-pick --right-only \
  "HEAD...${MERGE_SHA}" -- || true)"

fallback=""
regenerated=()
if [[ "$mode" == ancestry ]]; then
  # -X no-renames: with rename detection on (git's default), the release
  # commit's promotion of docs/releases/next.md into the canonical page
  # reads as a RENAME, and the default branch's rewritten next.md — the
  # notes for the NEXT series — would be merged into the released page
  # while the staging file silently vanished.  Without detection the
  # deletion surfaces as a modify/delete conflict, resolved below from the
  # default branch's side.
  if ! git merge --no-ff --no-commit -X no-renames "$MERGE_SHA" > "$tmp"/merge.log 2>&1; then
    mapfile -t conflicted < <(git -c core.quotePath=false diff --name-only --diff-filter=U)
    if ! git rev-parse -q --verify MERGE_HEAD > /dev/null || ((${#conflicted[@]} == 0)); then
      # Not a conflict: an unrelated history, a working-tree file the
      # merge would overwrite, a refused merge — nothing here can be
      # resolved, and continuing would commit a single-parent "port" whose
      # body claims an ancestry it does not carry.
      echo "::error::merging ${MERGE_SHA} into ${DEFAULT} failed without conflicts to resolve:"
      cat "$tmp"/merge.log
      exit 1
    fi
    code=()
    for f in "${conflicted[@]}"; do
      regenerable "$f" || code+=("$f")
    done
    if ((${#code[@]} > 0)); then
      git merge --abort
      mode=files
      fallback="$(printf '%s\n' "${code[@]}")"
      echo "::warning::${TAG}'s ancestry merge conflicts outside the release bookkeeping — falling back to a files-only port; merge ${MERGE_SHA} into ${DEFAULT} by hand (merge commit) to land the ancestry"
      printf 'port_bookkeeping:   %s\n' "${code[@]}"
    else
      for f in "${conflicted[@]}"; do
        # Stage 2 present: the default branch's side exists (modified on
        # both sides, or deleted by the release commit) — keep ours, the
        # regeneration below re-applies what the release contributes.
        # Absent: the default branch deleted it; the path is dropped from
        # the merge, and the regeneration below then re-creates only what
        # the release contributes (a notes page comes back as the released
        # copy, the files-mode contract).
        if git ls-files -u -- "$f" | awk '$3 == 2 { found = 1 } END { exit !found }'; then
          git checkout --ours -- "$f"
          git add -- "$f"
        else
          git rm -q -- "$f"
        fi
        regenerated+=("$f")
      done
      echo "port_bookkeeping: resolved ${#conflicted[@]} bookkeeping conflict(s) from ${DEFAULT}'s side; regenerating"
    fi
  fi
fi

# ---- Files: the changelog section, the notes page, the index entry ------
# Idempotent by construction (each half skips when the default branch
# already carries the content), so the ancestry mode runs them too: a
# clean merge already brought everything and they change nothing; a
# regenerated file gets the release's contribution re-applied.
git show "${MERGE_SHA}:CHANGELOG.md" > "$tmp"/released-changelog.md
# Escape the version's dots INSIDE awk (an escaped `-v` value would be
# mangled by awk's own escape processing), so the heading regex here and
# the dedup grep below match identically — neither treats `4.1.0` as also
# matching `4x1y0`.
awk -v ver="$VERSION" '
  BEGIN { gsub(/\./, "\\.", ver); re = "^## v?" ver "([ (]|$)" }
  $0 ~ re { found=1; print; next }
  found && /^## / { exit }
  found { print }
' "$tmp"/released-changelog.md > "$tmp"/section.md
if ! [[ -s "$tmp"/section.md ]]; then
  echo "::error::could not find the ## ${VERSION} section in the released CHANGELOG.md — port the changelog section to ${DEFAULT} by hand (ordinary PR)"
  exit 1
fi
if grep -qE "^## v?${VERSION//./\\.}([ (]|\$)" CHANGELOG.md; then
  echo "${DEFAULT}'s CHANGELOG.md already carries the ${VERSION} section — skipping the changelog half"
else
  # Insert directly below the insertion flag: newest-inserted-first matches
  # release chronology (a backport releases after the newer stables above
  # it were inserted), and knope inserts its next section under the same
  # flag, above whatever was ported.
  if ! grep -qF '<!-- version list -->' CHANGELOG.md; then
    echo "::error::${DEFAULT}'s CHANGELOG.md has no '<!-- version list -->' flag line — port the ${VERSION} section by hand (ordinary PR)"
    exit 1
  fi
  awk -v sec_file="$tmp/section.md" 'BEGIN { while ((getline line < sec_file) > 0) sec = sec line "\n" }
       { print }
       $0 == "<!-- version list -->" { printf "\n%s", sec }' \
    CHANGELOG.md > CHANGELOG.md.new
  mv CHANGELOG.md.new CHANGELOG.md
fi
# The notes page follows the changelog's port path (template#419): a
# branch-cut stable's page section landed on the release branch inside its
# release PR, and the default branch — where the pages for all releases
# accumulate — needs it too.  In files mode, or when the page conflicted
# and was taken from ours, the released page is copied wholesale; if the
# default branch's copy moved since the cut, that divergence surfaces as
# this PR's reviewable diff and is resolved by hand there, same contract
# as the changelog.  A page the ancestry merge brought in cleanly is left
# as git merged it: the default branch's own edits to it are kept.
page_from_release=no
if [[ "$mode" == files ]] || [[ ! -f "$page" ]]; then
  page_from_release=yes
else
  for f in ${regenerated[@]+"${regenerated[@]}"}; do
    [[ "$f" == "$page" ]] && page_from_release=yes
  done
fi
if [[ "$page_from_release" == yes ]] \
  && git show "${MERGE_SHA}:${page}" > "$tmp"/released-page.md 2>/dev/null; then
  if [[ -f "$page" ]] && cmp -s "$tmp"/released-page.md "$page"; then
    echo "${DEFAULT}'s ${page} already matches the released page — skipping the notes half"
  else
    mkdir -p docs/releases
    cp "$tmp"/released-page.md "$page"
  fi
fi
# The index entry ports by INSERTION, never wholesale copy: a backport
# branch's index predates newer minors listed on the default branch, so
# copying it would delete their entries.  When the default branch's index
# lacks this minor's entry (the first-stable-of-a-minor branch cut), the
# released index's entry lines — the `(minor.md)` line through the entry's
# continuation lines — are inserted right after the RELEASE-PAGES-START
# sentinel, newest-first like the changelog insertion.
if [[ -f "$index" ]] && ! grep -qF "(${minor}.md)" "$index" \
  && git show "${MERGE_SHA}:${index}" > "$tmp"/released-index.md 2>/dev/null \
  && grep -qF "(${minor}.md)" "$tmp"/released-index.md; then
  awk -v ref="(${minor}.md)" '
    index($0, ref) { f=1; print; next }
    f && (/^- / || /RELEASE-PAGES-END/) { exit }
    f { print }
  ' "$tmp"/released-index.md > "$tmp"/index-entry.md
  awk -v entry_file="$tmp/index-entry.md" 'BEGIN { while ((getline l < entry_file) > 0) e = e l "\n" }
       { print }
       /RELEASE-PAGES-START/ { start=1 }
       start && !done && /-->[[:space:]]*$/ { printf "%s", e; done=1 }' \
    "$index" > "${index}.new"
  mv "${index}.new" "$index"
fi

# ---- Stamps: ancestry mode only -----------------------------------------
# The release is the newest stable, so the default branch's stamps move to
# it: pyproject.toml's version line (knope's versioned file) plus, via the
# same script every stable release PR runs, uv.lock's self entry and the
# install-channel manifests.  Idempotent when the merge brought them in
# clean; load-bearing when a stamp file was regenerated from ours.
#
# One exception: a default branch already mid-rc on a HIGHER series
# (pyproject.toml at X.(Y+1).0-rc.N while X.Y.Z ships from its branch)
# keeps its own pyproject.toml and uv.lock — moving them back would reset
# knope's pre-release counter and recompute an rc that is already tagged.
# The install-channel manifests still move: they name the newest stable,
# and that is now this release.
if [[ "$mode" == ancestry ]]; then
  current="$(sed -n '/^version = "/{ s/^version = "\(.*\)"/\1/p; q; }' pyproject.toml)"
  if [[ -z "$current" ]]; then
    echo "::error::no version line in ${DEFAULT}'s pyproject.toml — nothing to stamp"
    exit 1
  fi
  keep_own=no
  if [[ "$current" == *-* ]] && [[ "${current%%-*}" != "$VERSION" ]] \
    && [[ "$(printf '%s\n' "${current%%-*}" "$VERSION" | sort -V | tail -1)" == "${current%%-*}" ]]; then
    keep_own=yes
    echo "port_bookkeeping: ${DEFAULT} is mid-rc on ${current} (a higher series) — its pyproject.toml and uv.lock stay; manifests move to ${VERSION}"
    cp uv.lock "$tmp"/uv.lock.keep
  else
    # First `version = "..."` line only — knope's [project] entry; a later
    # table's version key is not this script's to touch.
    sed -i "0,/^version = \".*\"/s//version = \"${VERSION}\"/" pyproject.toml
  fi
  python3 scripts/stamp_manifests.py "$VERSION"
  if [[ "$keep_own" == yes ]]; then
    cp "$tmp"/uv.lock.keep uv.lock
  fi
  git add -- pyproject.toml uv.lock
fi

git add -- CHANGELOG.md
[[ ! -d docs/releases ]] || git add -- docs/releases
if [[ "$mode" == files ]]; then
  # status --porcelain, not diff: a newly created page is untracked and
  # would be invisible to git diff.
  if ! git status --porcelain -- CHANGELOG.md docs/releases | grep -q .; then
    if [[ -n "$fallback" ]]; then
      # Nothing to carry, so no PR to carry the conflict report in — fail
      # the job instead, loudly: the ancestry still has to land by hand.
      echo "::error::${DEFAULT} already carries ${TAG}'s bookkeeping, but its ancestry merge conflicts outside the release bookkeeping — merge ${MERGE_SHA} into ${DEFAULT} by hand (merge commit) before the next Release Prepare there. Conflicting files:"
      printf 'port_bookkeeping:   %s\n' "${code[@]}"
      exit 1
    fi
    echo "${DEFAULT} already carries the changelog section and the notes pages — nothing to port"
    exit 0
  fi
fi
git commit -q -m "chore: port ${TAG} release bookkeeping to ${DEFAULT}"
git push --force origin "HEAD:${BRANCH}"

release_url="https://github.com/${REPO}/releases/tag/${TAG}"
if [[ "$mode" == ancestry ]]; then
  body="Ports the ${TAG} release from \`${BASE}\` to \`${DEFAULT}\` after the branch release ([release](${release_url})): a merge of the release commit, so \`${DEFAULT}\`'s next Release Prepare computes its version and changelog range from ${TAG}, plus the version stamps, which now name the newest stable.

**Merge this PR with a merge commit.** A squash or rebase discards the ancestry this port exists to carry; Release Prepare on \`${DEFAULT}\` refuses the next release until it lands.

Ordinary PR otherwise: review and merge on its own clock. Any fix that only ever landed on \`${BASE}\` arrives here as content — review it like any other change."
else
  body="Ports the ${TAG} changelog section and release-notes page from \`${BASE}\` to \`${DEFAULT}\` after the branch release ([release](${release_url})). Ordinary PR: review and merge on its own clock; divergence (if ${DEFAULT}'s copy moved since the cut) shows as this PR's diff and is resolved by hand."
  if [[ -n "$fallback" ]]; then
    body="${body}

**The ancestry merge of ${TAG} conflicted outside the release bookkeeping**, so this port carries files only. Land the ancestry by hand — \`git merge --no-ff ${MERGE_SHA}\` on a branch from \`${DEFAULT}\`, resolve, open a PR and merge it with a merge commit — before the next Release Prepare on \`${DEFAULT}\`, which refuses until then. Conflicting files:

$(printf -- '- %s\n' "${code[@]}")

Commits on \`${BASE}\` that never reached \`${DEFAULT}\`:

$(printf '%s\n' "$branch_only" | sed 's/^/- /')"
  elif [[ -n "$inflight" ]]; then
    body="${body} A higher stable (${inflight}) is in flight — its release PR merged before this one tagged — so the ancestry and the version stamps of ${TAG} stay on \`${BASE}\`, where they describe its own series."
  else
    body="${body} \`${DEFAULT}\` already carries a newer stable, so the ancestry and the version stamps of ${TAG} stay on \`${BASE}\`, where they describe its own series."
  fi
fi

# Skip cleanly when a port PR is already open (the force-push just
# refreshed it); otherwise create WITHOUT masking failures — a 403 or API
# error must fail this job loudly, per its contract.
existing="$(gh pr list --head "$BRANCH" --base "$DEFAULT" --state open --json number --jq 'length')"
if [[ "$existing" != "0" ]]; then
  echo "port PR for ${BRANCH} already open — the force-push refreshed it"
  exit 0
fi
gh pr create --base "$DEFAULT" --head "$BRANCH" \
  --title "chore: port ${TAG} release bookkeeping to ${DEFAULT}" \
  --body "$body"
