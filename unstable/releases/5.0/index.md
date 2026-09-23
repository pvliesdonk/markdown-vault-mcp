# 5.0

This major release moves the server to FastMCP 4 and makes a fresh Compose deployment work without a reverse proxy. Logging settings change too: logs are structured and default to JSON off a terminal. Writable servers reject blind whole-file replacement by default, while Python consumers move configuration into `VaultSettings` and remove two deprecated Git identity arguments. Links the server writes stay valid in Obsidian, the link graph holds more of the links a reader follows, and large vaults no longer pay a full index scan for every changed note. Notes deleted or renamed in a pulled commit no longer linger in listings when the reindex after the pull fails. OKF no longer treats a static bearer credential as proof that a person acted. Operators and library consumers need to follow the migration steps in [Upgrading](#upgrading).

## FastMCP 4 and a standalone container deployment

The server moves from FastMCP 3.4.7 to 4.0.5, through `fastmcp-pvl-core` 9.0 and copier template 9.0.1 ([#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465), [#1550](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1550), [#1570](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1570)). Library environments that pin FastMCP 3 must move to the FastMCP 4 dependency line. The application handles the protocol changes: background tasks register with each server instance, and `okf_verify` uses an input-required response on modern sessions while retaining server-initiated confirmation for older clients ([#1271](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1271), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

Compose no longer assumes Traefik or an existing proxy network. It pulls the published image, exposes `8000:8000`, and starts the server with its state and vault volumes. Before starting it, set `MARKDOWN_VAULT_MCP_SOURCE_DIR` in `.env` to the host directory containing the vault. An existing proxy deployment should move its labels and shared network into `compose.override.yml`; remove the published port there with `ports: !reset []`. The override syntax needs Compose 2.24.4 or newer. Keep `MARKDOWN_VAULT_MCP_BASE_URL` set to the public URL when a proxy or OIDC is in use. The [Docker guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deployment/docker/#behind-a-reverse-proxy) has the full overlay and explains how to select a locally built image ([template#577](https://github.com/pvliesdonk/fastmcp-server-template/pull/577), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

HTTP deployments also get unauthenticated health routes. `/health` returns 200 while the process serves and is the image and Compose liveness probe. `/health/ready` tests the shared key-value store and returns 503 when that check fails; use it for load-balancer readiness. It does not report index freshness or task-backend availability. A custom MCP mount such as `/x/mcp` moves the routes to `/x/health` and `/x/health/ready`, so the shipped probe must move with it. `MARKDOWN_VAULT_MCP_HEALTH_DETAIL` defaults to `standard`; `status` returns less detail, while `full` includes redacted failure reasons and belongs on a trusted network ([fastmcp-pvl-core#318](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/318), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

`MARKDOWN_VAULT_MCP_SHUTDOWN_GRACE_S` (default `3`) now sets how long SIGTERM may spend draining in-flight HTTP requests. The default matches the previous fixed value ([#1550](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1550)).

## Structured logs, JSON off a terminal

Logging is now owned by `fastmcp-pvl-core`, and one chain renders this server's lines, FastMCP's, and uvicorn's ([#1550](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1550), [fastmcp-pvl-core#332](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/332)). Two settings replace the old ones:

- `MARKDOWN_VAULT_MCP_LOG_LEVEL` (default `INFO`) replaces `FASTMCP_LOG_LEVEL`. The old name still works for this major version and logs `log_level_env_deprecated` at startup.
- `MARKDOWN_VAULT_MCP_LOG_FORMAT` picks `rich` or `json`. Unset, the server writes Rich output when stderr is a terminal and JSON everywhere else, so `docker logs` and `journalctl` show one JSON object per record with no configuration. Set it to `rich` to read a container or unit log in colour; no `COLUMNS` value is needed.

`FASTMCP_ENABLE_RICH_LOGGING` is removed and has no effect. The image and the packaged systemd unit no longer set it.

Log messages follow an `event_name key=value` form, so a line that read `github: push processed commits_pulled=2 ...` is now `webhook_push_processed kind=github commits_pulled=2 ...`. Startup reports `server_configured` and `auth_mode_resolved` instead of the previous free-text lines. Alerts or queries that match old message text need updating. Access lines are filtered rather than levelled: below `DEBUG` only requests with status 400 or higher appear, and the query string and any credential in the path are redacted ([fastmcp-pvl-core#330](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/330), [fastmcp-pvl-core#331](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/331)). The [logging section](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/#logging) describes both settings.

## Configuration mistakes fail at startup

A configuration error now ends `serve` with a one-line `ERROR: configuration error: ...` message and exit status 1 instead of a traceback ([#1550](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1550)). Under `--transport http` this includes a malformed `MARKDOWN_VAULT_MCP_KV_STORE_URL` or `MARKDOWN_VAULT_MCP_EVENT_STORE_URL` ([#1551](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1551), [#1570](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1570)). An explicit `MARKDOWN_VAULT_MCP_AUTH_MODE=remote` or `oidc-proxy` whose required variables are missing now refuses to start; before, the server started and served unauthenticated. Auto-detected modes are unaffected, and a server with no auth variables still starts without authentication ([fastmcp-pvl-core#316](https://github.com/pvliesdonk/fastmcp-pvl-core/issues/316), [fastmcp-pvl-core#338](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/338)).

Credentials embedded in an operator URL no longer leak. A `user:pass@` part of `MARKDOWN_VAULT_MCP_BASE_URL` is stripped before the URL becomes the MCP Apps content-security origin, and URL parse errors no longer echo passwords into logs ([fastmcp-pvl-core#344](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/344)). If a URL of yours carried credentials, treat them as exposed in any log sink that collected earlier output and rotate them.

## Whole-file replacement requires an etag

The guarded failure mode for an agent is calling `write` where it meant `edit`, so writable server deployments now default `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` to `true` ([#1136](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1136), [#1477](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1477)). A whole-file replacement of an existing note or attachment must carry the `if_match` etag returned by `read`. This also applies to `fetch`. Targeted `edit`, `append`, `delete`, and `rename` calls keep their existing behavior.

Upload links have no `if_match` field. Create them for a new destination, or set `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=false` if the deployment needs blind replacement. A destination that appears after a link was created is left intact and the upload returns HTTP 409. The [write-safety guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/#write-safety) and [upload walkthrough](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/transfer-links/#upload-walkthrough) cover both paths.

Direct Python construction keeps its library defaults: `VaultSettings()` is read-only, and `write_protect_existing` remains false if writes are enabled. The default change applies when server configuration is assembled.

## A smaller Python construction surface

`Vault` no longer accepts the 31 configuration keywords deprecated in 4.2. Move those values to fields with the same names on `VaultSettings` ([#1225](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1225), [#1478](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1478)):

```
from markdown_vault_mcp.vault import Vault, VaultSettings

vault = Vault(
    source_dir=root,
    settings=VaultSettings(read_only=False, index_path=index),
)
```

`source_dir` and the five constructed collaborators remain direct arguments: `embedding_provider`, `summarizer`, `git_strategy`, `on_write`, and `chunk_strategy`. Omitting `settings` still selects `VaultSettings()`.

Both import paths for `to_vault_kwargs` are removed. Code that constructs a vault from `ProjectConfig` should call `to_vault_instances(config)` once, pass those instances to `to_vault_settings`, and then pass settings and collaborators to `Vault`. Use `dataclasses.replace` for settings overrides. The [configuration migration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/api/config/#migrating-from-4x) gives the complete replacement, and the [Vault migration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/api/vault/#migrating-from-4x) lists the constructor rules.

`GitWriteStrategy` also drops its deprecated `commit_name_claim` and `commit_email_claim` arguments. Remove them from direct construction, and pass the retained `git_lfs` and `repo_path` options by keyword. Server operators keep using `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM`; claim extraction now stays at the request boundary. Direct integrations supply a resolved `Principal` with the write, or bind it around a `Vault` write with `markdown_vault_mcp._identity.bound_principal`. See the [Git API migration](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/api/git/#migrating-from-4x) ([#1236](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1236), [#1479](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1479)).

A custom `git_strategy` object passed to `Vault` must now also provide `set_commit_observer`, and its `start()` receives an `on_tick` callback. Both are part of the `Syncer` protocol in `markdown_vault_mcp.git.interfaces`; `GitWriteStrategy` provides both ([#1560](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1560)).

## Attribution that distinguishes credentials from people

In 4.2, a static bearer token could put `human:bearer-anon` or a mapped bearer subject into `generated.by`, and `trust-auth` could accept the same credential for an OKF human-review stamp. A bearer credential identifies a client but does not establish that a person acted. New writes now use the server tool actor for `generated.by`, and `trust-auth` refuses client-ID-only credentials ([#1463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1463), [#1481](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1481)).

For a human review over bearer authentication, set `MARKDOWN_VAULT_MCP_OKF_VERIFY=elicit` and use a client that presents the confirmation to a person. An affirmative reply is recorded as `human:local`. Existing provenance and verification fields are not rewritten, and Git commit identity remains a separate setting. OAuth service tokens that carry a `sub` claim retain their current attribution pending [#1480](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1480). The [OKF review guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/okf/#recording-a-human-review) describes each authentication case.

## Links the server writes stay valid

An operator reported that an agent-written link to a note in a folder with a space in its name failed in Obsidian, and the server produced the same form itself ([#1494](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1494)). Links written by `okf_generate_index`, `okf_convert_links`, and the link updates of `rename` and `move_folder` now percent-encode spaces, so `/Project Notes/x.md` becomes `/Project%20Notes/x.md` ([#1514](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1514)).

Characters that would end a destination or its text early are escaped too. In 4.2 a generated index entry or a rename into `notes/a)b.md` produced a link that resolved to `notes/a`, a different note or none. The destination now reads `/notes/a\)b.md`, and a title such as `Bra]cket` is written as `Bra\]cket` ([#1513](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1513), [#1516](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1516), [#1518](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1518)). A rename that rewrites a link with a space in its fragment repairs it: `[x](old.md#My Heading)` becomes `new.md#My%20Heading`. With OKF enabled, the server instructions ask agents for root-relative links with `%20` for spaces. Links already written with literal spaces are left as they are. The [OKF guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/okf/index.md) describes the generated forms.

After a rename, `updated_links` counts the files whose links were rewritten. In 4.2 it counted every file where a rewrite was attempted, including ones that matched nothing. Such a file now logs `link_rewrite_matched_nothing` ([#1521](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1521), [#1527](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1527)).

## The link graph holds more of what a reader follows

The work above showed that the scanner, which feeds backlinks, outlinks and broken-link reports, missed Markdown links that CommonMark and Obsidian both follow. These now appear in the graph:

- link text with an escaped bracket, such as `[Bra\]cket](x.md)`, in inline links, reference links and their definitions ([#1517](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1517), [#1520](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1520), [#1519](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1519), [#1522](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1522));
- nested brackets such as `[a [b] c](x.md)`, where a `]` now closes the nearest open `[`, and links inside an image's alt text ([#1526](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1526), [#1529](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1529), [#1528](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1528), [#1530](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1530));
- shortcut reference links, a bare `[label]` with a matching `[label]: x.md` definition, which no earlier version extracted ([#1531](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1531), [#1533](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1533)).

One shape leaves the graph: an image reference such as `![alt][r]` is an image, so it no longer counts as a link when its definition points at a note. A file of unbalanced brackets, such as an unfenced log paste, no longer stalls indexing. In one measurement the time for 40,000 `[` characters dropped from "~29 s" to "40 ms" ([#1343](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1343), [#1524](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1524)).

The first start after upgrading rebuilds the index once to apply these rules, and logs `index_provenance_changed key=index_semantics_version` with `action=rebuild`. No action is needed. Wikilinks whose target contains a single `]` are still not recognized ([#1384](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1384)).

## Large vaults no longer scan the whole index per changed note

A user running a 25,380-note vault reported that removing a changed note's search rows scanned the whole full-text table, so "a batch of N changed notes" cost that scan N times. A live incident "measured 1,526 GB read at up to 554 MB/s over 85 minutes" ([#1535](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1535), reported by [@humblesuperintelligence](https://github.com/humblesuperintelligence)). The rows are now found through a mapping table, which existing databases fill once on first open ([#1539](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1539)).

A reindex pass now saves its progress every 30 seconds, so a pass interrupted by a disconnect resumes where it stopped instead of starting over ([#1540](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1540)).

`MARKDOWN_VAULT_MCP_BOOT_REINDEX` (default `true`) controls the reconciliation pass that runs after startup. Set it to `false` on a large vault that restarts often, such as a stdio server launched per client session. Changes made while no server was running then stay invisible until a reindex runs, through the `reindex` tool or `markdown-vault-mcp reindex`. `_meta.index_stale` does not report this, and neither does `get_index_status` ([#1543](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1543), [#1542](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1542)). Only the `true` family of values (`1`, `true`, `yes`, `on`) keeps the pass on; any other value turns it off. See the [indexing settings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/configuration/#indexing).

## Pulled deletions and renames reach the index

In 4.2, the index was refreshed once after each pull that moved the vault's Git HEAD. When that single reindex failed, nothing retried it: later pulls found nothing new to fetch, so `list_documents`, `list_folders` and `get_broken_links` kept serving notes the working tree no longer held until a manual reindex. A deployment hit this after a commit from another clone renamed and deleted several dozen notes ([#1532](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1532)).

The server now records the revision the index last reflected, and reindexes whenever Git HEAD differs from it. The periodic pull checks after every tick, webhook deliveries and `git_sync` after every pull, so a failed reindex is retried on the next check. A commit that reaches the clone some other way is picked up too. The server's own write commits do not trigger another scan. A check that finds the index still building waits for it rather than scanning twice. Each catch-up logs `index_head_reconciled`, and a failed one logs `index_head_reconcile_failed` at ERROR ([#1560](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1560)). The [Git integration guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/guides/git-integration/index.md) describes the periodic and webhook paths.

## Mutations see completed writes

A successful write could previously be followed immediately by `okf_convert_links` reporting its new target as unresolved because the index update was still queued. Link conversion, index generation, rename with `update_links=true`, and folder move now refresh prior completed writes before they read links or folder contents ([#1464](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1464), [#1482](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1482)).

The refresh and any preceding build share a 60-second deadline. A refresh or build failure stops the mutation before it changes files. The boundary includes targets outside the requested folder, but it does not isolate concurrent edits or make every read synchronous. Read tools retain their optional `wait_for_pending_writes` behavior and `_meta.index_stale` signal. The [tool guide](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/tools/#index-freshness-after-writes) describes the boundary.

The same lifecycle now completes an asynchronous build Future only after the scan, completion marker, and readiness state finish. Marker failures propagate through the Future. During later refreshes, healthy notes still receive vector updates when another note fails, and retrying reuses matching stored vectors for unchanged notes ([#1483](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1483), [#1482](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1482)).

## Parallel edits and linked renames

Every successful edit changes the note's etag, so several edits to one note sent together with the same `if_match` failed after the first. The `edit` description itself recommended `if_match` for line-range edits. The `if_match` descriptions of `edit` and `rename` now tell agents when to leave it out:

- edits that carry only `old_text` can go out together without it, because each fails on its own if its text is gone;
- edits that carry line numbers still pass it, one edit per read, because an earlier edit can shift the range;
- renames of several linked notes sent together leave it out, because `update_links` rewrites the linking notes, which changes the etag of each.

Server behaviour is unchanged; only the guidance agents see differs ([#1493](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1493), [#1572](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1572)). The [`edit` reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/tools/#edit) explains the rule.

## Clearer rebase diagnostics

A rebase that stopped with no unresolved conflict paths used to emit the same final DEBUG message as a resolver that had failed through all 50 conflict iterations. The resolver now records `git_pull_rebase_no_unmerged_paths` for the early stop and reserves `git_pull_conflict_resolution_exhausted` for the iteration cap. Recovery behavior, saved conflicts, and log levels are unchanged ([#1382](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1382), [#1488](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1488)).

## Upgrading

Before moving a 4.2 deployment or library integration to this release:

1. If you use Compose, set `MARKDOWN_VAULT_MCP_SOURCE_DIR` in `.env`. Move proxy labels and networks to an override, or accept the new published port. If the MCP mount path changes, update the health check path too.
1. Move Python environments from FastMCP 3 to FastMCP 4.
1. Decide whether server writes should keep allowing blind replacement. The default is protected; set `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=false` only where the old behavior is required.
1. Move `Vault` configuration keywords into `VaultSettings`, replace `to_vault_kwargs`, and remove the two claim arguments from `GitWriteStrategy`.
1. If bearer-authenticated clients record human OKF reviews, use `elicit` with a client that shows the confirmation to a person.
1. Replace `FASTMCP_LOG_LEVEL` with `MARKDOWN_VAULT_MCP_LOG_LEVEL` and remove `FASTMCP_ENABLE_RICH_LOGGING`. Set `MARKDOWN_VAULT_MCP_LOG_FORMAT=rich` where you want coloured output off a terminal, and update alerts or log queries that match old message text.
1. If `MARKDOWN_VAULT_MCP_AUTH_MODE` is set, check that its mode's required variables are set too; the server no longer starts without them.
1. Expect one full index rebuild on the first start. On a large vault that restarts often, consider `MARKDOWN_VAULT_MCP_BOOT_REINDEX=false`.
