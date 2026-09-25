# Log

## 2026-09-25

- Revised [MCP model-facing text](mcp-model-facing-text.md): markdown-vault-mcp
  removed `tests/test_client_surface_budget.py` and its aggregate ceilings
  (markdown-vault-mcp#1600, #1601). The 2026-09 baseline now cites the last
  commit that carried the test and the design text, and a new claim records
  that no source derives an aggregate ceiling; only the per-item client
  limits stay. Next review unchanged: 2027-03-23.
- Added [MCP tool outcomes and errors](mcp-tool-outcomes-and-errors.md),
  checked against the MCP 2026-07-28, 2025-11-25 and 2025-06-18 schema and
  tools pages at commit ab3a39c1, FastMCP 4.0.9, the MCP Python SDK 2.2.0 and
  fastmcp-pvl-core 9.0.1 source plus an in-memory probe, the Anthropic, OpenAI
  and Gemini tool-result docs, and the reference, GitHub, Sentry and Notion
  servers' source. Found that the spec never says whether "not found" or
  "permission denied" sets `isError`, that the Python SDK logs an anticipated
  `ToolError` at INFO while FastMCP defaults it to ERROR, and that pvl-core's
  middleware logs every raised `ToolError` as `tool_call_failed` at ERROR.
  Refute pass re-read the schema, the issue 199 ruling, the SDK and FastMCP
  docstrings, Anthropic's `is_error` wording and the Sentry and filesystem
  sources; host-side handling of `isError` stays unverified. Next review:
  2027-03-25.
- Added [Negative outcomes and faults outside MCP](negative-outcomes-and-faults.md),
  widening the same question beyond MCP: RFC 9110 and 9457, gRPC status codes,
  google.rpc.Code, AIP-193/194, the SRE Workbook, OpenTelemetry semconv v1.44.0
  (HTTP, gRPC, recording errors) and the Trace API, GraphQL September2025 with
  graphql.org, Apollo and Shopify, JSON-RPC 2.0, and the Rust, Go, Python,
  .NET, Java and Swift error-handling docs. Found broad agreement that a
  valid negative outcome is not a fault (OpenTelemetry leaves server-side 4xx
  and NOT_FOUND unset; only 5xx burns the SRE example SLO), and disagreement
  on which wire slot carries not-found. Refute pass re-read the OpenTelemetry,
  RFC 9110, gRPC, SRE, graphql.org, Rust and Python quotes. Next review:
  2027-09-25.
- Extended [fastmcp-pvl-core transfer errors and retries](fastmcp-transfer.md)
  with the validate hook's rejection contract, read from `register.py` at
  pvl-core 9.0.1 and 10.0.0 and `sink.py`'s `TransferValidator` at 10.0.0:
  through 9.x any exception rejects, and from 10.0.0 only a `ToolError` at INFO
  does. Pinned by the sink tests for #1623. Next review unchanged: 2027-03-13.

## 2026-09-23

- Added [MCP model-facing text](mcp-model-facing-text.md), checked against
  the MCP 2026-07-28 and 2025-11-25 schemas, the Claude, OpenAI, Gemini,
  VS Code and Cursor documentation, and FastMCP 4.0.5 by probe. Covered
  who reads each description field, Claude Code's 2,048-unit cut and
  tool-search deferral, FastMCP's docstring parsing (the issue 4952 leak
  and the unparsed resource docstring), pvl-core's instruction roles,
  the markdown-vault-mcp surface baseline, and SEP-2640 skills over MCP
  with FastMCP's skills provider probed on the wire. Refute pass re-fetched the
  claims the skill depends on; the Cursor tool cap and the Claude Desktop
  resource UI stay unverified. The same pass found the scaffold's own
  `ping`, `status` and `summarize` docstrings shipping developer
  commentary and a framework link as their wire descriptions, and fixed
  them. Next review: 2027-03-23.

