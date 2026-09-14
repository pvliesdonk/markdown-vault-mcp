# 5.0

This major release moves the server to FastMCP 4 and makes a fresh Compose deployment work without a reverse proxy. Writable servers reject blind whole-file replacement by default, while Python consumers move configuration into `VaultSettings` and remove two deprecated Git identity arguments. OKF no longer treats a static bearer credential as proof that a person acted, and mutations that depend on the index catch up with completed writes before they inspect links or folders. Operators and library consumers need to follow the migration steps in [Upgrading](#upgrading).

## FastMCP 4 and a standalone container deployment

The server moves from FastMCP 3.4.7 to 4.0.3, through `fastmcp-pvl-core` 7.1 and copier template 8.2. Library environments that pin FastMCP 3 must move to the FastMCP 4 dependency line. The application handles the protocol changes: background tasks register with each server instance, and `okf_verify` uses an input-required response on modern sessions while retaining server-initiated confirmation for older clients ([#1271](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1271), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

Compose no longer assumes Traefik or an existing proxy network. It pulls the published image, exposes `8000:8000`, and starts the server with its state and vault volumes. Before starting it, set `MARKDOWN_VAULT_MCP_SOURCE_DIR` in `.env` to the host directory containing the vault. An existing proxy deployment should move its labels and shared network into `compose.override.yml`; remove the published port there with `ports: !reset []`. The override syntax needs Compose 2.24.4 or newer. Keep `MARKDOWN_VAULT_MCP_BASE_URL` set to the public URL when a proxy or OIDC is in use. The [Docker guide](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/deployment/docker/#behind-a-reverse-proxy) has the full overlay and explains how to select a locally built image ([template#577](https://github.com/pvliesdonk/fastmcp-server-template/pull/577), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

HTTP deployments also get unauthenticated health routes. `/health` returns 200 while the process serves and is the image and Compose liveness probe. `/health/ready` tests the shared key-value store and returns 503 when that check fails; use it for load-balancer readiness. It does not report index freshness or task-backend availability. A custom MCP mount such as `/x/mcp` moves the routes to `/x/health` and `/x/health/ready`, so the shipped probe must move with it. `MARKDOWN_VAULT_MCP_HEALTH_DETAIL` defaults to `standard`; `status` returns less detail, while `full` includes redacted failure reasons and belongs on a trusted network ([fastmcp-pvl-core#318](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/318), [#1465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1465)).

Container and packaged systemd installations now disable Rich logging by default, so FastMCP records stay on one line in `docker logs` and `journalctl` instead of wrapping across three lines on non-terminal output. Set `FASTMCP_ENABLE_RICH_LOGGING=true` to restore Rich output; in a container, also set a suitable `COLUMNS` value ([template#608](https://github.com/pvliesdonk/fastmcp-server-template/issues/608), [template#609](https://github.com/pvliesdonk/fastmcp-server-template/pull/609)).

## Whole-file replacement requires an etag

The guarded failure mode for an agent is calling `write` where it meant `edit`, so writable server deployments now default `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` to `true` ([#1136](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1136), [#1477](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1477)). A whole-file replacement of an existing note or attachment must carry the `if_match` etag returned by `read`. This also applies to `fetch`. Targeted `edit`, `append`, `delete`, and `rename` calls keep their existing behavior.

Upload links have no `if_match` field. Create them for a new destination, or set `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=false` if the deployment needs blind replacement. A destination that appears after a link was created is left intact and the upload returns HTTP 409. The [write-safety guide](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/configuration/#write-safety) and [upload walkthrough](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/guides/transfer-links/#upload-walkthrough) cover both paths.

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

Both import paths for `to_vault_kwargs` are removed. Code that constructs a vault from `ProjectConfig` should call `to_vault_instances(config)` once, pass those instances to `to_vault_settings`, and then pass settings and collaborators to `Vault`. Use `dataclasses.replace` for settings overrides. The [configuration migration](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/api/config/#migrating-from-4x) gives the complete replacement, and the [Vault migration](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/api/vault/#migrating-from-4x) lists the constructor rules.

`GitWriteStrategy` also drops its deprecated `commit_name_claim` and `commit_email_claim` arguments. Remove them from direct construction, and pass the retained `git_lfs` and `repo_path` options by keyword. Server operators keep using `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM`; claim extraction now stays at the request boundary. Direct integrations supply a resolved `Principal` with the write, or bind it around a `Vault` write with `markdown_vault_mcp._identity.bound_principal`. See the [Git API migration](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/api/git/#migrating-from-4x) ([#1236](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1236), [#1479](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1479)).

## Attribution that distinguishes credentials from people

In 4.2, a static bearer token could put `human:bearer-anon` or a mapped bearer subject into `generated.by`, and `trust-auth` could accept the same credential for an OKF human-review stamp. A bearer credential identifies a client but does not establish that a person acted. New writes now use the server tool actor for `generated.by`, and `trust-auth` refuses client-ID-only credentials ([#1463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1463), [#1481](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1481)).

For a human review over bearer authentication, set `MARKDOWN_VAULT_MCP_OKF_VERIFY=elicit` and use a client that presents the confirmation to a person. An affirmative reply is recorded as `human:local`. Existing provenance and verification fields are not rewritten, and Git commit identity remains a separate setting. OAuth service tokens that carry a `sub` claim retain their current attribution pending [#1480](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1480). The [OKF review guide](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/guides/okf/#recording-a-human-review) describes each authentication case.

## Mutations see completed writes

A successful write could previously be followed immediately by `okf_convert_links` reporting its new target as unresolved because the index update was still queued. Link conversion, index generation, rename with `update_links=true`, and folder move now refresh prior completed writes before they read links or folder contents ([#1464](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1464), [#1482](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1482)).

The refresh and any preceding build share a 60-second deadline. A refresh or build failure stops the mutation before it changes files. The boundary includes targets outside the requested folder, but it does not isolate concurrent edits or make every read synchronous. Read tools retain their optional `wait_for_pending_writes` behavior and `_meta.index_stale` signal. The [tool guide](https://pvliesdonk.github.io/markdown-vault-mcp/5.0/tools/#index-freshness-after-writes) describes the boundary.

The same lifecycle now completes an asynchronous build Future only after the scan, completion marker, and readiness state finish. Marker failures propagate through the Future. During later refreshes, healthy notes still receive vector updates when another note fails, and retrying reuses matching stored vectors for unchanged notes ([#1483](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1483), [#1482](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1482)).

## Clearer rebase diagnostics

A rebase that stopped with no unresolved conflict paths used to emit the same final DEBUG message as a resolver that had failed through all 50 conflict iterations. The resolver now records `git_pull_rebase_no_unmerged_paths` for the early stop and reserves `git_pull_conflict_resolution_exhausted` for the iteration cap. Recovery behavior, saved conflicts, and log levels are unchanged ([#1382](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1382), [#1488](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1488)).

## Upgrading

Before moving a 4.2 deployment or library integration to this release:

1. If you use Compose, set `MARKDOWN_VAULT_MCP_SOURCE_DIR` in `.env`. Move proxy labels and networks to an override, or accept the new published port. If the MCP mount path changes, update the health check path too.
1. Move Python environments from FastMCP 3 to FastMCP 4.
1. Decide whether server writes should keep allowing blind replacement. The default is protected; set `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=false` only where the old behavior is required.
1. Move `Vault` configuration keywords into `VaultSettings`, replace `to_vault_kwargs`, and remove the two claim arguments from `GitWriteStrategy`.
1. If bearer-authenticated clients record human OKF reviews, use `elicit` with a client that shows the confirmation to a person.
