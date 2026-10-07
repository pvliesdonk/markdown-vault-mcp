# Upgrade

An upgrade is a new version of the same server. Three questions come with every one: the step to run; what a connected client has to do; what happens to the state. This page answers them for any release and says where a release page answers them for that release: its Upgrading section. The [release notes](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/releases/index.md) tell each release's own story.

## The step for your channel

One step per channel; the linked page owns the detail.

| You run                         | The step                                                                                                                                                                                                                                                             |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The command, installed with uv  | `uv tool upgrade markdown-vault-mcp`; pin one release with `uv tool install "markdown-vault-mcp==X.Y.Z"` ([Installation](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/get-started/installation/#as-a-command-on-your-machine))                                |
| The command, installed with pip | `pip install --upgrade "markdown-vault-mcp"` ([Installation](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/get-started/installation/#as-a-command-on-your-machine))                                                                                            |
| Claude Desktop                  | with the bundle: install the new release's `.mcpb` the same way as the first; with the command: upgrade it as above, then quit and reopen Claude Desktop ([Claude Desktop](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/get-started/claude-desktop/index.md)) |
| Docker Compose                  | `docker compose pull` then `docker compose up -d`; `restart` keeps the old image ([Upgrading the image](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/docker/#upgrading-the-image))                                                                     |
| The Linux package               | install the new `.deb` or `.rpm` the same way as the first; `postinstall` installs the new version into the virtual environment and restarts a running service ([systemd](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/systemd/#upgrade))              |
| The Claude Code plugin          | `/plugin`, the plugin under **Installed**, **Update now**; from a shell, `claude plugin update markdown-vault-mcp@pvliesdonk` ([Claude Code](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/get-started/claude-code/index.md))                                  |

## Release channels

Artifacts ship on three channels. Each row lists what that channel publishes.

| Channel          | Version identity                                                 | Artifacts                                                                                                                                                                                                                                                                 |
| ---------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `edge` (rolling) | None; the commit is the identity                                 | Docker image `:edge`, rebuilt on every merge to `main`; the `.mcpb` bundle and the Claude Code plugin `.zip` as workflow artifacts; the rolling `unstable` docs version. No git tag, GitHub release or PyPI entry.                                                        |
| Pre-release      | `vX.Y.Z-rc.N`, computed and reviewed in its release pull request | PyPI (as `X.Y.ZrcN`); a GitHub release with wheels, `sdist`, `.deb`/`.rpm` packages, the `.mcpb` bundle, the plugin `.zip` and the SBOM; the Docker image under `vX.Y.Z-rc.N` plus the rolling `rc` tag. Not the plugin marketplace, the MCP registry or the docs deploy. |
| Stable           | `vX.Y.Z`                                                         | Everything: PyPI, Docker (the version tag plus `latest`, `vX` and `vX.Y`), `.deb`/`.rpm`, the GitHub release assets, the plugin marketplace and MCP registry entries when the release is the newest stable, and versioned docs with the `latest` alias.                   |

A PEP 440 resolver skips pre-releases unless the requirement pins one or you pass `--pre`; ask for a candidate by name with `pip install "markdown-vault-mcp==X.Y.ZrcN"`. The rolling tags are ordering-aware: a patch cut from an older series never moves `latest` back, and a candidate for an already-released version never moves `rc`. The [release process](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/contribute/release-process/index.md) describes the model as maintainers see it.

## Your clients after an upgrade

An MCP client discovers the server's tools, resources and prompts when it connects, so a new release's surface reaches it on the next connection: restart a local server, or reconnect to a remote one. Nothing in the client's configuration changes for an upgrade. A client that caches the server's instructions (Claude.ai does) picks up new instructions when the connector is reconnected.

## Security posture

A release that changes what the server can reach, what it changes or who gets in says so on its **Security posture** line, even when the answer is none. The [security model](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/security-model/index.md) is the page those changes are measured against.

## State that survives

A container or a package install keeps its state across upgrades in the paths the deployment pages name: the Docker [volumes](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/docker/#volumes), or `/var/lib/markdown-vault-mcp` and `/etc/markdown-vault-mcp/env` for a package install. A release that changes a state format says so on its **State** line, with the step to take.

## What a release page tells you

A release page's Upgrading section answers the three questions on three fixed lines (a page written before this format answers them in its Upgrading text instead):

- **Clients:** what a client has to do, or that nothing is needed beyond reconnecting.
- **State:** what is kept and what is rebuilt or moved, with the step when there is one.
- **Security posture:** any change in what the server exposes or trusts, or none.

The steps a release needs sit above those lines, each conditional on what you run, so a step that does not apply to your deployment is skipped by its first words. A patch release adds its own section to the series page.

## What an upgrade of this server touches

An upgrade never rewrites your notes. What it can touch is the state the server derives from them.

- **The text index.** The index records how it was built: the version of the rules that turn notes into index rows, the embedding model, and the indexing settings (title field, searchable and indexed frontmatter fields, chunk size override, attachment extensions). When any of these differs at start, the server rebuilds the index once and logs `index_provenance_changed` naming what changed. A release that changes those rules says so on its **State** line.
- **The embeddings.** A rebuild of the text index keeps them: only a note whose sections came out different is embedded again.
- **Where that state lives.** Under Docker Compose, on the `state-data` volume at `/data/state`. On a package install, wherever the environment file's `MARKDOWN_VAULT_MCP_INDEX_PATH` and `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` point ([systemd](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/systemd/index.md)). A command that a desktop client starts keeps the index in memory unless `MARKDOWN_VAULT_MCP_INDEX_PATH` is set, so it rebuilds at every start anyway. The change-tracking file sits beside the index, or in memory without one, unless `MARKDOWN_VAULT_MCP_STATE_PATH` names a path. Earlier releases defaulted it to a `.markdown_vault_mcp/` folder inside the vault. On the first start after the upgrade the server copies that file beside the index, so nothing is indexed or embedded again, and logs `legacy_state_file_unused` while the folder is there; delete it once the new file exists.