- Added [Git submodules seen from a superproject](git-submodules.md) for the submodule epics #1561 and #1562. Checked seventeen git-scm.com manual pages (gitsubmodules, git-submodule, gitmodules, clone, fetch, pull, merge, rebase, checkout, reset, config, ls-files, ls-tree, rev-parse, status, add, log); the pages leave the superproject-level `add` refusal, the `ls-files`/`log`/`show` answers for a path inside a submodule, and the abort on a dirty submodule undocumented, so those are `[observed]` against a throwaway superproject on git 2.55.0. Refute pass re-found every quoted phrase in its page and confirmed git-merge(1) and git-rebase(1) carry no `--recurse-submodules`. No claim is pinned yet: no test exercises a submodule; the epic's features pin what they depend on. Left uncovered: push recursion outcomes, nested submodules, cross-boundary rename staging, LFS inside a submodule. Next review: 2027-03-23.

## 2026-09-19

- Added [SQLite FTS5 delete cost and shadow-table architecture](sqlite-fts5.md) for #1535. Checked sqlite.org/fts5.html (content-carrying default, the `'delete'` command's external-content/contentless restriction) and sqlite.org/vtab.html (`xBestIndex` as the mechanism that selects a virtual table's access path). The full-scan-on-ordinary-column-filter behaviour itself is not stated by either page — recorded as `[observed]`, pinned to the project's own `EXPLAIN QUERY PLAN` regression test rather than a documentation quote. Left `[unverified]`: the big-O of an FTS5 rowid lookup specifically. Next review: 2027-03-19.
- `commonmark-gfm.md`: three **markdown-it departures** recorded for #1531, found while widening the reference family's differential corpus and worth a page of their own because the corpora use markdown-it as their oracle, so a deviation there reads as a defect in the scanner. It abandons a link outright when only whitespace follows a `(` to the end of the inline block, where §6.3 falls back to a reference (`[a](` with `[a]` defined is a shortcut); it does not fall back on a whitespace-only second span, where §4.7's "at least one non-whitespace character" makes `[ ]` no label at all; and its label scan counts bracket nesting, where §4.7 forbids an unescaped bracket in a label outright, so it reads `[a[b]]` as a label. This scanner matches markdown-it on the third and the spec on the first two; `[unverified]` whether cmark agrees on the third, which is reported to stop at the first inner `[`. Each is pinned and excluded from the sweeps by name rather than silently. The reference-link accuracy block is rewritten for the same change: all four forms now, with inline precedence real rather than incidental, definition removal bounded by the destination, and the stale "shortcut not extracted" claim gone; "shortcut references" dropped from the not-covered list; the `iter_bracket_links` deactivation claim corrected to retire link openers only, an image opener staying live (Ex. 575).
- Noted while adding the above: #1517, #1519, #1526 and #1528 each amended this page without a log entry. Not backfilled here — they are separate merged changes — but recorded so the gap is visible rather than inferred from silence.

## 2026-09-18

- Added [GitHub repository security settings](github-repository-security-settings.md),
  checked against GitHub.com documentation and two read-only API probes.
  Covered the private-vulnerability-reporting, vulnerability-alerts and
  `security_and_analysis` endpoints, their visibility and licence limits, and
  the security policy file's locations. Single pass, no refute pass and no
  writes: the page is `draft` until a generated project's bootstrap run
  confirms the calls. Next review: 2027-03-18.

## 2026-09-14

- Added [Python Future completion and cancellation](python-futures.md) for #1483. Checked Python 3.14 documentation and pinned CPython v3.14.0 source; refuted callback-thread and result-completion assumptions against the implementation. Pinned the project guarantees to held-build and cancellation-callback regressions. Next review: 2027-03-14.

- Added [Authenticated subjects and human attribution](authenticated-subjects.md) for #1463, checked against pinned sources matching installed FastMCP 4.0.3 and pvl-core 7.1.0, plus RFC 9068 and RFC 9700. Reproduced static bearer attribution through the real verifier and SDK auth context. OAuth service subjects remain a separate limitation (#1480). Next review: 2027-03-14.

## 2026-09-13

