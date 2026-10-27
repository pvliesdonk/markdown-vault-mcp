---
type: Reference
title: The SQLite each installation channel brings
description: Which SQLite library the Python that runs markdown-vault-mcp links against, for each way of installing it, and how uv picks that Python, measured against the WAL-reset bug's fixed releases.
subject_version: "Observed 2026-10-27: ghcr.io/pvliesdonk/markdown-vault-mcp latest (v5.1.0) and edge; uv 0.12.24 managed CPython 3.11.17–3.14.8; python.org installers to 3.14.10; Debian 12–14, Ubuntu 24.04 and 26.04, Fedora 43 and 44 system python3"
valid_for: "the channels markdown-vault-mcp 5.x ships (Docker image, PyPI with uvx or pip, the Claude plugin and Desktop bundle, the .deb and .rpm packages); a moving target, re-observe by date"
generated:
  by: process:researching-references
  at: 2026-10-27T12:54:35+01:00
stale_after: 2027-04-27T12:54:35+01:00
verified:
  - by: process:researching-references
    at: 2026-10-27T12:54:35+01:00
status: stable
sources:
  - id: wal-docs
    title: Write-Ahead Logging
    resource: https://sqlite.org/wal.html
    accessed: 2026-10-27
  - id: uv-python
    title: uv — Python versions
    resource: https://docs.astral.sh/uv/concepts/python-versions/
    accessed: 2026-10-27
  - id: cpython-changelog
    title: Python 3.14 changelog
    resource: https://docs.python.org/3.14/whatsnew/changelog.html
    accessed: 2026-10-27
  - id: debian-packages
    title: Debian package search, libsqlite3-0
    resource: https://packages.debian.org/search?keywords=libsqlite3-0&searchon=names&exact=1&suite=all&section=all
    accessed: 2026-10-27
  - id: debian-changelog
    title: sqlite3 3.46.1-7+deb13u2 changelog
    resource: https://metadata.ftp-master.debian.org/changelogs//main/s/sqlite3/sqlite3_3.46.1-7+deb13u2_changelog
    accessed: 2026-10-27
---

# The SQLite each installation channel brings

markdown-vault-mcp uses Python's standard `sqlite3` module, so the SQLite
library is whichever one the Python running the server links against. That
Python comes from the installation channel, not from this project's
dependencies. This page records, per channel, which SQLite that is. Each
version is measured against the WAL-reset bug's fixed releases (3.51.3, and
the 3.50.7 and 3.44.6 backports), whose conditions are on
[`sqlite-wal-durability.md`](sqlite-wal-durability.md).

## Scope

- **Covers:**
  - the Docker image;
  - PyPI through `uvx` or `pip`, which the Claude Code plugin and the Claude
    Desktop bundle also use;
  - the `.deb` and `.rpm` packages, which run on the system `python3`;
  - how uv picks the Python it runs;
  - when the index is on disk at all;
  - the network-filesystem requirement WAL places on the index's storage.
- **Does not cover:**
  - Homebrew, conda and pyenv builds;
  - Windows beyond the python.org installer;
  - distribution patches that fix the bug without changing the reported
    version, except Debian 13's, which was read.
