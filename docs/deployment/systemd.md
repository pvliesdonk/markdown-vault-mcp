# systemd Deployment

Run markdown-vault-mcp as a native Linux system service with systemd.

## Prerequisites

- Python 3.11+ with `python3-venv` (Debian/Ubuntu) or `python3` (Fedora/RHEL)
- systemd (present on all modern Linux distributions)
- Root access for service installation

## Installation

### Option A: Package install (.deb / .rpm)

Download the latest `.deb` or `.rpm` from the
[GitHub Releases](https://github.com/pvliesdonk/markdown-vault-mcp/releases) page.

=== "Debian / Ubuntu"

    ```bash
    sudo dpkg -i markdown-vault-mcp_*.deb
    sudo apt-get install -f  # resolve dependencies if needed
    ```

=== "Fedora / RHEL"

    ```bash
    sudo rpm -i markdown-vault-mcp-*.rpm
    ```

The package automatically:

- Creates a `markdown-vault-mcp` system user and group
- Installs a Python venv at `/opt/markdown-vault-mcp/venv/`
- Places the systemd unit file at `/usr/lib/systemd/system/markdown-vault-mcp.service`
- Creates the state directory at `/var/lib/markdown-vault-mcp/`

### Option B: Manual install (pip / uv)

```bash
# 1. Create system user
sudo useradd --system --no-create-home \
    --home-dir /var/lib/markdown-vault-mcp \
    --shell /usr/sbin/nologin \
    --comment "Markdown Vault MCP Server" \
    markdown-vault-mcp

# 2. Create directories
sudo mkdir -p /opt/markdown-vault-mcp /var/lib/markdown-vault-mcp /etc/markdown-vault-mcp

# 3. Create venv and install
sudo python3 -m venv /opt/markdown-vault-mcp/venv
sudo /opt/markdown-vault-mcp/venv/bin/pip install "markdown-vault-mcp[all]"

# 4. Download and install systemd unit file and env template
#    (These files are in the GitHub repo under packaging/)
sudo curl -fsSL https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/packaging/markdown-vault-mcp.service \
    -o /usr/lib/systemd/system/markdown-vault-mcp.service
sudo curl -fsSL https://raw.githubusercontent.com/pvliesdonk/markdown-vault-mcp/main/packaging/env.example \
    -o /etc/markdown-vault-mcp/env.example

# 5. Create config from template
sudo cp /etc/markdown-vault-mcp/env.example /etc/markdown-vault-mcp/env
sudo chmod 600 /etc/markdown-vault-mcp/env

# 6. Set ownership and reload systemd
sudo chown -R markdown-vault-mcp:markdown-vault-mcp /var/lib/markdown-vault-mcp
sudo systemctl daemon-reload
```

## Configuration

Edit `/etc/markdown-vault-mcp/env` — this is the `EnvironmentFile` loaded by the systemd unit.
The example file at `/etc/markdown-vault-mcp/env.example` documents all available variables.

### Minimum required

```bash
# /etc/markdown-vault-mcp/env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/var/lib/markdown-vault-mcp/vault
MARKDOWN_VAULT_MCP_INDEX_PATH=/var/lib/markdown-vault-mcp/index.db
```

### Typical read-write setup

```bash
# /etc/markdown-vault-mcp/env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/var/lib/markdown-vault-mcp/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_INDEX_PATH=/var/lib/markdown-vault-mcp/index.db
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/var/lib/markdown-vault-mcp/embeddings
MARKDOWN_VAULT_MCP_INDEXED_FIELDS=tags,category
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=fastembed
```

See [Configuration](../configuration.md) for the full list of environment variables.

## Directory Layout

| Path | Purpose | Ownership |
|------|---------|-----------|
| `/opt/markdown-vault-mcp/venv/` | Python virtualenv with the installed package | root |
| `/etc/markdown-vault-mcp/env` | Environment configuration file | root |
| `/etc/markdown-vault-mcp/env.example` | Commented example with all variables | root |
| `/var/lib/markdown-vault-mcp/` | State directory (index, embeddings, vault) | markdown-vault-mcp |
| `/usr/lib/systemd/system/markdown-vault-mcp.service` | systemd unit file | root |

## Vault Location

By default, the service expects the vault inside the state directory at
`/var/lib/markdown-vault-mcp/vault/`. You have several options:

### Symlink (simplest)

```bash
sudo ln -s /home/user/my-vault /var/lib/markdown-vault-mcp/vault
```

### Direct path with ReadWritePaths

Point `SOURCE_DIR` to the vault's actual location and add a `ReadWritePaths`
override so the security-hardened unit file allows access:

```bash
# /etc/markdown-vault-mcp/env
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/my-vault
```

```bash
# Create a systemd override
sudo systemctl edit markdown-vault-mcp
```

Add the following to the override file:

```ini
[Service]
ReadWritePaths=/home/user/my-vault
```

### Git-managed vault

For managed-repo mode (auto-clone + pull + push), set the git variables:

```bash
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/user/vault.git
MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_...
MARKDOWN_VAULT_MCP_READ_ONLY=false
```

!!! warning "Protect your env file"
    The env file contains plaintext tokens and API keys. Ensure it is not
    world-readable: `sudo chmod 600 /etc/markdown-vault-mcp/env`

The service will clone the repo into `SOURCE_DIR` on first start.

## Service Management

```bash
# Enable service to start on boot
sudo systemctl enable markdown-vault-mcp

# Start the service
sudo systemctl start markdown-vault-mcp

# Check status
sudo systemctl status markdown-vault-mcp

# View logs (live)
sudo journalctl -u markdown-vault-mcp -f

# View recent logs
sudo journalctl -u markdown-vault-mcp --since "1 hour ago"

# Restart after config changes
sudo systemctl restart markdown-vault-mcp

# Stop the service
sudo systemctl stop markdown-vault-mcp
```

## Security Hardening

The unit file includes 20+ security directives that restrict the service to
minimum required access. Key directives:

| Directive | Effect |
|-----------|--------|
| `ProtectSystem=strict` | Filesystem is read-only except explicitly listed paths |
| `ProtectHome=yes` | `/home`, `/root`, `/run/user` are inaccessible |
| `NoNewPrivileges=yes` | Cannot gain new privileges via setuid/setgid |
| `PrivateTmp=yes` | Isolated `/tmp` namespace |
| `PrivateDevices=yes` | No access to physical devices |
| `ProtectKernelTunables=yes` | Cannot modify `/proc/sys`, `/sys` |
| `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` | Only Unix, IPv4, IPv6 sockets |
| `SystemCallFilter=@system-service` | Restricted to standard service syscalls |
| `MemoryDenyWriteExecute=no` | Allowed — required for Python/numpy JIT |

### Relaxing restrictions

If your vault is outside `/var/lib/markdown-vault-mcp`, add `ReadWritePaths`
via a systemd override (see [Vault Location](#vault-location) above).

If you need network access to additional services (e.g., Ollama on a remote host),
no changes are needed — outbound TCP connections are allowed by default.

## Troubleshooting

### Permission denied errors

```bash
# Check the service user can access the vault
sudo -u markdown-vault-mcp ls /var/lib/markdown-vault-mcp/vault/

# Check for SELinux denials (RHEL/Fedora)
sudo ausearch -m avc -ts recent
```

If using SELinux, you may need to set the correct context:

```bash
sudo semanage fcontext -a -t var_lib_t "/var/lib/markdown-vault-mcp(/.*)?"
sudo restorecon -Rv /var/lib/markdown-vault-mcp
```

### Python version mismatch

The package requires Python 3.11+. Check the system Python version:

```bash
python3 --version
```

On older distributions, you may need to install a newer Python from a PPA
(Ubuntu) or use `python3.12` explicitly:

```bash
# Ubuntu — add deadsnakes PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.12 python3.12-venv
```

Then recreate the venv with the correct Python:

```bash
sudo rm -rf /opt/markdown-vault-mcp/venv
sudo python3.12 -m venv /opt/markdown-vault-mcp/venv
sudo /opt/markdown-vault-mcp/venv/bin/pip install "markdown-vault-mcp[all]"
```

### Service fails to start

```bash
# Check detailed logs
sudo journalctl -u markdown-vault-mcp -n 50 --no-pager

# Verify the unit file syntax
systemd-analyze verify /usr/lib/systemd/system/markdown-vault-mcp.service

# Test running the command manually as the service user
sudo -u markdown-vault-mcp /opt/markdown-vault-mcp/venv/bin/markdown-vault-mcp serve --transport http
```

### Upgrading

=== "Package upgrade"

    ```bash
    # Debian/Ubuntu
    sudo dpkg -i markdown-vault-mcp_*.deb

    # Fedora/RHEL
    sudo rpm -U markdown-vault-mcp-*.rpm
    ```

    The postinstall script upgrades the venv automatically.

=== "Manual upgrade"

    ```bash
    sudo /opt/markdown-vault-mcp/venv/bin/pip install --upgrade "markdown-vault-mcp[all]"
    sudo systemctl restart markdown-vault-mcp
    ```
