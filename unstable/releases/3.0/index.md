# 3.0

Released June 17, 2026.

Reconstructed after the fact

This page is a best-effort backfill written in August 2026, not a contemporaneous release note. It was reconstructed from the commit range, the pull requests in it, and the issues those pull requests closed ([#1058](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1058)). Every claim below links its evidence, but the page is weaker evidence than a note written at release time. Treat the upgrade section as a starting point for your own check, not as an exhaustive audit.

3.0 is the release where the vault stopped being a single-threaded library with a server bolted on. Startup no longer blocks on building the index, search returns files rather than chunks, and git synchronisation became something you can trigger and observe instead of only wait for. The Python class you import is now `Vault`, not `Collection`, which is the change that earns the major version. 261 commits between v1.28.0 and v3.0.0.

## Why the number jumped from 1.28 to 3.0

There was never a stable 2.0.0. The work on this page was stabilised as `v2.0.0-rc.1` through `v2.0.0-rc.5`, and the final dispatch produced `v3.0.0` instead of the 2.0.0 it was stabilising towards.

`[per the maintainer]` this was a release-workflow defect, not a decision: a forced version bump compounded on the pre-release's series target instead of naming the release kind, so dispatching a major bump at `v2.0.0-rc.5` emitted 3.0.0 and 2.0.0 was never cut. The defect is [fastmcp-server-template#154](https://github.com/pvliesdonk/fastmcp-server-template/issues/154), since fixed upstream, and the framing is recorded in [#1054](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1054). No work was abandoned and no feature was renumbered. The [1.x page](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/releases/1.x/#version-numbers-that-were-never-released) carries the full ledger of skipped numbers.

## Upgrade notes

Read these before upgrading from 1.28. Four affect existing deployments.

### The package root exports `Vault`, not `Collection`

`Collection` was renamed to `Vault`, `CollectionConfig` to `VaultConfig`, and `CollectionStats` to `VaultStats`, with the module moving from `collection.py` to `vault.py` ([#629](https://github.com/pvliesdonk/markdown-vault-mcp/issues/629)). No back-compatible alias ships, so `from markdown_vault_mcp import Collection` raises `ImportError` after the upgrade. `load_config` and `SimilarItem` are gone from the package root as well; configuration is now built with `VaultConfig.from_env()`.

This is the change the major version is for. Only Python API consumers are affected. Environment variables, the CLI and the MCP tool names are unchanged by the rename.

The rename was the final step of a decomposition that ran through the whole release. `Collection` had grown to 1,705 lines and owned index-write state alongside everything else ([#576](https://github.com/pvliesdonk/markdown-vault-mcp/issues/576)); the work extracted an `indexing/` package, a write-callback dispatcher, and four facets (reader, writer, graph and index, [#604](https://github.com/pvliesdonk/markdown-vault-mcp/issues/604)) before removing the 39 flat delegators and renaming what was left.

### `chunks_per_doc` is now `chunks_per_file`, and `limit` counts files

`MARKDOWN_VAULT_MCP_CHUNKS_PER_DOC` was renamed to `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE`, keeping its default of `2`. Set the new name; the old one is no longer read.

The `limit` parameter on `search` and `get_similar` now counts files rather than chunks ([#469](https://github.com/pvliesdonk/markdown-vault-mcp/issues/469)). See [Search returns files, not chunks](#search-returns-files-not-chunks) below for what changed in the response shape.

### Attachment reads are capped at 1 MB, note reads at 256 KB

`MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` dropped from `10.0` to `1.0`. The configuration reference at v3.0.0 gives the reason: most LLM contexts cannot survive a 10 MB base64-encoded attachment, so the old default was a silent context blow-up. Set the variable back to `10` explicitly if a non-LLM consumer needs the old ceiling.

`MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` is new, defaulting to 262144 bytes. Whole-document `.md` reads above it raise `ValueError`; partial reads through `read(path, section=...)` bypass the cap, and `0` disables it ([#442](https://github.com/pvliesdonk/markdown-vault-mcp/issues/442)).

### A reindex is worth running once after the upgrade

Chunk boundaries and vector metadata both changed during this range. Vector rows gained `start_line` so that sections sort stably inside a group, and the character cap on chunks is now derived from the embedding model's context length ([#649](https://github.com/pvliesdonk/markdown-vault-mcp/issues/649)). Existing indexes keep working, but a single `reindex` after upgrading puts them on the new boundaries.

## Highlights

### Startup no longer blocks on the index

Before this release, a cold start built the full-text index inside the MCP `initialize` handshake. On a large vault with no pre-built index that pushed the handshake past the client's timeout, and the documented workaround was to run the CLI first ([#513](https://github.com/pvliesdonk/markdown-vault-mcp/issues/513)).

The lifespan now loads whatever persistent state exists, schedules the rest as background work, and returns. Read tools answer from whatever is indexed at that moment. Tools that cannot answer at all from a partial index wait on readiness at the MCP layer rather than failing, and `get_index_status` is a tool rather than a field buried in `get_server_info`. Pre-building through the CLI became an optimisation instead of a prerequisite.

Getting there took the concurrency work underneath it. Sharing one SQLite connection across threads surfaces cross-thread errors on Python 3.12 and later, and several attempts at background indexing failed on exactly that; the index moved to per-thread connections with a strong-reference registry first ([#519](https://github.com/pvliesdonk/markdown-vault-mcp/issues/519)), then to a single-owner writer thread that owns every mutation of both the full-text and vector indexes through a first-in-first-out job queue ([#559](https://github.com/pvliesdonk/markdown-vault-mcp/issues/559)). Contended writes retry on `SQLITE_LOCKED` ([#560](https://github.com/pvliesdonk/markdown-vault-mcp/issues/560)).

The vocabulary changed with it. `get_index_status` reports `queryable` rather than `ready`, `IndexNotReadyError` became `IndexUnavailableError` and carries a `reason` discriminator, and `MARKDOWN_VAULT_MCP_READY_TIMEOUT_S` became `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/issues/538)). All of those names were introduced and renamed inside this same range, so upgrading from 1.28 sees only the final spelling.

Because writes are now asynchronous, reads say whether they might be behind. Every index-querying read tool reports freshness in `result._meta.index_stale` and returns its bare payload unchanged ([#646](https://github.com/pvliesdonk/markdown-vault-mcp/issues/646)); a caller that needs a guarantee passes `wait_for_pending_writes=true` and blocks until the writer drains, bounded by `MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S` ([#534](https://github.com/pvliesdonk/markdown-vault-mcp/issues/534)).

### Search returns files, not chunks

Adaptive chunking, which landed in 1.28, had an unintended consequence: `search`, `get_similar` and `get_context` started surfacing several chunks of the *same* document in the top results, crowding out other files that matched. In the reported case a `get_context` call with `similar_limit=5` returned two distinct documents, four of the five slots taken by chunks of one note ([#469](https://github.com/pvliesdonk/markdown-vault-mcp/issues/469)).

All three call sites now field-collapse: the result is a `GroupedResult` per file, scored by its best section, with the matching sections as a sub-list of `SectionHit`. `chunks_per_file` bounds the sections shown per file, defaulting to `2` for `search` and `get_similar` and `1` for `get_context` so dossiers stay compact. The design follows the same fuse-then-collapse ordering as Elasticsearch's reciprocal-rank-fusion retriever and Qdrant's grouped queries, with the per-chunk length downweight applied before collapsing.

`SimilarItem` was removed rather than aliased. A companion fix stopped the length downweight applying inside `get_similar` and `get_context.similar`, where it penalised exactly the long documents those calls exist to surface ([#472](https://github.com/pvliesdonk/markdown-vault-mcp/issues/472)).

### Git synchronisation you can trigger, and a webhook

`git_sync` is a new tool that runs a pull, a push, or both, synchronously, and returns what happened ([#444](https://github.com/pvliesdonk/markdown-vault-mcp/issues/444)). Before it, both directions were purely time-based, with a deferred push 30 seconds after the last write and a pull every 600 seconds. The issue gives two motivating cases. In the first, an assistant finishes a multi-write workflow while the user waits to see the result on another device. In the second, a user has edited the vault elsewhere and wants the latest state before asking a question. Conflicts come back as conflict-file paths in the response rather than as a log line. The tool is hidden when git is not in managed mode.

For multi-author vaults there is now a push webhook. Setting `MARKDOWN_VAULT_MCP_GITHUB_WEBHOOK_SECRET` mounts a `POST /github-webhook` route that verifies the GitHub HMAC-SHA256 signature and triggers a forced pull plus reindex, cutting staleness from up to the pull interval down to delivery latency. The route is only mounted on HTTP transports and only when the secret is set, so stdio and single-user deployments are unaffected. The periodic pull loop stays on as a backstop for missed deliveries.

Pulls also became less likely to leave a mess. A write landing on disk just before its deferred commit, concurrent with a pull, used to abort the pull on a dirty tree and cascade into a rejected push and a spurious conflict sibling. Both pull paths now pause new writes and drain the deferred-commit queue before fetching, so the merge always runs on a clean tree ([#571](https://github.com/pvliesdonk/markdown-vault-mcp/issues/571)).

Two smaller git changes are worth knowing about: commit identity can be taken from OIDC claims per request, via `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM`, with the author separated from the committer so per-user attribution survives; and `get_history` and `get_diff` now work on attachments, rename-aware ([#342](https://github.com/pvliesdonk/markdown-vault-mcp/issues/342)).

### External edits are noticed without git

A vault edited by something other than the server (a text editor, a sync daemon, a `cp -r`) had no automatic detection unless git pull or the webhook was running ([#558](https://github.com/pvliesdonk/markdown-vault-mcp/issues/558)). A watchdog-backed file watcher now triggers a reindex after a debounce window, controlled by `MARKDOWN_VAULT_MCP_FILE_WATCHER` and `MARKDOWN_VAULT_MCP_FILE_WATCHER_DEBOUNCE_S`. It is mutually exclusive with the pull loop and the webhook, both of which already reindex on their own cadence; mixing them risks scanning mid-checkout.

### A production report rebuilt index-state integrity

[@mikebronner](https://github.com/mikebronner) ran the server on a 181 MB, 729-file vault, hit boot times past the 30-second client timeout, and diagnosed four defects on a fork of 1.20.0 before re-verifying each against the 2.0.0-rc line and filing them as one issue with file-and-line evidence ([#665](https://github.com/pvliesdonk/markdown-vault-mcp/issues/665)). The deployment pattern behind them is one the issue argues is common for MCP servers: external processes write markdown while no server is running, and several instances boot concurrently against the same index.

The fixes landed as three pull requests, contributed by the reporter:

- **Warm boots reconcile offline changes.** A warm restart short-circuited on a sentinel and never scanned the filesystem, so anything written while the server was down stayed invisible until an unrelated event, and the new staleness signal reported fresh, because it was computed from in-process writer state only. The lifespan now enqueues an incremental reindex after the build job.
- **Skipped files are remembered.** Files failing the frontmatter check never entered the index, and tracker state was rebuilt from index contents, so every scan re-detected them as added and re-logged each skip. Tracker state is now versioned and records deterministic skips by content hash; transient errors are deliberately not recorded, so they retry.
- **Embeddings converge instead of drifting.** `build_embeddings(force=False)` skipped all work whenever the vector index was non-empty, which made drift permanent once present. It now diffs the indexed chunk set against stored vector metadata and embeds exactly the missing chunks, dropping orphans.
- **A dotted `--cov` no longer kills the test session.** The eager package root pulled in the whole tree, and coverage's package resolution left an orphaned import hook behind that took the next import down with it. The package root became lazy through PEP 562.

Measured on the reporter's vault and quoted from the issue: warm boot fell from 30 s or more to between 1 and 2 s. Index size fell from 1.8 GB to 4.7 MB. Drift converged to zero on each boot.

### Configuration fails fast

Invalid configuration used to degrade quietly. Most sharply: when an embedding provider could not be constructed, the server logged a warning and continued with keyword-only search, even when the operator had explicitly named a backend in `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`, so a misconfigured deployment silently lost semantic search with no signal ([#638](https://github.com/pvliesdonk/markdown-vault-mcp/issues/638)).

An explicitly chosen provider that cannot be built now raises at startup. Auto-detection is unchanged: with the variable unset and no backend available, the server still warns and continues with semantic search disabled, so the zero-configuration path keeps working. Range checks moved into construction so they also cover direct `VaultConfig(...)` calls, and the project standardised on one catchable `ConfigurationError` shared across the server family.

### Transfer links, removed and rebuilt

One-time download links existed before this release, built on shared file-exchange machinery. All of it was removed ([#620](https://github.com/pvliesdonk/markdown-vault-mcp/issues/620)) and replaced by local code written from scratch, deliberately without reusing the old machinery.

What ships is two tools and one route: `create_download_link(path)` mints a one-time capability URL for a note or attachment, `create_upload_link(path)` mints one for a fixed, pre-validated destination, and `GET/POST /transfer/{token}` serves them. Both move bytes out of band, so a file transfer costs no LLM context. Lifetimes are bounded by `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S` and `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`, and uploads by `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES`. The guide is [Transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/transfer-links/index.md).

### The documentation site gained a generator and versions

The [configuration generator](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration-generator/index.md) is a decision-tree wizard that emits a Claude configuration file, a `.env`, a `docker run` line, a Compose file or a systemd unit for your answers ([#693](https://github.com/pvliesdonk/markdown-vault-mcp/issues/693)). Everything runs in the browser; non-secret answers are encoded in the URL so a configuration is shareable, and secret fields are kept out of the URL with placeholders in the output.

The site itself became versioned with `mike` ([#700](https://github.com/pvliesdonk/markdown-vault-mcp/issues/700)): each stable release publishes its own minor version with a selector, and pre-releases feed a single rolling unstable version.

## Security

The `fetch` tool's guard against server-side request forgery only inspected the host literal in the URL, so a public hostname resolving to an internal address passed the check, and a host that passed could be re-pointed between the check and the connection. The old helper's own docstring conceded it did not prevent rebinding. `fetch` now resolves before connecting, rejects the request if any resolved address is private, loopback, link-local, unspecified, reserved or multicast, fails closed on a resolution error, and dials the exact address it validated while carrying the original `Host` header and TLS server name ([PR #704](https://github.com/pvliesdonk/markdown-vault-mcp/pull/704)).

Dependency work in the same range cleared advisories in `starlette`, `pyjwt`, `cryptography`, `python-multipart`, `pip` and `urllib3`.

## Other changes

### Reading and editing

- `read` error messages point at the alternative that would have worked, and read-side size guards are documented as a context cost on the tools that incur one ([#442](https://github.com/pvliesdonk/markdown-vault-mcp/issues/442)).
- `read(path, section=...)` tolerates whitespace-run differences when matching a heading ([PR #497](https://github.com/pvliesdonk/markdown-vault-mcp/pull/497)).
- Failed multi-line edits report the line that actually diverged ([PR #502](https://github.com/pvliesdonk/markdown-vault-mcp/pull/502)).
- A UTF-8 byte-order mark is normalised on every vault markdown read ([#673](https://github.com/pvliesdonk/markdown-vault-mcp/issues/673)) and on ingress through `fetch` and transfer uploads ([#681](https://github.com/pvliesdonk/markdown-vault-mcp/issues/681)).

### Indexing and links

- Wikilinks are resolved after every write, edit, delete and rename, not only on a full reindex ([PR #495](https://github.com/pvliesdonk/markdown-vault-mcp/pull/495)).
- Symlinked content is followed on Python 3.13 and later, including when the source directory is itself a symlink ([#508](https://github.com/pvliesdonk/markdown-vault-mcp/issues/508)).
- Excluded paths are filtered before hashing during change detection, instead of being hashed and discarded downstream ([#257](https://github.com/pvliesdonk/markdown-vault-mcp/issues/257)), and the index runs an `optimize` pass after bulk purges ([#669](https://github.com/pvliesdonk/markdown-vault-mcp/issues/669)).
- Tied search results break their tie deterministically ([PR #499](https://github.com/pvliesdonk/markdown-vault-mcp/pull/499)), and no chunk exceeds the configured word budget ([PR #496](https://github.com/pvliesdonk/markdown-vault-mcp/pull/496)).
- Embedding-build progress is logged in deciles rather than per batch ([#311](https://github.com/pvliesdonk/markdown-vault-mcp/issues/311)).

### Graph and MCP Apps

- `get_backlinks` and `get_outlinks` accept a `limit` ([#617](https://github.com/pvliesdonk/markdown-vault-mcp/issues/617)).
- The hub graph view fetches in parallel, producing byte-identical output to the sequential version ([#285](https://github.com/pvliesdonk/markdown-vault-mcp/issues/285)).
- `MARKDOWN_VAULT_MCP_DISABLE_APPS_UI` hides `browse_vault` and `show_context` from the tool listing, for clients that cannot render an MCP Apps panel and pay for the listing anyway ([#527](https://github.com/pvliesdonk/markdown-vault-mcp/pull/527)).

### Embeddings

- The `openai` provider accepts `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` and `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL`, so it can point at any OpenAI-compatible embeddings endpoint. Defaults are unchanged. Per the contributing pull request, the alternative was patching installed package files, which does not survive a fresh `uvx` environment ([PR #505](https://github.com/pvliesdonk/markdown-vault-mcp/pull/505)).
- The chunk character cap is derived from the embedding model's context length rather than a word count. A word budget does not bound tokens: token-dense content produced chunks of 287 to 399 words carrying 4,000 to 5,400 tokens, which Ollama rejected outright and FastEmbed silently truncated ([#649](https://github.com/pvliesdonk/markdown-vault-mcp/issues/649)).

### Platform

- The server adopted `fastmcp-pvl-core` 3.x, which reworked the middleware stack and replaced the event-store factory with a unified key-value factory behind `MARKDOWN_VAULT_MCP_KV_STORE_URL` ([#492](https://github.com/pvliesdonk/markdown-vault-mcp/issues/492)).
- The mcpb bundle and Claude Code plugin manifests cover the full environment surface ([#345](https://github.com/pvliesdonk/markdown-vault-mcp/issues/345)).
- The README, configuration reference and guides were audited against the code ([#653](https://github.com/pvliesdonk/markdown-vault-mcp/issues/653)), and the docs corpus was scrubbed of machine-prose tells and brought under Vale ([#686](https://github.com/pvliesdonk/markdown-vault-mcp/issues/686)).

## Thanks

Three contributions in this release came from outside the repository:

- [@mikebronner](https://github.com/mikebronner) for [#665](https://github.com/pvliesdonk/markdown-vault-mcp/issues/665): four diagnosed defects with file-and-line evidence, re-verified against `main`, and three of the four fixes contributed as pull requests, plus [#669](https://github.com/pvliesdonk/markdown-vault-mcp/issues/669)
- [@Finomosec](https://github.com/Finomosec) for the `DISABLE_APPS_UI` setting ([#527](https://github.com/pvliesdonk/markdown-vault-mcp/pull/527))
- [@huiyi9420](https://github.com/huiyi9420) for OpenAI-compatible embedding configuration ([PR #505](https://github.com/pvliesdonk/markdown-vault-mcp/pull/505))

## Patch releases

## v3.0.1 (June 22, 2026)

Fixes an idle-CPU regression in the file watcher. Anyone running with `MARKDOWN_VAULT_MCP_FILE_WATCHER=true` on a non-git vault should take this patch.

On a non-git vault with the watcher active, the server reindexed endlessly and burned 20% to 30% of a CPU at idle with no real file changes, logging `0 added, 0 modified, 0 deleted` continuously ([#720](https://github.com/pvliesdonk/markdown-vault-mcp/issues/720), reported with `py-spy` and `inotifywait` evidence by [@Finomosec](https://github.com/Finomosec)).

The reindex walk itself was the trigger. Hashing every file makes the kernel emit inotify read events, the handler filtered only on hidden paths and not on event type, so each walk scheduled the next one. The handler now reacts only to mutating events, and the vector index is saved only when a reindex actually changed it, instead of on every empty-diff pass.

## v3.0.2 (June 25, 2026)

Hardens the vector sidecar against corruption and fixes wikilinks written with an escaped pipe, the form Obsidian requires inside a table cell.

The vector sidecar is now written atomically, a corrupt sidecar self-heals on load rather than failing the process, both load paths share one loader ([#736](https://github.com/pvliesdonk/markdown-vault-mcp/issues/736)), and row-count parity between the sidecar and its metadata is enforced.

`[[path\|alias]]` parsed with the escaping backslash baked into the link target, so the link was reported broken and its graph edge was dropped ([#731](https://github.com/pvliesdonk/markdown-vault-mcp/issues/731), reported by [@Denzilla04](https://github.com/Denzilla04)). The reporter's vault had about 190 false broken links, all from this.

## v3.0.3 (June 26, 2026)

Fixes partial results from `read(path, section=...)`. Anyone reading long sections through the tool was silently getting only part of them.

A section large enough to be split across chunks returned only its first chunk, with no error and no truncation marker, and a section with sub-headings returned only the preamble before the first one ([#741](https://github.com/pvliesdonk/markdown-vault-mcp/issues/741)). Section reads now reassemble every row under the heading.

Two guards landed alongside it: whole-document `read` degrades to `None` with a logged warning on malformed frontmatter ([#742](https://github.com/pvliesdonk/markdown-vault-mcp/issues/742)) rather than raising, and its content read is guarded against failing mid-read ([#745](https://github.com/pvliesdonk/markdown-vault-mcp/issues/745)).

## v3.0.4 (June 27, 2026)

One fix: git sync resolves its comparison ref as `origin/<branch>` instead of `@{upstream}`, which is absent on a branch with no configured upstream.

## All changes

See [CHANGELOG.md](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/CHANGELOG.md) for the full commit-level list, or the [v1.28.0 to v3.0.0 comparison](https://github.com/pvliesdonk/markdown-vault-mcp/compare/v1.28.0...v3.0.0).