- **Depended on by:**
  - `docs/use/storage.md` (the operator's channel table);
  - `src/markdown_vault_mcp/domain.py` (the `sqlite_runtime` startup line);
  - `docs/design/design.md` § the writer-lock paragraph.

## Claims

### What the server reports

- At startup the server logs INFO `sqlite_runtime version=<x.y.z>`, taken
  from `sqlite3.sqlite_version`: the library the running Python links
  against, so an operator can read it against the table below. It checks
  nothing. [pins: tests/test_service_startup.py::test_start_logs_the_sqlite_runtime_once]

### When the index is on disk

- With `MARKDOWN_VAULT_MCP_INDEX_PATH` unset the keyword index is in memory
  (`config.py`, "unset keeps the index in memory"), and the keyword index
  skips WAL for an in-memory database (`fts_index.py`), so neither the
  WAL-reset bug nor the filesystem requirement below applies. The Claude Code
  plugin (`.claude-plugin/plugin/.mcp.json`) and the Desktop bundle
  (`packaging/mcpb/manifest.json.in`) set no index path; the Docker compose
  file and the systemd guide do.

### Docker image

- The published `latest` image, v5.1.0, is built on `python:3.14-slim`
  (Debian 13; `git show v5.1.0:Dockerfile`). It runs Python 3.14.8 with
  SQLite 3.46.1, which is in the affected range. [observed: `docker run --rm --entrypoint python
  ghcr.io/pvliesdonk/markdown-vault-mcp:latest -I -c "import sys, sqlite3;
  print(sys.version, sqlite3.sqlite_version)"`]
- Debian 13's `libsqlite3-0` is `3.46.1-7+deb13u2`. Its trixie entries
  backport FTS5 CVE fixes and a build dependency, and no WAL-reset fix.
  [source: debian-packages] [source: debian-changelog] That the image's
  Python links this Debian library is inferred from the matching version,
  not read from the image's build.
- Since template v11.5.0 (#1922), the image is built on `debian:trixie-slim`
  with a uv-managed CPython. The `edge` image built on 2026-10-27 runs Python
  3.14.10 with SQLite 3.53.1, which is fixed. [observed: the same command on
  `:edge`] `edge` images built before 2026-10-27 are on `python:3.14-slim`
  like v5.1.0. A release built from the current Dockerfile gets the SQLite of
  the CPython uv downloads at its build. [unverified] No such release exists
  yet; its `sqlite_runtime` line will settle it.

### uv-managed CPython (PyPI through uvx, the plugin, the Desktop bundle)

- A freshly downloaded uv-managed CPython links SQLite 3.53.1 on every
  supported minor: 3.11.17, 3.12.15, 3.13.16 and 3.14.8, downloaded by uv
  0.12.24. [observed: `UV_PYTHON_INSTALL_DIR=<empty dir> uv python install
  3.11 3.12 3.13 3.14`, then `sqlite3.sqlite_version` in each]
- Older uv-managed builds link older SQLite. Builds installed earlier on the
  research machine (3.10.20, 3.11.14, 3.12.13, 3.13.11 and 3.14.5) link
  3.50.4, which is affected. [observed: `~/.local/share/uv/python/*/bin/python3`]
- uv uses a Python it already has before it downloads one. It checks
  managed installations first, then `python`, `python3` and `python3.x` on
  `PATH`; "system Python installations are still preferred over downloading
  a managed Python version". Among managed installations it prefers the
  newest. It downloads only "if a Python version cannot be found on the
  system". [source: uv-python] So `uvx` can run on an old managed build, or
  on the system `python3`, and then gets that build's SQLite.
- `uv python upgrade` moves managed installations to the newest patch
  release, keeps the old one, and "will prefer the new version" afterwards.
  [source: uv-python] The uv page does not say whether an existing `uvx`
  tool environment moves to the new patch with it. [unverified] Running
  `uvx` after an upgrade and reading the `sqlite_runtime` line would settle
  it.

### python.org installers

- The macOS and Windows installers have bundled SQLite 3.50.4 since 3.14.0
  rc2 (gh-137134). The changelog shows no later update up to 3.14.10, so
  these installers bundle an affected SQLite. [source: cpython-changelog]
- Android builds moved to SQLite 3.53.4 in 3.14.8 (gh-152023). That does not
  reach any channel this project ships. [source: cpython-changelog]

### Linux packages (`.deb`, `.rpm`)

- The packages create a virtual environment from the system `python3`
  (`packaging/scripts/postinstall.sh`), and `packaging/nfpm.yaml` requires
  `python3 (>= 3.11)`. The SQLite they get is the distribution's
  `libsqlite3`.
- The system `python3` and SQLite, by base image:

  | Base image | python3 | SQLite | WAL-reset fix |
  |---|---|---|---|
  | `debian:bookworm-slim` (12) | 3.11.2 | 3.40.1 | no |
  | `debian:trixie-slim` (13) | 3.13.5 | 3.46.1 | no |
  | `debian:forky-slim` (testing) | 3.14.8 | 3.53.4 | yes |
  | `ubuntu:24.04` | 3.12.3 | 3.45.1 | no, by version |
  | `ubuntu:26.04` | 3.14.4 | 3.46.1 | no, by version |
  | `fedora:43` | 3.14.8 | 3.50.2 | no, by version |
  | `fedora:44` | 3.14.8 | 3.51.2 | no, by version |

  [observed: `docker run --rm <image>`, installing `python3` where the image
  lacks it, then `python3 -I -c "import sys, sqlite3;
  print(sys.version, sqlite3.sqlite_version)"`]
- `ubuntu:22.04` (Python 3.10) and Rocky Linux 9 (Python 3.9) are below the
  package's `python3 >= 3.11` floor. [observed: same command]
- "No, by version" means the reported version is in the affected range.
  Only Debian 13's changelog was read for a backported fix. [unverified]
  Ubuntu and Fedora may patch the fix in without changing the version; their
  package changelogs would settle it.

### Storage the index needs

- WAL requires all processes using a database to be on one host: "WAL does
  not work over a network filesystem", because "WAL requires all processes
  to share a small amount of memory". (§1, §2.2) [source: wal-docs] The
  keyword index (`index.db`) and the SQLite vector store are WAL databases,
  so their paths must be on a local filesystem.

## Where this project departs from the subject

None. By the owner's decision (#1896), the project picks dependencies and
records what each channel brings. It does not pin, bundle, check or refuse
SQLite versions, and it does not detect network filesystems.

## Not covered

- Homebrew, conda and pyenv Pythons: each links its own SQLite. A user of
  those reads the `sqlite_runtime` line.
- Whether Claude Desktop runs the bundle with its own copy of uv or with the
  user's. [unverified] Its documentation would settle it; either way, uv's
  discovery above applies.