- Added [fastmcp-pvl-core transfer errors and retries](fastmcp-transfer.md) for PR #1477, checked against installed 7.1.0 and matching pinned upstream source. Records sink-selected status codes, empty error responses, release-on-failure limits, and the grace-window replay that invokes the sink again. Refute pass checked handler branches and token transitions, including backend release failures and expiry. Next review: 2027-03-13.
- Added [FastMCP 4 protocol and server behaviour](fastmcp-4.md) for #1271, checked against FastMCP 4.0.3 and fastmcp-pvl-core 7.1.0 at pinned commits. Covered SDK snake_case names, the modern guard and legacy elicitation paths, default templated-resource screening, per-server task registration, public app-tool hash metadata, and the still-root-mounted OAuth authorization-server metadata route. Refute pass re-read each claim against the implementation and added project test pins. Next review: 2027-03-13.

## 2026-09-12

- Added [GitHub planning objects](github-planning-objects.md), checked against GitHub.com documentation and GitHub CLI 2.97.0. Covered milestone identity, pagination, PR membership, nullable updates, issue hierarchy and dependencies, and documented attachment support. Refute pass retained the cross-owner REST documentation conflict and marked unreproduced UI/search observations explicitly. Next review: 2027-03-12.

## 2026-09-11

- `okf-v0.2.md`: §8 and §9 re-read at the page's pinned commit 62432a09 for #1396 (identical at `open-knowledge-format` 0b87c52c). The reserved-frontmatter departure said §8 allows "none in `log.md`" and the `type`-exemption departure that reserved files "carry none by §8"; §8 names index files only and §9 says nothing about frontmatter, so both are corrected and the silence is recorded on the §9 claim. The `type` exemption is now stated as a reading of conformance rule 2 against §3.1 rather than the text's own consequence. The reserved-frontmatter departure gains the `index_frontmatter` audit finding and its pointer to #1438; the §8 and §9 claims gain pins.

## 2026-09-09

