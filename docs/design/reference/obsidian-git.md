---
type: Reference
title: obsidian-git and isomorphic-git in a shared vault
description: "How the obsidian-git plugin commits, pulls, merges and reports conflicts on desktop (system git through simple-git) and on mobile (isomorphic-git); its default subject, placeholders and identity; the files it writes into the vault; and what Obsidian itself puts in a new note's frontmatter"
subject_version: "obsidian-git master bac5123f (2026-09-08; latest release 2.39.0, 2026-08-12); isomorphic-git documentation 1.x; Obsidian help pages as published 2026-09-08"
valid_for: "obsidian-git 2.x while its desktop manager is simple-git and its mobile manager isomorphic-git; isomorphic-git 1.x"
generated:
  by: process:researching-references
  at: 2026-09-09T08:16:06+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-09T08:31:00+02:00
stale_after: 2027-03-09
status: stable
sources:
  - id: og-readme
    title: obsidian-git README (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/README.md
    accessed: 2026-09-08
  - id: og-getting-started
    title: obsidian-git docs/Getting Started.md (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/docs/Getting%20Started.md
    accessed: 2026-09-09
  - id: og-features
    title: obsidian-git docs/Features.md (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/docs/Features.md
    accessed: 2026-09-09
  - id: og-constants
    title: obsidian-git src/constants.ts (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/constants.ts
    accessed: 2026-09-09
  - id: og-settings
    title: obsidian-git src/setting/settings.ts (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/setting/settings.ts
    accessed: 2026-09-09
  - id: og-localstorage
    title: obsidian-git src/setting/localStorageSettings.ts (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/setting/localStorageSettings.ts
    accessed: 2026-09-09
  - id: og-main
    title: obsidian-git src/main.ts (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/main.ts
    accessed: 2026-09-09
  - id: og-gitmanager
    title: obsidian-git src/gitManager/gitManager.ts, the shared base (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/gitManager/gitManager.ts
    accessed: 2026-09-09
  - id: og-simplegit
    title: obsidian-git src/gitManager/simpleGit.ts, the desktop manager (master)
    resource: https://raw.githubusercontent.com/Vinzent03/obsidian-git/master/src/gitManager/simpleGit.ts
    accessed: 2026-09-09
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
records what that plugin does to the repository and to the vault: the
subject and identity it puts on commits, when it commits, how it pulls
and what it does on a conflict, on desktop (the system git through
simple-git) and on mobile (isomorphic-git, a JavaScript git), the files
it writes into the vault, and what Obsidian itself puts in a new note's
frontmatter. It leaves out authentication set-up, the history and
line-author views, and submodules.

## Scope

- Covers: commit subject defaults and placeholders; commit identity and
  where it is stored; automatic commit, pull and push; `syncMethod` and
  `mergeStrategy`; conflict behaviour on both platforms and the conflict
  note the plugin writes into the vault; isomorphic-git's merge and pull
  limits; `.gitattributes` on desktop; repository layout settings; what
  Obsidian writes on a new note.
- Does not cover: SSH, PAT and credential-manager set-up; the plugin's
  published docs site (`publish.obsidian.md/git`, which serves no static
  text to a fetch); the mobile first-run notice text; the
  `commitMessageScript` setting's semantics.
- Depended on by: the pull path in `git/strategy.py` and the resolver in
  `git/conflict.py` (their assumptions about the other side, recorded in
  #229 and #231: the plugin commits often and independently, and a
  same-file edit on both sides conflicts on rebase); `okf.py`'s
  `build_log_markdown`, which renders commit subjects; the guide
  `docs/guides/obsidian-everywhere.md` §Limitations; the OKF ownership
  design work under epic #1425.

## Claims

### Commit subject

- The default commit subject for both manual and automatic commits is
  `vault backup: {{date}}` (`commitMessage` and `autoCommitMessage` in
  `DEFAULT_SETTINGS`). [source: og-constants]
- `{{date}}` renders with `commitDateFormat`, whose default
  `DATE_TIME_FORMAT_SECONDS` is `` `${DATE_FORMAT} HH:mm:ss` `` with
  `DATE_FORMAT = "YYYY-MM-DD"`, so a default subject reads
  `vault backup: 2026-09-08 07:12:03`. [source: og-constants]
- The settings tab documents four placeholders for both message
  templates: "Available placeholders: {{date}} (see below), {{hostname}}
  (see below), {{numFiles}} (number of changed files in the commit) and
  {{files}} (changed files in commit message)." [source: og-settings]
- `{{numFiles}}` is the count of staged files; `{{hostname}}` comes from
  the plugin's local storage or, on desktop, the OS hostname; `{{files}}`
  lists the staged files grouped by action, and above one hundred files
  is replaced by "Too many files to list". [source: og-gitmanager]
- With `listChangedFilesInMessageBody` (default `false`; "List filenames
  affected by commit in the commit body") the message body gains
  "Affected files:" followed by the list. [source: og-constants]
  [source: og-gitmanager] [source: og-settings]
- The date placeholder uses Moment.js formats: "The plugin uses momentjs
  for formatting the date". [source: og-features]
- `commitMessageScript` defaults to the empty string. [source: og-constants]
  What a non-empty script does is [unverified]; reading `gitManager.ts`
  around `formatCommitMessage` for the script branch would settle it.

### Commit identity

- The plugin's "Commit author" settings read and write `user.name` and
  `user.email` through the git manager's `getConfig` / `setConfig`, that
  is, into the repository's git configuration, on every platform, once
  the repository is ready. [source: og-settings]
- The "Authentication/commit author" section stores only a username and
  a password or token, in the plugin's local storage, and only for the
  isomorphic-git manager; local storage holds no author name or email.
  [source: og-settings] [source: og-localstorage]
- On desktop the plugin commits through simple-git as
  `git.commit(<formatted message>)` with no `--author` option, so author
  and committer are whatever the repository's or the user's git
  configuration resolves, exactly as a command-line commit would.
  [source: og-simplegit]
- On mobile the manager reads `user.name` and `user.email` from the
  repository configuration and throws `Git author name and email are not
  set.` when either is missing; there is no fallback identity.
  [source: og-isogit]
- Consequence: an obsidian-git commit carries the human's configured
  identity on both platforms and never a plugin identity. A server that
  classifies commits by committer can tell a plugin commit from its own
  only while the two configured identities differ. [source: og-simplegit]
  [source: og-isogit]

### When it commits

- Automatic operation is off by default: `autoSaveInterval`,
  `autoPullInterval` and `autoPushInterval` are `0`, `autoPullOnBoot` and
  `autoBackupAfterFileChange` are `false`. [source: og-constants] The
  settings tab describes the interval as committing (and, unless commit
  and push have separate intervals, syncing) "changes every X minutes.
  Set to 0 (default) to disable." [source: og-settings]
- With `autoBackupAfterFileChange` on, a debounced commit-and-sync runs
  after vault modify, delete, create and rename events: "This waits X
  minutes after your latest change for the commit-and-sync."
  [source: og-main] [source: og-features]
- A commit reads the repository status and reports "No changes to
  commit" when nothing was committed, so an interval with no changed
  bytes produces no commit. [source: og-main] This contradicts
  #229's premise that the plugin "commits aggressively — even on
  mtime-only changes": git status does not report an mtime-only change
  as modified, and the plugin commits nothing when status is empty.
- The commit-and-sync command orders its steps by `syncMethod`: with
  `reset`, pull (when `pullBeforePush`) then commit; otherwise commit
  then pull (when `pullBeforePush`); in both cases a push follows unless
  `disablePush` is set, when a remote is configured. [source: og-main]
  `pullBeforePush` defaults to `true` ("Pull on commit-and-sync: On
  commit-and-sync, pull commits as well."). [source: og-constants]
  [source: og-settings]

### Sync method and merge strategy

- `syncMethod` defaults to `"merge"`; the settings tab offers "Merge",
  "Rebase" and "Other sync service (Only updates the HEAD without
  touching the working directory)" (`reset`). `mergeStrategy` defaults to
  `"none"`; its options are "None (git default)", "Our changes" and
  "Their changes". [source: og-constants] [source: og-settings]
- On desktop, `pull` runs `git fetch`, compares the local commit with
  `rev-parse` of the tracking branch, and when they differ runs
  `git merge <tracking>` or `git rebase <tracking>`, with a
  `--strategy-option` argument carrying the configured strategy when it
  is not `none`; `reset` runs `git update-ref refs/heads/<branch> <upstream>`
  followed by `git reset`. [source: og-simplegit]
- The desktop manager is simple-git over the system git: `baseDir` is
  the vault path joined with the "Custom base path (Git repository
  path)" setting, and a "Custom Git directory path (Instead of '.git')"
  sets `GIT_DIR` with `GIT_WORK_TREE` at the base path; the repository root is resolved with
  `git rev-parse --show-cdup`. [source: og-simplegit] [source: og-settings]
  Consequence: a vault may be a subdirectory of its repository, or use a
  git directory outside `.git`, on the plugin side as well as on this
  server's.
- Because the desktop merge is the system git's, merge attributes apply
  as git applies them. [observed: two clones of a bare repository on git
  2.55.0, `.gitattributes` with `log.md merge=union`, both sides adding a
  bullet to the same `## 2026-09-08` section, then `git pull --no-rebase`
  on one side: auto-merged with both bullets and no conflict.] The
  plugin's merge call passes only the tracking ref and the optional
  strategy option, nothing that would disable attributes.
  [source: og-simplegit]
- The README lists, for mobile, "No rebase merge strategy" among the
  feature limitations, beside "No SSH authentication", "Limited repo
  size, because of memory restrictions" and "No submodules support"; both
  it and the Getting Started page say "The Git implementation on mobile
  is very unstable!". [source: og-readme] [source: og-getting-started]
  The Getting Started page adds why: "you cannot use native Git on
  Android or iOS", and "Depending on your device and available free RAM,
  Obsidian may crash on clone/pull, create buffer overflow errors, or
  run indefinitely." [source: og-getting-started]

### Conflicts

- On desktop a failing merge or rebase is caught as an exception and
  displayed to the user; the manager itself neither resolves nor
  classifies it. [source: og-simplegit]
- After a pull, when the status reports conflicted paths, the plugin
  writes a note named `conflict-files-obsidian-git.md` into the vault,
  beginning "# Conflicts", "Please resolve them and commit them using the
  commands `Git: Commit all changes` followed by `Git: Push`", "(This
  file will automatically be deleted before commit)", followed by links
  to the conflicted files, and opens it. [source: og-main]
  [source: og-constants] Consequence for this server: that note is an
  ordinary non-reserved markdown file until the plugin deletes it, so it
  is indexed, searchable and listed in a generated `index.md` while it
  exists.
- On mobile the manager calls isomorphic-git's `merge` with
  `abortOnConflict: false`, supplies a `mergeDriver` callback only when
  `mergeStrategy` is not `"none"` (the callback runs `diff3Merge` and
  picks ours or theirs), and passes a `MergeConflictError` to the same
  conflict handler with the conflicting paths. [source: og-isogit]
- isomorphic-git's `merge`: "By default, if isomorphic-git encounters a
  merge conflict it cannot resolve using the builtin diff3 algorithm or
  provided merge driver, it will abort and throw a
  `MergeNotSupportedError`"; with `abortOnConflict: false` the
  conflicting files are written to the working directory with conflict
  markers. The documentation names only the `mergeDriver` callback for
  custom resolution and does not mention `.gitattributes`; it "will fail
  if multiple candidate merge bases are found" and "does not support
  selecting alternative merge strategies". [source: ig-merge]
- isomorphic-git's `pull` offers `fastForward` ("If false, only create
  merge commits") and `fastForwardOnly`, and no rebase. [source: ig-pull]
- Consequence: guidance that relies on a `.gitattributes` merge driver
  reaches obsidian-git on desktop and never on mobile. [source: ig-merge]
  [source: og-isogit]

### What Obsidian writes on a new note

- The help page on properties describes adding properties through the
  "Add file property" command, its hotkey, the note's More actions menu,
  typing `---` at the top of the file, or inserting a template, and
  describes no property that a new note receives automatically.
  [source: obs-properties] That a new note is created with no frontmatter
  is the negative reading of that page and is [unverified] as an
  observation; creating a note in a fresh vault and reading the file
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
  Moment.js format. [source: obs-templates] A template may carry a
  properties block: "When you insert a template into the active note,
  all the properties from the template will be added to the note.
  Obsidian will also merge any properties that exist in your note with
  properties in the template." [source: obs-properties]

## Where this project departs from the subject

None: nothing in the project drives or emulates the plugin. The server's
pull path rebases where the plugin's default merges, and its resolver
saves a sibling where the plugin writes a conflict note; those are two
tools' policies on one repository, not a departure from the plugin's
contract. The guide's description of the divergence case
(`docs/guides/obsidian-everywhere.md`, "Limitations & troubleshooting")
is consistent with the claims above.

## Not covered

- The plugin's published documentation site, which could not be fetched
  as text; the in-repository `docs/` pages were read instead.
- The mobile first-run notice text.
- What a non-empty `commitMessageScript` does.
- Whether desktop obsidian-git ever passes options that suppress
  `.gitattributes` in a rebase (`syncMethod: "rebase"`); only the merge
  path was observed.
