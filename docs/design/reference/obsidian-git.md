---
type: Reference
title: obsidian-git and isomorphic-git in a shared vault
description: "How the obsidian-git plugin commits, pulls and merges on desktop and on mobile (isomorphic-git), what its default commit subject and identity are, and what Obsidian itself writes as frontmatter on a new note — the premises of the OKF ownership design"
subject_version: "obsidian-git `master` as fetched 2026-09-08 (release version not read); isomorphic-git documentation 1.x; Obsidian help pages as published 2026-09-08"
valid_for: "obsidian-git while its desktop manager is simple-git and its mobile manager isomorphic-git; isomorphic-git 1.x"
generated:
  by: process:researching-references
  at: 2026-09-08T21:27:28+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-08T21:27:28+02:00
stale_after: 2027-03-08
status: stable
sources:
  - id: og-readme
    title: obsidian-git README (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/README.md
    accessed: 2026-09-08
  - id: og-constants
    title: obsidian-git src/constants.ts, DEFAULT_SETTINGS (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/constants.ts
    accessed: 2026-09-08
  - id: og-simplegit
    title: obsidian-git src/gitManager/simpleGit.ts, the desktop manager (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/gitManager/simpleGit.ts
    accessed: 2026-09-08
  - id: og-isogit
    title: obsidian-git src/gitManager/isomorphicGit.ts, the mobile manager (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/gitManager/isomorphicGit.ts
    accessed: 2026-09-08
  - id: ig-merge
    title: isomorphic-git, merge
    resource: https://isomorphic-git.org/docs/en/merge
    accessed: 2026-09-08
  - id: ig-pull
    title: isomorphic-git, pull
    resource: https://isomorphic-git.org/docs/en/pull
    accessed: 2026-09-08
  - id: obs-properties
    title: Properties - Obsidian Help
    resource: https://obsidian.md/help/properties
    accessed: 2026-09-08
  - id: obs-templates
    title: Templates - Obsidian Help
    resource: https://obsidian.md/help/plugins/templates
    accessed: 2026-09-08
---

# obsidian-git and isomorphic-git in a shared vault

The most common second author of a git-synced vault served by this
project is Obsidian with the community plugin obsidian-git. This page
records what that plugin does to the repository — the subject it puts on
its commits, where its identity comes from, how it pulls and what it does
on a conflict, on desktop (which shells out to the system git through
simple-git) and on mobile (a pure-JavaScript git, isomorphic-git) — and
what Obsidian itself writes as frontmatter on a new note. It leaves out
the plugin's UI, its authentication set-up and its history views. No code
depends on it yet; the OKF ownership design does.

## Scope

- Covers: default commit subject and its placeholder; commit identity;
  `syncMethod` and `mergeStrategy` defaults and what each does; conflict
  behaviour on both platforms; isomorphic-git's merge and pull limits;
  whether Obsidian adds frontmatter to a new note.
- Does not cover: authentication (SSH, PAT), the docs site's full
  placeholder list, submodules, line-author and history views, the
  plugin's release versioning.
- Depended on by: `docs/design/okf-ownership.md` §5.1 (commit identity as
  a discriminator), §6.2 (opaque subjects, entry dating), §8 (untyped
  notes), §11.

## Claims

### Commit subject

- The default commit subject for both manual and automatic commits is
  `vault backup: {{date}}` (`commitMessage` and `autoCommitMessage` in
  `DEFAULT_SETTINGS`). [source: og-constants]
- `{{date}}` renders with `commitDateFormat`, whose default is
  `DATE_TIME_FORMAT_SECONDS` = `YYYY-MM-DD HH:mm:ss`, so a default subject
  reads `vault backup: 2026-09-08 07:12:03`. [source: og-constants]
- `{{date}}` is the only placeholder that appears in `constants.ts`.
  [source: og-constants] Whether the docs site lists further placeholders
  (`{{hostname}}`, `{{numFiles}}`, `{{files}}`) is [unverified]; the raw
  README carries no placeholder section. Reading the plugin's settings
  tab source would settle it.
- `listChangedFilesInMessageBody` defaults to `false`; `commitMessageScript`
  defaults to the empty string. [source: og-constants] What the body
  contains when the first is on is [unverified] beyond the setting's name.
- Automatic commits are off by default (`autoSaveInterval: 0`,
  `autoPullInterval: 0`, `autoPushInterval: 0`, `autoPullOnBoot: false`).
  [source: og-constants]

### Commit identity

- On desktop the plugin commits through simple-git as
  `git.commit(<formatted message>)` with no `--author` option, so the
  author and committer are whatever the user's git configuration
  resolves, exactly as a command-line commit would. [source: og-simplegit]
- On mobile the plugin reads `user.name` and `user.email` from the
  repository's git config and throws `Git author name and email are not
  set.` when either is missing; there is no fallback identity.
  [source: og-isogit]
- Consequence for a discriminator built on committer identity: an
  obsidian-git commit carries the human's configured identity on both
  platforms, never a plugin identity, so a plugin commit is
  distinguishable from this server's only when the two configured
  identities differ. [source: og-simplegit] [source: og-isogit]

### Sync method and merge strategy

- `syncMethod` defaults to `"merge"`; the other values are `"rebase"` and
  `"reset"`. `mergeStrategy` defaults to `"none"`. `pullBeforePush`
  defaults to `true`; `squashCommitsBeforePush` to `false`.
  [source: og-constants]
- On desktop, `merge` runs `git merge <tracking branch>` (with
  `--strategy-option` when `mergeStrategy` is set), `rebase` runs
  `git rebase` with the same arguments, and `reset` runs
  `git update-ref refs/heads/<branch> <upstream>` followed by `git reset`.
  [source: og-simplegit]
- Because the desktop manager drives the system git, merge attributes in
  `.gitattributes` (for example `merge=union`) apply as git applies them.
  [unverified] Inferred from the mechanism, not exercised; a two-clone
  fixture with the plugin on one side would settle it.
- The README lists, for mobile, "No rebase merge strategy" among the
  feature limitations, beside "No SSH authentication", "Limited repo size,
  because of memory restrictions" and "No submodules support", and calls
  the mobile implementation "very unstable". [source: og-readme]

### Conflicts

- On desktop a failing merge or rebase is caught as an exception and
  displayed to the user; the plugin does not resolve or classify it.
  [source: og-simplegit]
- On mobile the plugin calls isomorphic-git's `merge` with
  `abortOnConflict: false`, and supplies a `mergeDriver` callback only when
  `mergeStrategy` is not `"none"` (the callback runs `diff3Merge` and picks
  ours or theirs); a `MergeConflictError` is passed to the plugin's own
  conflict handler with the conflicting file paths. [source: og-isogit]
- isomorphic-git's `merge`: "By default, if isomorphic-git encounters a
  merge conflict it cannot resolve using the builtin diff3 algorithm or
  provided merge driver, it will abort and throw a
  `MergeNotSupportedError`"; with `abortOnConflict: false` the conflicting
  files are written to the working directory with conflict markers. The
  documentation names only the `mergeDriver` callback for custom
  resolution and does not mention `.gitattributes`; it "will fail if
  multiple candidate merge bases are found" (no recursive strategy) and
  "does not support selecting alternative merge strategies".
  [source: ig-merge]
- isomorphic-git's `pull` offers `fastForward` ("If false, only create
  merge commits") and `fastForwardOnly` and no rebase. [source: ig-pull]
- Consequence: guidance that relies on a `.gitattributes` merge driver
  reaches obsidian-git on desktop at most, and never on mobile.
  [source: ig-merge] [source: og-isogit]

### What Obsidian writes on a new note

- The help page on properties describes adding properties to a note
  through the "Add file property" command, the hotkey, the note's More
  actions menu, typing `---` at the top of the file, or inserting a
  template; it describes no property that a new note receives
  automatically. [source: obs-properties] That a new note is created with
  no frontmatter is the negative reading of that page and is [unverified]
  as an observation; creating a note in a fresh vault and reading the file
  would settle it.
- Default property names are `tags`, `aliases` and `cssclasses`.
  [source: obs-properties]
- Nested properties are listed under "Not supported": "To view nested
  properties, we recommend using the source mode." The OKF shapes
  `generated: {by, at}` and `verified: [{by, at}]` are nested.
  [source: obs-properties]
- The core Templates plugin inserts a template on command ("Insert
  template") at the cursor, not on note creation; its variables are
  `{{title}}`, `{{date}}` and `{{time}}`, the last two with an optional
  Moment.js format. A template may begin with a properties block, which
  Obsidian merges with the note's existing properties.
  [source: obs-templates]

## Where this project departs from the subject

None: nothing in the project drives or emulates the plugin. The design
that consumes these claims (`docs/design/okf-ownership.md`) treats the
plugin's default subject as opaque and a plugin commit as external; those
are readings, not departures.

## Not covered

- Whether obsidian-git on desktop honours `.gitattributes` merge drivers
  in practice (a two-clone fixture would settle it).
- The docs site's list of commit-message placeholders.
- The plugin's release version at the time of reading; the claims are
  against `master`.
- The desktop manager's fetch step and whether `pull` is a single
  `git pull` or `fetch` followed by the chosen sync method.
