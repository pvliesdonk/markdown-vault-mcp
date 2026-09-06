---
okf_version: "0.2"
title: External-behaviour references
description: OKF v0.2 bundle of dated, sourced references on how things outside this repository behave
---

# External-behaviour references

Each page records, with primary sources and dates, how something outside this
repository behaves where the code depends on it. Read the page for the module
you are about to touch; a page past its `stale_after` is re-researched with
the `researching-references` skill, never trusted. Trust tier and staleness
follow OKF v0.2 (`generated`, `verified`, `stale_after`).

- [Obsidian markdown dialect and link resolution](/obsidian-markdown.md): wikilinks, embeds, aliases, tags, the resolution tie-break question (#1350). Read before `scanner.py` wikilink extraction or `fts_index.py` resolution.
- [CommonMark and GitHub Flavored Markdown](/commonmark-gfm.md): line endings, paragraph boundaries, link and reference grammar, code spans and fences, the #1334 decision table. Read before any regex in `scanner.py`.
- [Git push, credentials and remotes](/git-push-and-remotes.md): push refusals and their wording, `--porcelain`, askpass and `GIT_TERMINAL_PROMPT`, URL forms, `safe.directory`, `symbolic-ref` and `origin/HEAD`. Read before `git/health.py`, `git/push_scheduler.py`, `git/_run.py`, `git/bootstrap.py`, or the repository-discovery part of `git/conflict.py`.
- [Git staging, commits and rebase state](/git-staging-and-commits.md): pathspec magic, `add`/`check-ignore`, `commit --only` and identity, ancestry and fast-forward, rebase state on disk and conflict markers. Read before `git/strategy.py`, `git/conflict.py`, or the pathspec part of `git/_run.py`.
- [Git history and revision queries](/git-history-queries.md): `log -z`/`--name-status` framing, `--follow` and rename detection, `--since`/`--until` date semantics, `ls-tree -l`, symlink blobs, LFS pointers. Read before `git/query.py`.
