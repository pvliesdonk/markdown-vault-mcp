# Docker Deployment

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

The server listens on port 8000 with HTTP transport, published on the host as `8000:8000`. No reverse proxy, TLS terminator, or external network is assumed.

Copying `.env.example` first is the step to keep. Every variable in it arrives commented out, so the server starts on its defaults and the copy changes no behaviour by itself. It is the file you edit next, and `compose.yml` names it.

Apply a later edit with `docker compose up -d`, which recreates the container with the new values. `docker compose restart` does not pick them up: an env file is read when a container is created, so a restarted container keeps the values it was created with and the edit is ignored without any message.

## Docker Compose

`compose.yml` is a working deployment, not an illustration. It is re-rendered on every `copier update`, so fixes and new defaults reach it; edit it inside the sentinel blocks described below and your changes survive.

### Where configuration goes

The split matters, because two files can set the same variable:

- **`.env` holds the server's configuration.** `compose.yml` reads it with `env_file:`. `.env.example` is generated from the server's own config surface and lists every variable with its default and a one-line description, so it is both the checklist and the place to edit. [Configuration](../configuration.md) carries the full reference.
- **`compose.yml`'s `environment:` block holds only what the file itself determines.** Currently that is `FASTMCP_HOME`, which points at the state volume the file mounts. Values here override `.env`, so a knob set in both places takes the value from `compose.yml`, which is rarely what an operator editing `.env` expects.

The `env_file:` entry is marked `required: false`, so a checkout with no `.env` still starts on defaults. That form needs Compose 2.24.0 or newer; on an older engine, either upgrade or replace the entry with plain `env_file: .env` and make sure the file exists.

### Ports

The image pins its own listener: `CMD` passes `--host 0.0.0.0 --port 8000`, so `MARKDOWN_VAULT_MCP_HOST` and `MARKDOWN_VAULT_MCP_PORT` in a `.env` do not move it. To serve on a different host port, change the left-hand side of the mapping (`"9000:8000"`) rather than the server's port.

### Domain content and `copier update`

Four sentinel blocks mark the parts of `compose.yml` a project owns. Content inside them survives a template update; content outside them does not, and will conflict.

| Block | For |
|-------|-----|
| `DOMAIN-COMPOSE-VOLUMES` | Extra mounts on the service |
| `DOMAIN-COMPOSE-ENVIRONMENT` | Extra environment this file determines |
| `DOMAIN-COMPOSE-SERVICES` | Sidecars, such as a task backend or a cache |
| `DOMAIN-COMPOSE-VOLUME-NAMES` | Top-level declarations for any named volume added above |

A named volume needs an entry in two of those: the mount in `DOMAIN-COMPOSE-VOLUMES`, and its declaration in `DOMAIN-COMPOSE-VOLUME-NAMES`. A bind mount needs only the first.

### Behind a reverse proxy

Proxy configuration is deployment-specific, so `compose.yml` ships none. Add it in a second file rather than by editing `compose.yml`, which is template-owned and re-rendered: save this as `compose.override.yml`, which Compose loads automatically alongside `compose.yml`.

```yaml
services:
  markdown-vault-mcp:
    # `!reset` drops the published port: the proxy reaches the container over
    # the shared network, so nothing needs to be on the host. Plain merging
    # appends to sequences, so without this the port stays published.
    ports: !reset []
    networks:
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"

networks:
  traefik:
    external: true
```

`!reset` needs Compose 2.24.4 or newer. On an older engine, drop that line and remove the port mapping from `compose.yml` directly, accepting that the edit conflicts on the next template update.

Check the result before starting anything, since a merge that silently kept the port mapping looks identical until the port clashes:

```bash
docker compose config
```

Substitute your own hostname for `mcp.example.com`. Set `MARKDOWN_VAULT_MCP_BASE_URL` to the public URL as well: the server needs it to advertise its own address, and it is required once OIDC is enabled. Do not reach for `MARKDOWN_VAULT_MCP_HOST` here. That variable is the interface the server binds to, which is not the name the proxy routes.

The network must already exist and be the one the proxy watches. For the same overlay with OIDC, see [OIDC](oidc.md).

### Building the image yourself

`compose.yml` pulls a published image rather than building one, so `docker compose up -d` never rebuilds from a stale checkout. To run your own build, build and tag it first:

```bash
docker build -t ghcr.io/pvliesdonk/markdown-vault-mcp:dev .
```

then point the `image:` line at that tag.

### Health

