---
description: "Run Markdown Vault MCP as a systemd service from the .deb or .rpm package, from the environment file to upgrades."
kind: how-to
---

# systemd and the Linux packages

The `.deb` and `.rpm` on the [releases page](https://github.com/pvliesdonk/markdown-vault-mcp/releases) install the server as a systemd service. [Installation](../get-started/installation.md#linux-packages) has the install commands; this page is what comes after. The unit confines the process, which is defence in depth: authentication stays the boundary, and the [security model](../security-model.md) says what an authenticated caller then reaches.

## What the package installed

| Path | What it is | Owner |
|---|---|---|
| `/opt/markdown-vault-mcp/venv` | The server, installed from PyPI at the package's own version (a release candidate installs the wheel attached to its GitHub release) | root |
| `/usr/lib/systemd/system/markdown-vault-mcp.service` | The unit | root |
| `/etc/markdown-vault-mcp/env.example` | Every variable, commented out, generated from the server's configuration surface; replaced by an upgrade | root |
| `/etc/markdown-vault-mcp/env` | Your configuration, copied from `env.example` on first install, readable by root only (`600`), never touched by an upgrade | root |
| `/var/lib/markdown-vault-mcp` | The state directory, the one path the service may write | `markdown-vault-mcp:markdown-vault-mcp` |

The package also creates the `markdown-vault-mcp` system user and group the service runs as, with `/var/lib/markdown-vault-mcp` as home and no login shell.

## Configure

Edit `/etc/markdown-vault-mcp/env`. It holds deviations from the defaults only: every line arrives commented out, and the server starts on its defaults when nothing is set. Remove the `#` from the lines you change. The unit reads the file at start (`EnvironmentFile=`), so an edit needs a restart to take effect. Secrets belong here and nowhere else; the file is already mode `600`.

```ini { .config data-expect="server_name='notes'" }
# /etc/markdown-vault-mcp/env
MARKDOWN_VAULT_MCP_SERVER_NAME=notes
MARKDOWN_VAULT_MCP_LOG_LEVEL=INFO
```

The unit starts `markdown-vault-mcp serve --transport http`, so the server listens on `MARKDOWN_VAULT_MCP_HOST` and `MARKDOWN_VAULT_MCP_PORT`, `127.0.0.1:8000` unless set. To serve other machines, configure [authentication](authentication.md) first and put a [reverse proxy](reverse-proxy.md) in front; the [configuration reference](../reference/configuration.md) lists every variable.

## Start and watch

The package installs the unit but does not enable it; starting on boot is your decision:

```bash
sudo systemctl enable --now markdown-vault-mcp
systemctl status markdown-vault-mcp
journalctl -u markdown-vault-mcp -f
sudo systemctl restart markdown-vault-mcp    # after editing the env file
```

Under journald each record is one JSON object: the unit sets no log format, so the server picks its JSON renderer because journald is not a terminal. `MARKDOWN_VAULT_MCP_LOG_FORMAT=rich` in the env file switches to the coloured renderer. `Restart=on-failure` brings a crashed server back after five seconds.

## What the unit allows

The unit runs the server as the `markdown-vault-mcp` user with these directives, among others:

| Directive | Effect |
|---|---|
| `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes` | The filesystem is read-only except the paths listed below; `/home`, `/root` and `/run/user` are not visible; a private `/tmp` |
| `StateDirectory=markdown-vault-mcp`, `ReadWritePaths=/var/lib/markdown-vault-mcp` | The one writable path, created and owned for the service |
| `NoNewPrivileges=yes`, an empty `CapabilityBoundingSet=` | No privilege escalation and no capabilities |
| `PrivateDevices=yes`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` | No devices; only Unix, IPv4 and IPv6 sockets, so outbound connections to other services work |
| `SystemCallFilter=@system-service` | Only the system calls an ordinary service needs |
| `MemoryDenyWriteExecute=no` | Left open because Python needs writable, executable memory |
| `UMask=0027` | New files are group-readable, so an administrator in the `markdown-vault-mcp` group can inspect the state directory |

To let the server read or write a path outside `/var/lib/markdown-vault-mcp`, add a drop-in rather than editing the unit, which the next package upgrade replaces:

```bash
sudo systemctl edit markdown-vault-mcp
```

```ini
[Service]
ReadWritePaths=/srv/markdown-vault-mcp
```

`systemctl edit` reloads the unit definitions; restart the service afterwards. The other directives stay as they are. On a host with SELinux enforcing, the directory also needs a label the service may use (`semanage fcontext`, then `restorecon`).

## Upgrade

Install the new package the same way as the first one. Its `postinstall` installs the new version into the existing virtual environment and restarts the service when it is running. `/etc/markdown-vault-mcp/env` is left alone; `env.example` is replaced, so compare the two for variables the release added:

```bash
diff /etc/markdown-vault-mcp/env.example /etc/markdown-vault-mcp/env
```

What a release changes for your clients and your data is on the [Upgrade](../upgrade/index.md) page.

## Without a package

On a distribution the packages do not cover, mirror what the package does. The unit and the environment template live in the repository's `packaging/` directory at every release tag:

```bash
sudo groupadd --system markdown-vault-mcp
sudo useradd --system --gid markdown-vault-mcp --no-create-home \
  --home-dir /var/lib/markdown-vault-mcp --shell /usr/sbin/nologin markdown-vault-mcp
sudo mkdir -p /opt/markdown-vault-mcp /etc/markdown-vault-mcp /var/lib/markdown-vault-mcp
sudo python3 -m venv /opt/markdown-vault-mcp/venv
sudo /opt/markdown-vault-mcp/venv/bin/pip install "markdown-vault-mcp==X.Y.Z"
sudo curl -fsSL -o /usr/lib/systemd/system/markdown-vault-mcp.service \
  https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/vX.Y.Z/packaging/markdown-vault-mcp.service
sudo curl -fsSL -o /etc/markdown-vault-mcp/env.example \
  https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/vX.Y.Z/packaging/env.example
sudo cp /etc/markdown-vault-mcp/env.example /etc/markdown-vault-mcp/env
sudo chmod 600 /etc/markdown-vault-mcp/env
sudo chown markdown-vault-mcp:markdown-vault-mcp /var/lib/markdown-vault-mcp
sudo systemctl daemon-reload
```

Then configure and start as above. Python 3.11 or newer with the `venv` module is the one system requirement.

## If it does not start

- `journalctl -u markdown-vault-mcp -n 50 --no-pager` shows the last records; a configuration error names the variable.
- Run the command as the service user to see the error directly: `sudo -u markdown-vault-mcp /opt/markdown-vault-mcp/venv/bin/markdown-vault-mcp serve --transport http`.
- A permission error on a path outside `/var/lib/markdown-vault-mcp` is the confinement above: add the drop-in, or move the data.
- `systemd-analyze verify /usr/lib/systemd/system/markdown-vault-mcp.service` checks the unit after an edit.

<!-- DOMAIN-SYSTEMD-EXTRA-START -->
### Where this server keeps its files

The package installs `markdown-vault-mcp[all]`, so search by meaning and the file watcher work without further installs. On a manual install, install `"markdown-vault-mcp[all]==X.Y.Z"` for the same.

The environment file leaves `MARKDOWN_VAULT_MCP_SOURCE_DIR` commented out. Its default, `/data/vault`, is outside what the unit may write, so set it, and put this server's other files under the state directory too:

```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/var/lib/markdown-vault-mcp/vault
MARKDOWN_VAULT_MCP_INDEX_PATH=/var/lib/markdown-vault-mcp/index.db
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/var/lib/markdown-vault-mcp/embeddings/embeddings
MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR=/var/lib/markdown-vault-mcp/fastembed
```

- **The vault must be writable, even read-only.** The server keeps a change-tracking file in the vault's `.markdown_vault_mcp/` folder unless `MARKDOWN_VAULT_MCP_STATE_PATH` points elsewhere. A vault outside the state directory needs its own `ReadWritePaths=` drop-in, as above. The unit hides `/home` (`ProtectHome=yes`), so a vault there is out of reach.
- **Without `INDEX_PATH`** the index lives in memory and is rebuilt at every start.
- **Without `FASTEMBED_CACHE_DIR`** FastEmbed keeps its model in the temporary directory. The unit's `PrivateTmp=yes` discards that at every stop, so the model downloads again at each start.

For a vault that commits and pushes through the server, an empty `SOURCE_DIR` plus the variables in [Git integration](../guides/git-integration.md#managed-mode-recommended-for-containerized-deployments) is enough: the server clones at the first start.
<!-- DOMAIN-SYSTEMD-EXTRA-END -->
