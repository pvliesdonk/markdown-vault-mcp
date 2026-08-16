# 1.x

March 9 to May 3, 2026, from the first tagged release to v1.28.0.

Reconstructed after the fact, and thinner than the pages after it

This page is a best-effort backfill written in August 2026 ([#1058](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1058)). It is not contemporaneous, and the record it was reconstructed from is thin: most of this period predates the pull-request discipline that links every change to an issue. Where the evidence supports only a factual statement, this page makes only a factual statement. Where it supports nothing, the page says so rather than guessing. Do not use it as upgrade guidance for a specific 1.x version.

## What this page covers

One page for the whole 1.x line: 42 stable releases across 27 minor versions, 801 commits, in eight weeks. Writing 27 separate pages would have produced 27 thin ones, and nobody upgrades from v1.7.0 today. What is useful now is the shape of the arc: the server's capabilities at the start of the line, its capabilities at the end, and the places where the version numbers skip.

Individual releases in this range have no page of their own and will not get one.

This page is final. The 1.x line closed at v1.28.0 and will receive no further releases, so no patch section will ever be appended below.

## Evidence, and its limits

The measurable gap between this page and the ones after it is how often a commit in the range carries a link to an issue explaining it. Counted per stable-to-stable range and recorded in [#1058](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1058):

| Range             | Ranges counted | Commits with a linked issue          |
| ----------------- | -------------- | ------------------------------------ |
| v1.0.0 to v1.28.0 | 41             | mostly 0% to 40%, seven ranges at 0% |
| v1.28.0 to v3.0.0 | 1              | 78%                                  |
| v3.0.0 to v3.1.0  | 5              | 50% to 100%                          |

Those percentages are the ones recorded in the issue, counted by its own rule. Recounting the v1.28.0 to v3.0.0 range against one rule stated in full, that the commit message carries a closing keyword (`closes`, `fixes`, `resolves`, `refs`) next to an issue number, gives 76%: 199 of the 261 commits. Two rules, one body of evidence. The figures differ because they count different things, and either one leaves the 1.x ranges far behind.

Seven ranges in this period contain no linked issue at all: `v1.8.0..v1.8.1` (5 commits), `v1.18.0..v1.18.1` (9), `v1.19.0..v1.19.1` (4), `v1.19.1..v1.20.0` (8), `v1.23.1..v1.25.0` (10), `v1.25.0..v1.27.0` (8) and `v1.27.0..v1.27.1` (42). The largest of them, `v1.27.0..v1.27.1`, is accounted for in [the April section](#edit-reliability-git-history-and-the-move-to-shared-machinery-april) below, where the maintainer names what it was. For the other six, nothing beyond the code itself records what the change was for.

The narrative below is anchored on artifacts that can be checked rather than on inferred motives: the published tool reference at each tag, the configuration reference at each tag, and the issues that do exist. Each claim links what it rests on.

## The arc

### A working server, then a deployable one (March 9 to 13)

The first tagged release exposed 13 tools: `search`, `read`, `write`, `edit`, `delete`, `rename`, the three `list_*` calls, `reindex`, `stats`, `build_embeddings` and `embeddings_status`. Hybrid full-text and semantic search, frontmatter-aware indexing and git-backed writes were all there on day one.

The next four days were about running it somewhere other than a laptop. Git writes moved behind a `GitWriteStrategy` with deferred push ([PR #64](https://github.com/pvliesdonk/markdown-vault-mcp/pull/64)), a streamable HTTP transport arrived for container deployments ([PR #70](https://github.com/pvliesdonk/markdown-vault-mcp/pull/70)), and the server moved to FastMCP 3 ([PR #72](https://github.com/pvliesdonk/markdown-vault-mcp/pull/72)). Authentication came in two forms, OIDC through a proxy ([PR #76](https://github.com/pvliesdonk/markdown-vault-mcp/pull/76)) and a simple bearer token ([PR #167](https://github.com/pvliesdonk/markdown-vault-mcp/pull/167)), with a multi-auth mode accepting either ([PR #237](https://github.com/pvliesdonk/markdown-vault-mcp/pull/237)) shortly after.

Two changes from this stretch still shape how the server behaves. Optimistic concurrency landed: reads return an etag and writes accept `if_match` ([PR #125](https://github.com/pvliesdonk/markdown-vault-mcp/pull/125), [PR #126](https://github.com/pvliesdonk/markdown-vault-mcp/pull/126)). And the default embedding backend changed twice for the same reason. First from `sentence-transformers` to `fastembed` ([PR #144](https://github.com/pvliesdonk/markdown-vault-mcp/pull/144)), then to a smaller default model, in both cases to stop the container running out of memory ([#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306)).

The project also became findable in this window: GitHub topics, PyPI metadata and a `server.json` manifest for the official MCP registry, which the issue behind it calls "the single highest-leverage action" because downstream aggregators consume it automatically ([#112](https://github.com/pvliesdonk/markdown-vault-mcp/issues/112)).

### The vault became a graph (March 14 to 16)

The largest single capability jump in 1.x. Links were extracted and stored in the index ([PR #194](https://github.com/pvliesdonk/markdown-vault-mcp/pull/194)), and on top of that came `get_backlinks`, `get_outlinks` and `get_broken_links` ([PR #195](https://github.com/pvliesdonk/markdown-vault-mcp/pull/195)), `get_similar` ([PR #196](https://github.com/pvliesdonk/markdown-vault-mcp/pull/196)), `get_recent` ([PR #197](https://github.com/pvliesdonk/markdown-vault-mcp/pull/197)), the `get_context` dossier ([PR #200](https://github.com/pvliesdonk/markdown-vault-mcp/pull/200)), `get_orphan_notes` and `get_most_linked` ([PR #205](https://github.com/pvliesdonk/markdown-vault-mcp/pull/205)), and `get_connection_path`, a breadth-first shortest path between two notes ([PR #227](https://github.com/pvliesdonk/markdown-vault-mcp/pull/227)).

Because links were now first-class, renaming a note started rewriting the links pointing at it ([PR #206](https://github.com/pvliesdonk/markdown-vault-mcp/pull/206)).

The same days brought the two extension points the workflow guides later build on: MCP resources and prompts ([PR #97](https://github.com/pvliesdonk/markdown-vault-mcp/pull/97)) and user-defined prompts loaded from a mounted directory, overriding the built-in ones ([PR #226](https://github.com/pvliesdonk/markdown-vault-mcp/pull/226)).

### Visual views, packaging and Obsidian semantics (March 18 to 31)

The published tool reference goes from 13 tools at v1.6.0 to 26 at v1.18.0. Nine of the thirteen new entries are the graph work above; the rest arrived here.

MCP Apps support was built as a single-page application with tab routing rather than a resource per view. That was an explicit architecture decision, chosen for a single sidebar panel with tabbed navigation, at the cost of a more complex page ([#273](https://github.com/pvliesdonk/markdown-vault-mcp/issues/273)). Three views followed on the same shell: the Note Context Card ([#274](https://github.com/pvliesdonk/markdown-vault-mcp/issues/274)), the Graph Explorer ([#275](https://github.com/pvliesdonk/markdown-vault-mcp/issues/275)) and the Vault Browser ([#276](https://github.com/pvliesdonk/markdown-vault-mcp/issues/276)), with cross-view navigation and send-to-conversation standardised across them ([#277](https://github.com/pvliesdonk/markdown-vault-mcp/issues/277)).

Two tools for getting bytes in and out arrived alongside: `fetch`, which downloads a URL into the vault ([PR #259](https://github.com/pvliesdonk/markdown-vault-mcp/pull/259)), and `create_download_link`, a one-time HTTP endpoint for an artifact ([PR #261](https://github.com/pvliesdonk/markdown-vault-mcp/pull/261)).

Deployment surfaces multiplied: systemd units ([PR #250](https://github.com/pvliesdonk/markdown-vault-mcp/pull/250)), `.deb` and `.rpm` packages built with nfpm ([PR #251](https://github.com/pvliesdonk/markdown-vault-mcp/pull/251)), and a persistent event store so HTTP sessions survive a restart ([PR #279](https://github.com/pvliesdonk/markdown-vault-mcp/pull/279)).

Obsidian compatibility was sharpened twice: wikilinks resolve vault-wide rather than relative to the containing folder ([PR #234](https://github.com/pvliesdonk/markdown-vault-mcp/pull/234)), and frontmatter aliases resolve as link targets ([PR #319](https://github.com/pvliesdonk/markdown-vault-mcp/pull/319)). Pull conflicts stopped being fatal: a failed fast-forward rebases ([PR #230](https://github.com/pvliesdonk/markdown-vault-mcp/pull/230)) and an unresolvable conflict is written out as a sibling file, the way Syncthing handles one ([PR #232](https://github.com/pvliesdonk/markdown-vault-mcp/pull/232)).

### Edit reliability, git history, and the move to shared machinery (April)

The `edit` tool was rebuilt around a failure mode specific to LLM callers. Its issue describes the round trip precisely: file to MCP response to context window to model output to MCP request to string comparison, where "any transformation anywhere in that chain breaks the match" ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/issues/325)). Three changes followed. An optional line-range mode covers large block replacements. A normalised-matching fallback survives smart quotes and dash substitution while preserving the original bytes everywhere except the replaced span. Diagnostics name the diverging line and character offset, so the model can correct itself instead of retrying blindly.

Git history became readable through the tools: `get_history` and `get_diff` ([PR #337](https://github.com/pvliesdonk/markdown-vault-mcp/pull/337)) took the published tool count to 28, where it stayed for the rest of 1.x. Distribution widened again with a Claude Code plugin and an mcpb bundle ([PR #350](https://github.com/pvliesdonk/markdown-vault-mcp/pull/350)).

The last three weeks were mostly structural, and they are the reason later releases look the way they do. `Collection` was split into managers with dependency injection ([PR #378](https://github.com/pvliesdonk/markdown-vault-mcp/pull/378)), then seven consecutive refactors moved configuration, authentication, the middleware stack, logging, the event store, the artifact store and the command line onto a shared library, `fastmcp-pvl-core` ([PR #396](https://github.com/pvliesdonk/markdown-vault-mcp/pull/396) through [PR #402](https://github.com/pvliesdonk/markdown-vault-mcp/pull/402)), and the project was re-based on a copier template that owns the release and CI machinery ([PR #405](https://github.com/pvliesdonk/markdown-vault-mcp/pull/405)).

`[per the maintainer]` the 42 commits between `v1.27.0` and `v1.27.1`, the largest block in 1.x with no linked issue behind it, are the rest of that same separation: the split of shared infrastructure out of this repository into two upstreams, the `fastmcp-server-template` copier template and the `fastmcp-pvl-core` library. It ran as many small ports back and forth between the repositories rather than as one tracked feature, so the issue record for it is thin. Per-change detail for this stretch lives in the pull request bodies, not in issues.

The pull requests in the range show the shape of that work. Sentinel markers were carved into `config.py` and `CLAUDE.md` so template updates and project-owned content stop overwriting each other ([PR #417](https://github.com/pvliesdonk/markdown-vault-mcp/pull/417), [PR #419](https://github.com/pvliesdonk/markdown-vault-mcp/pull/419)), the server module was renamed to the template's `server.py` and `make_server` spelling ([PR #416](https://github.com/pvliesdonk/markdown-vault-mcp/pull/416)), template-owned files that had drifted were converged back ([PR #421](https://github.com/pvliesdonk/markdown-vault-mcp/pull/421)), and the weekly `copier update` workflow that keeps them converging was bootstrapped ([PR #415](https://github.com/pvliesdonk/markdown-vault-mcp/pull/415)). That last body records why the backfill had to be done by hand: a literal update from the template flipped the license and replaced the command line, so the repository had to be reshaped before the automation could be trusted.

The line closes with the search work that the 3.0 release then had to correct. v1.28.0 introduced four ranking knobs at once: a per-document cap on result slots, a length downweight, snippet truncation, and an adaptive chunker that descends heading levels when a chunk runs over budget ([PR #433](https://github.com/pvliesdonk/markdown-vault-mcp/pull/433), closing [#432](https://github.com/pvliesdonk/markdown-vault-mcp/issues/432)). The adaptive chunker is what later caused several chunks of one document to crowd the top of a result list, which [3.0](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/releases/3.0/#search-returns-files-not-chunks) fixed by collapsing results per file.

## Version numbers that were never released

`[verified: git tag]` The stable line skips 1.24 and 1.26 entirely, and never reaches 2.x: `v1.23.2`, `v1.24.0`, `v1.26.0` and `v2.0.0` exist only as pre-release tags. v1.25.0 and v1.27.0 are stable tags carrying nothing but their own release commit, because everything they contain was already tagged on the release candidate immediately before them.

`[per the maintainer]` these are unintended bumps from two release-workflow defects, both since fixed upstream, and not abandoned feature work. Nothing was being built under those numbers, so there is no story to reconstruct:

- [fastmcp-server-template#154](https://github.com/pvliesdonk/fastmcp-server-template/issues/154) reports that a forced version bump compounded on the pre-release's series target instead of naming the release kind. Dispatching a major bump at `v2.0.0-rc.5` produced `v3.0.0`, and 2.0.0 was never cut. Fixed by template#165.
- [fastmcp-server-template#235](https://github.com/pvliesdonk/fastmcp-server-template/issues/235) reports that release-candidate detection matched an abandoned series and cut an `-rc.1` from a stable base, which is where `v1.23.2-rc.1`, `v1.24.0-rc.1` and `v1.26.0-rc.1` come from. Fixed by template#236 and adopted here in [#854](https://github.com/pvliesdonk/markdown-vault-mcp/issues/854).

Both fixes are in this repository. The framing above is recorded on [#1054](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1054); from the tags alone the obvious reading is an abandoned release series, and that reading is wrong.

## What could not be reconstructed

Stated plainly, because a gap left unmarked reads as an absence of change:

- Six of the seven zero-density ranges listed above have no recoverable rationale. The seventh and largest, `v1.27.0..v1.27.1` at 42 commits, is the upstream split described in the April section; even there the account is a shape rather than a change-by-change record, because the detail sits in pull request bodies rather than in issues.
- Patch releases in this line are not individually described. Several exist only because a release-workflow run produced them, and separating those from real fixes is not possible from the record.
- No upgrade or breaking-change analysis is offered for any step inside 1.x. The import surface guard that makes such an analysis mechanical ([template#352](https://github.com/pvliesdonk/fastmcp-server-template/issues/352)) did not exist yet, and reconstructing one release-by-release from source would be inference presented as fact.

## All changes

See [CHANGELOG.md](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/CHANGELOG.md) for the full commit-level list, or the [v1.0.0 to v1.28.0 comparison](https://github.com/pvliesdonk/markdown-vault-mcp/compare/v1.0.0...v1.28.0).