The server serves two unauthenticated routes for an orchestrator to probe. They sit outside the MCP mount and outside auth, so they answer normally while the MCP endpoint still answers `401`.

| Route | Question | Answer |
|-------|----------|--------|
| `/health` | Is the process serving? | Static `200` for as long as it does. A failure means restart it. |
| `/health/ready` | Can it do its job? | Runs every readiness check and answers `503` if any fails. A failure means take it out of rotation. |

`compose.yml` probes `/health`, so `docker compose ps` reports `healthy` once the server answers and `docker compose up --wait` returns. The image carries the same probe as its `HEALTHCHECK`, so a bare `docker run` reports health too. Compose only reports the verdict: it gates `depends_on: condition: service_healthy` on it but restarts nothing. An orchestrator that does act on liveness, such as Swarm or a Kubernetes `livenessProbe`, restarts the container, so the probe is deliberately not `/health/ready`. A restart does not fix an unreachable backing store, and a readiness verdict here would hold up every dependent service for one. Point a load balancer or a Kubernetes `readinessProbe` at `/health/ready` instead.

Readiness ships with one check, `kv_store`, which writes a short-lived key so a state volume that has silently vanished is detected. A project adds its own checks, such as whether an upstream API key is still valid, in the `health_checks` dict beside the `DOMAIN-WIRING` block in `src/markdown_vault_mcp/server.py`.

The paths follow the mount. The server strips a conventional trailing `mcp` segment from `MARKDOWN_VAULT_MCP_HTTP_PATH`, so the default `/mcp` publishes `/health` and a mount at `/markdown-vault-mcp/mcp` publishes `/markdown-vault-mcp/health`. The shipped probe assumes the default, so a `.env` that changes the mount path must move the probe with it.

Check the readiness verdict through the published port; `curl` prints the body on a `503` as well, which is where the failing check is named:

```bash
curl -s http://localhost:8000/health/ready
```

`MARKDOWN_VAULT_MCP_HEALTH_DETAIL` decides how much the bodies say, because anyone who can reach the port can read them. `status` returns the status alone. The default, `standard`, adds the server name and version on `/health` and a verdict per check on `/health/ready`. `full` adds the exception type and a redacted reason for each check that raised, and belongs only where the port is reachable from a trusted network.

### Logs

The image sets `FASTMCP_ENABLE_RICH_LOGGING=false`, so `docker logs` gets one line per record: a JSON object for every MCP request the logging middleware sees, `LEVEL: message` from the rest of FastMCP. Both grep cleanly and both survive a log collector. The server's own loggers print one line either way.

The reason is that a container has no terminal. Rich falls back to 80 columns, its time, level and source columns claim most of them, and a structured record then wraps across three space-padded lines that no reader and no parser puts back together. Rich's time column goes with the setting, and Docker timestamps every line it captures anyway, so `docker logs -t` prints them.

This is an image default like any other, so `.env` or the compose `environment:` block overrides it. Turning Rich back on for a human reading `docker logs` needs `COLUMNS` set as well, since that is what Rich reads in place of asking a terminal it does not have. In `.env`:

```ini
FASTMCP_ENABLE_RICH_LOGGING=true
COLUMNS=200
```

Records then render one line each, in color, padded out to the full width. The packaged Debian and RPM installs make the same trade for `journalctl`: the systemd unit sets `FASTMCP_ENABLE_RICH_LOGGING=false`, and `/etc/markdown-vault-mcp/env` overrides it.