- `obsidian-git.md` created (#1424; owner's direction on PR #1426/#1429: split the multi-author design into small PRs and start with properly researched references). Questions framed from `git/strategy.py`'s pull path, `git/conflict.py`, `build_log_markdown`, issues #229/#231 and the Obsidian-everywhere guide; sources: the plugin's README and in-repo docs, `constants.ts`, `settings.ts`, `localStorageSettings.ts`, `main.ts`, the shared, desktop and mobile git managers, isomorphic-git's `merge` and `pull` pages, Obsidian's properties and templates help pages; `merge=union` observed on git 2.55.0. Records the `conflict-files-obsidian-git.md` note the plugin writes into the vault, that author identity lives in the repository git config on every platform, and that "No changes to commit" contradicts #229's mtime premise. Left `[unverified]`: `commitMessageScript` semantics; that a new Obsidian note carries no frontmatter. Refute pass (independent, every source re-fetched): three quotes trimmed to verbatim, one citation moved from the templates page to the properties page, the push step restored to the `reset` ordering; recorded in `verified`. Codex on PR #1430: the desktop identity claim widened to the process environment the plugin builds (`process.env` plus its desktop-only PATH, `GIT_DIR` and KEY=VALUE settings, handed to simple-git), pointing at the git page for the `GIT_COMMITTER_*` precedence instead of restating it; the conflict-note consequence qualified by `required_frontmatter` and exclusion patterns.

## 2026-09-07

- `obsidian-markdown.md`: eleven `[unverified]` rows settled by the maintainer's console session on Obsidian 1.13.7 / Windows (#1358): wikilink shape (`]` inside a target, no line ending, nesting), `[[#H]]` self-edge, schemed wikilink as unresolved note, extension case, unstable equal-length tie, name case, aliases never resolving links, scalar alias parse, heading holding a wikilink, links in `%%` comments, the desktop forbidden set, NFC/NFD, markdown destinations decoded and looked up vault-wide, what `generateMarkdownLink` writes. Departures rewritten from "unverifiable" to contrary-or-deliberate; two contrary ones filed.
- `git-staging-and-commits.md`: the rebase-and-identity claim #1362 added re-sourced to git-commit(1) "COMMIT INFORMATION" and git-config(1) `user.useConfigOnly` — its `[source: git-var]` named no declared source, and git-var(1) documents only the names of `GIT_AUTHOR_IDENT`/`GIT_COMMITTER_IDENT`; the identity resolution chain (environment, `user.*`, `EMAIL`, system user plus mail hostname) recorded from the same section. The `--author` claim corrected: the environment overrides `-c user.*`, not the reverse (git-commit(1); observed on git 2.55.0); the `_git_env` departure extended with what that means for the `-c` pair the conflict commit still passes.
- `obsidian-markdown.md`: the #1350 tie-break fixture run by the maintainer on Obsidian 1.13.7 through `getFirstLinkpathDest`; the tie-break section rewritten from `[unverified]` to the observed three rules (exact vault path, own folder, shortest path string) with the fixture and pins; the relative-wikilink and path-suffix rows settled by the same run; departure entries updated.
- `commonmark-gfm.md`: the `_RE_INLINE_LINK`, reference-definition and `apply_link_replacement` departure entries re-observed after #1353 (destination parsed by §6.3's grammar, entities by §2.5's rule; the three-level parenthesis cap recorded as the deliberate departure); pins added.
- `okf-v0.2.md`: the write-stamp departure removed for #1372 — `generated.at` and `verified[].at` are now UTC instants in the spec's example form, written as strings; the `generated`/`verified` claims gain their pins and the open offset question closes.
- `okf-v0.2.md`: the staleness departure rewritten for #1373 — an instant with an offset is now compared as one, the bare date stays as a recorded lenience, an offset-less datetime is ignored; the August `stale_after` claim gains its pin.
- Every page's `generated.at` and `verified[].at` rewritten from a bare date to the author instant of the commit that did the research or the refute (Codex on #1374: the OKF page documented the datetime rule and stamped itself with a date, as the template-owned skill's own template prescribes). `stale_after` stays a calendar date because `scripts/check_references.py` requires one; both raised upstream as fastmcp-server-template#603; the plugin's stale digest as claude-plugins#47. `okf-v0.2.md` gains the reserved-frontmatter departure (`ReservedFrontmatterPolicy`, #1174/#1175) and the bundle's own `stale_after` shape.

## 2026-09-06

- `okf-v0.2.md` created from `okf/SPEC.md` at commit 62432a09 and the July 2026 text it amended (Codex on #1371: #1357 changed runtime semantics on a digest of the spec with no reference page behind it). Records that the spec's timestamp contract moved in place on 2026-08-21 (`stale_after` is an instant, every timestamp carries an offset) while the header stayed 0.2; departures on staleness and write stamps recorded and filed as #1373 and #1372; the root `index.md` trimmed to `okf_version` only, which §8 requires and the plugin validator enforced.
- `commonmark-gfm.md`: the `_RE_INLINE_LINK` and reference-link departure entries re-observed after #1334 (per-region matching, LF normalisation, no line ending in a destination); the decision table's "Now" column filled in from `_paragraph_regions`, with "dep." for the two deliberate departures and "no, by choice" for the rows left unhonoured; pointer to the design-doc rationale added.
- `obsidian-markdown.md`: the `.md`-append and embed entries updated for #1333 (attachment references are not links; note embeds still are); the departure recorded as a deliberate notes-only carve-out pointing at #1359; test pins added; the stale "without decoding" departure note corrected to the #1332 behaviour.
- Bundle created: `obsidian-markdown.md`, `commonmark-gfm.md`, `git-cli.md` migrated from the first reference contract to OKF v0.2 (`generated`, `verified`, `stale_after`, `sources[].resource`); the refute pass on each page recorded as a `process:` verification.
- `git-cli.md` (37 KB, one page for every module in `git/`) split by consumer module into `git-push-and-remotes.md`, `git-staging-and-commits.md` and `git-history-queries.md`; claims and markers moved verbatim, each page declaring only the sources its body cites; the original deleted.