`FASTMCP_LOG_LEVEL` sets how much is logged; see [Configuration](../configuration.md#logging).

## Image tags

| Tag | Contents | Updated by |
|-----|----------|------------|
| `latest` | Newest stable release | Each stable release that is newest across all series |
| `vX.Y.Z` | That exact release (pre-releases included, as `vX.Y.Z-rc.N`) | Never (immutable) |
| `vX.Y`, `vX` | Newest stable release in that series | Each stable release that is newest in its series |
| `rc` | Newest release candidate | Each pre-release still ahead of `latest` |
| `edge` | Newest commit on `main` | Every merge to `main` |

Rolling tags are ordering-aware: a patch release cut from an old `release/X.Y` branch after a newer stable has shipped updates its own series tags but never `latest`. The same rule governs `rc`: a candidate only moves the tag while its version is still ahead of the newest stable, so a candidate for an already-released version never pulls `rc` behind `latest`.

The three rolling tags answer different questions. Use `latest` to run released code, `rc` to test the candidate for the next release, and `edge` to run the newest merged commit. Note that `rc` is not cleared when its release ships: it keeps pointing at the last candidate until the next one is cut, so `latest` is the tag to follow in production. To find the commit behind an `edge` image, read its `org.opencontainers.image.revision` label:

```bash
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
  ghcr.io/pvliesdonk/markdown-vault-mcp:edge
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | n/a | Enable bearer token auth |
| `FASTMCP_LOG_LEVEL` | `INFO` | Log level (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `FASTMCP_ENABLE_RICH_LOGGING` | `false` in the image | Rich output; off means one plain or JSON line per record (see [Logs](#logs)) |
| `MARKDOWN_VAULT_MCP_INSTANCE_DESCRIPTION` | n/a | Routing context that distinguishes this deployment |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA` | n/a | Deployment-specific behavioral policy added to the generated MCP instructions |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | (computed at startup) | Legacy full replacement of the generated instructions (deprecated) |
| `MARKDOWN_VAULT_MCP_DEBUG_PORT` | n/a | Remote-debugger TCP port (see [Remote debugging](#remote-debugging); requires `--build-arg DEBUG=true` image) |
| `MARKDOWN_VAULT_MCP_DEBUG_WAIT` | `false` | Block startup until IDE attaches (see [Remote debugging](#remote-debugging)) |

For OIDC auth variables, see [Authentication](../guides/authentication.md).

Running behind a reverse proxy on a path prefix (`https://mcp.example.com/myservice/mcp`) rather than its own hostname needs two routing rules, one of which sits outside the prefix: see [Subpath Deployments](oidc.md#subpath-deployments).

## Volumes

| Path | Purpose |
|------|---------|
| `/data/service` | Your service data (bind-mount or named volume) |
| `/data/state` | State files (FastMCP OIDC state, etc.) |

## UID/GID

Set `PUID` and `PGID` in your `.env` file to match the owner of bind-mounted
directories (default 1000/1000).

## Remote debugging

Production images ship without `debugpy` to keep the image lean. To attach a remote Python debugger from VS Code or PyCharm:

1. **Build with the debug extra:**

    ```bash
    docker build --build-arg DEBUG=true -t markdown-vault-mcp:debug .
    ```

    This installs the `[debug]` optional-dependency group (which pulls `debugpy` transitively from `fastmcp-pvl-core`). Default builds (`DEBUG=false`) skip it.

2. **Run with the debug env vars set and the port mapped:**

    ```bash
    docker run --rm \
      -e MARKDOWN_VAULT_MCP_DEBUG_PORT=5678 \
      -e MARKDOWN_VAULT_MCP_DEBUG_WAIT=true \
      -p 127.0.0.1:5678:5678 \
      -p 8000:8000 \
      markdown-vault-mcp:debug
    ```

    | Env var | Effect |
    |---------|--------|
    | `MARKDOWN_VAULT_MCP_DEBUG_PORT` | TCP port the debugger listens on (any value parsing to ``0`` disables; non-numeric or out-of-range values log a WARNING and the listener stays off) |
    | `MARKDOWN_VAULT_MCP_DEBUG_WAIT` | When truthy (``1``/``true``/``yes``/``on``), block startup until the IDE attaches. Default is non-blocking. |

3. **Attach from VS Code**, adding a launch config:

    ```json
    {
      "name": "Attach to markdown-vault-mcp",
      "type": "debugpy",
      "request": "attach",
      "connect": { "host": "localhost", "port": 5678 }
    }
    ```

    PyCharm uses *Run → Edit Configurations → Python Debug Server* with the same host/port.

!!! danger "Never publish the debug port on a public network"
    The debug listener binds `0.0.0.0` inside the container so the IDE can reach it from the host, but **debugpy's DAP protocol is unauthenticated**: any peer that can reach the port has arbitrary code execution as the server process. Always bind the port mapping to localhost (`-p 127.0.0.1:5678:5678`) or tunnel via `kubectl port-forward` / SSH. Production images should be built with default `DEBUG=false`.

When the helper is invoked but `debugpy` isn't installed (say, someone sets `DEBUG_PORT` on a non-debug image), it logs a WARNING and continues; this is the safe failure mode.


<!-- DOMAIN-DOCKER-EXTRA-START -->
<!-- Project-specific notes for Docker deployment go here; kept across copier
     update. (E.g. "the /data/uploads volume must be writable by UID Y",
     "container needs cap_add: SYS_PTRACE for debugging tools".) -->
<!-- DOMAIN-DOCKER-EXTRA-END -->
